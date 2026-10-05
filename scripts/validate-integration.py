"""Check permissions, workload ownership and rendered ingress/Registry behavior."""
from pathlib import Path
import re
import subprocess
import sys
import yaml

from render import verify_archive
from rendered_inputs import checked_documents

ROOT = Path(__file__).resolve().parents[1]
CLUSTER_KINDS = {'Namespace', 'Node', 'PersistentVolume', 'StorageClass', 'CustomResourceDefinition',
                 'ClusterRole', 'ClusterRoleBinding', 'MutatingWebhookConfiguration',
                 'ValidatingWebhookConfiguration', 'ValidatingAdmissionPolicy',
                 'ValidatingAdmissionPolicyBinding', 'APIService', 'PriorityClass', 'CSIDriver', 'CSINode'}
NAMESPACED_KINDS = {'ServiceAccount', 'Secret', 'ConfigMap', 'Service', 'Endpoints', 'EndpointSlice',
                    'Deployment', 'StatefulSet', 'DaemonSet', 'Pod', 'Job', 'CronJob', 'Role', 'RoleBinding',
                    'NetworkPolicy', 'Ingress', 'PersistentVolumeClaim', 'PodDisruptionBudget',
                    'HorizontalPodAutoscaler', 'ResourceQuota', 'LimitRange'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_permissions(project, documents, default_namespace, scopes):
    destinations = {d['namespace'] for d in project['spec']['destinations']}
    allowed_cluster = {(d['group'], d['kind']) for d in project['spec']['clusterResourceWhitelist']}
    for obj in documents:
        kind = obj['kind']
        group = obj['apiVersion'].split('/')[0] if '/' in obj['apiVersion'] else ''
        scope = scopes.get((group, kind))
        if scope is None:
            scope = 'Cluster' if kind in CLUSTER_KINDS else 'Namespaced' if kind in NAMESPACED_KINDS else None
        require(scope is not None, f'Unknown resource scope: {group}/{kind}; extend the explicit scope map')
        if scope == 'Cluster':
            require((group, kind) in allowed_cluster, f'Project denies cluster resource {group}/{kind}')
        else:
            namespace = obj['metadata'].get('namespace', default_namespace)
            require(namespace in destinations, f'Project denies {namespace}/{kind}/{obj["metadata"]["name"]}')


def selector_matches(selector, labels):
    # Fail closed when chart changes introduce selectors this static check does
    # not yet interpret, rather than silently skipping a policy.
    require(not selector.get('matchExpressions'), 'Review new NetworkPolicy matchExpressions')
    return all(labels.get(k) == v for k, v in selector.get('matchLabels', {}).items())


def check_server_ingress(documents, labels):
    policies = [o for o in documents if o['kind'] == 'NetworkPolicy'
                and o['metadata'].get('namespace') == 'argocd'
                and selector_matches(o['spec']['podSelector'], labels)
                and 'Ingress' in o['spec'].get('policyTypes', ['Ingress'])]
    require(policies, 'Argo CD server has no ingress policy')
    permitted = set()
    for policy in policies:
        for rule in policy['spec'].get('ingress', []):
            require(rule.get('from') and rule.get('ports'), 'Argo CD server ingress allows unrestricted sources/ports')
            for port in rule['ports']:
                require(port.get('protocol', 'TCP') == 'TCP' and not port.get('endPort'), 'Unexpected server port rule')
                number = port.get('port')
                for source in rule['from']:
                    require('ipBlock' not in source, 'Review Argo CD server ipBlock exception')
                    if 'namespaceSelector' in source:
                        ns_selector = source['namespaceSelector']
                        ns = ns_selector.get('matchLabels', {}).get('kubernetes.io/metadata.name')
                        require(not ns_selector.get('matchExpressions'), 'Review server namespace expressions')
                    else:
                        require('podSelector' in source, 'Empty server ingress source allows all namespaces')
                        ns = 'argocd'
                    require((ns, number) in {('argocd', 8080), ('gateway-system', 8080), ('monitoring', 8083)},
                            f'Argo CD server unexpected ingress allowance: {ns}:{number}')
                    if source.get('podSelector', {}) == {}:
                        permitted.add((ns, number))
    require({('gateway-system', 8080), ('monitoring', 8083)} <= permitted, 'Required server ingress path is missing')


def check_registry(documents, enabled):
    flags = []
    for obj in documents:
        if obj['kind'] == 'ConfigMap':
            for value in obj.get('data', {}).values():
                # Embedded Rails configuration contains ERB, so inspect the
                # rendered registry stanza without evaluating that Ruby code.
                flags += re.findall(r'^\s+registry:\s*\n\s+enabled:\s*(true|false)\s*$', value, re.MULTILINE)
    require(len(flags) == 3 and all(flag == str(enabled).lower() for flag in flags),
            f'Rails Registry settings disagree with deployment: {flags}, expected {enabled}')
    deployed = any(o['kind'] == 'Deployment' and o['metadata']['name'] == 'gitlab-registry' for o in documents)
    require(deployed == enabled, 'GitLab Registry Deployment disagrees with Rails setting')


def pod_specs(documents):
    for obj in documents:
        kind = obj['kind']
        if kind == 'Pod':
            yield obj['spec']
        elif kind in {'Deployment', 'StatefulSet', 'DaemonSet', 'Job', 'ReplicaSet', 'ReplicationController'}:
            yield obj['spec']['template']['spec']
        elif kind == 'CronJob':
            yield obj['spec']['jobTemplate']['spec']['template']['spec']


def secret_references(spec):
    """Return mandatory Pod Secret names and any explicitly requested keys."""
    for volume in spec.get('volumes', []):
        if 'secret' in volume:
            secret = volume['secret']
            if not secret.get('optional', False):
                yield secret['secretName'], {item['key'] for item in secret.get('items', [])}
        for source in volume.get('projected', {}).get('sources', []):
            secret = source.get('secret')
            if secret and not secret.get('optional', False):
                yield secret['name'], {item['key'] for item in secret.get('items', [])}
    for container in spec.get('containers', []) + spec.get('initContainers', []):
        for env in container.get('env', []):
            secret = env.get('valueFrom', {}).get('secretKeyRef')
            if secret and not secret.get('optional', False):
                yield secret['name'], {secret['key']}
        for env in container.get('envFrom', []):
            secret = env.get('secretRef')
            if secret and not secret.get('optional', False):
                yield secret['name'], set()
    for secret in spec.get('imagePullSecrets', []):
        yield secret['name'], set()


def check_gitlab_dependencies(documents, contract, registry_enabled=False):
    external = dict(contract['external'])
    if registry_enabled:
        external.update(contract['registryOverlay'])
    generated = set(contract['generatedByChartHooks'])
    rendered = {obj['metadata']['name'] for obj in documents if obj['kind'] == 'Secret'}
    references = list(ref for spec in pod_specs(documents) for ref in secret_references(spec))
    require(references, 'No GitLab Pod Secret references found')
    for name, keys in references:
        require(name in external or name in generated or name in rendered,
                f'GitLab requires undocumented Secret {name}; review provisioning and preflight')
        if name in external:
            require(keys <= set(external[name]), f'GitLab external Secret {name} has undocumented keys {keys}')
    addresses = [address for obj in documents if obj['kind'] == 'ConfigMap'
                 for value in obj.get('data', {}).values()
                 for address in re.findall(r'^\s+gitaly_address:\s+(\S+)\s*$', value, re.MULTILINE)]
    require(len(addresses) == 4 and all(address.startswith('tls://') for address in addresses),
            'GitLab external Gitaly clients must keep TLS enabled')
    require('gitlab-backend-ca' in {name for name, _ in references}, 'GitLab external CA trust is missing')


def main():
    charts = yaml.safe_load((ROOT / 'platform/releases.yaml').read_text())['charts']
    production = checked_documents(ROOT / 'rendered/production', 'name')
    kustomize = checked_documents(ROOT / 'rendered/kustomize', 'path')
    require(set(production) == {c['name'] for c in charts if 'production' in c['values']}, 'Incomplete production rendering')
    output = ROOT / 'rendered/verification/gitops'
    subprocess.run(['python', 'scripts/generate-gitops.py', '--example', '--repo-url',
                    'https://git.example.invalid/platform.git', '--revision', 'a' * 40,
                    '--output', str(output.relative_to(ROOT))], cwd=ROOT, check=True)
    generated = list(yaml.safe_load_all((output / 'infrastructure.yaml').read_text()))
    project = next(o for o in generated if o['kind'] == 'AppProject')
    applications = {o['metadata']['name']: o for o in generated if o['kind'] == 'Application'}
    expected = {c['name'] for c in charts if c['management'] == 'argocd'} | {'platform-networking', 'platform-logging'}
    require(set(applications) == expected, 'Application ownership does not match release management')
    require(not {'argocd', 'gitlab'} & applications.keys(), 'Helm-managed applications must not have a second owner')
    scopes = {}
    for docs in production.values():
        for obj in docs:
            if obj['kind'] == 'CustomResourceDefinition':
                spec = obj['spec']
                scopes[(spec['group'], spec['names']['kind'])] = spec['scope']
    for name, app in applications.items():
        require('automated' not in app['spec']['syncPolicy'], f'{name}: automatic sync must be explicitly reviewed')
        require(not app['metadata'].get('finalizers'), f'{name}: unexpected cascading deletion')
        sources = app['spec'].get('sources', [app['spec'].get('source')])
        require(all(s['repoURL'] in project['spec']['sourceRepos'] for s in sources), f'{name}: repository denied')
        if name in production:
            docs = production[name]
        else:
            docs = kustomize[app['spec']['source']['path']]
        check_permissions(project, docs, app['spec']['destination']['namespace'], scopes)
    server = next(o for o in production['argocd'] if o['kind'] == 'Deployment' and o['metadata']['name'] == 'argocd-server')
    check_server_ingress(production['argocd'] + kustomize['platform/networking/production'],
                         server['spec']['template']['metadata']['labels'])
    grafana = next(o for o in production['monitoring']
                   if o['kind'] == 'Deployment' and o['metadata']['name'] == 'monitoring-grafana')
    env = {e['name']: e for c in grafana['spec']['template']['spec']['containers']
           if c['name'] == 'grafana' for e in c.get('env', [])}
    require(env.get('GF_SECURITY_SECRET_KEY', {}).get('valueFrom', {}).get('secretKeyRef') ==
            {'name': 'grafana-encryption', 'key': 'secret-key'}, 'Grafana requires its persistent encryption Secret')
    check_registry(production['gitlab'], False)
    secret_contract = yaml.safe_load((ROOT / 'platform/gitlab/required-secrets.yaml').read_text())
    check_gitlab_dependencies(production['gitlab'], secret_contract)
    gitlab = next(c for c in charts if c['name'] == 'gitlab')
    archives = list((ROOT / '.cache/charts' / f'gitlab-{gitlab["version"]}').glob('*.tgz'))
    require(len(archives) == 1, 'Expected one GitLab chart archive')
    verify_archive(archives[0], gitlab)
    result = subprocess.run(['helm', 'template', 'gitlab', str(archives[0]), '--namespace', 'gitlab',
                             '--kube-version', yaml.safe_load((ROOT / 'platform/releases.yaml').read_text())['kubernetesVersion'],
                             '--include-crds', '-f', gitlab['values']['production'],
                             '-f', 'platform/gitlab/values-registry.yaml.example'],
                            cwd=ROOT, text=True, capture_output=True, check=True)
    (ROOT / 'rendered/verification/gitlab-registry.yaml').write_text(result.stdout)
    registry_documents = [o for o in yaml.safe_load_all(result.stdout) if o]
    check_registry(registry_documents, True)
    check_gitlab_dependencies(registry_documents, secret_contract, registry_enabled=True)
    print(f'PASS {len(applications)} Applications: ownership and rendered project permissions; '
          'Argo CD server ingress; Grafana encryption Secret; GitLab Registry variants, Secret dependencies and Gitaly TLS')
    print('Static integration checks only; no API admission or network enforcement tested.')


if __name__ == '__main__':
    main()
