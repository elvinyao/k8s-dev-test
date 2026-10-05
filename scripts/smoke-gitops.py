"""Verify real Argo CD synchronization and AppProject denials in a disposable kind cluster."""
from datetime import datetime, timezone
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import uuid

import yaml

from local_cluster import LocalCluster, ROOT

REPOSITORY = 'http://git-fixture.gitops-fixture.svc.cluster.local:8080/config.git'


def source_hashes():
    files = [Path(__file__), ROOT / 'scripts/local_cluster.py', ROOT / 'clusters/local/kind.yaml',
             ROOT / 'platform/releases.yaml', ROOT / 'platform/argocd/values-local.yaml',
             ROOT / 'clusters/production/namespaces.yaml', ROOT / 'platform/security/apps-prod.yaml',
             ROOT / 'argocd-app-settings/project-production.yaml.example',
             ROOT / 'argocd-app-settings/production-demo.yaml.example',
             ROOT / 'apps/production-demo/admin-networkpolicy.yaml.example',
             *sorted(path for path in (ROOT / 'apps/production-demo').glob('*') if path.suffix in {'.yaml', '.html'})]
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in files if path.is_file()}


def current_operation(application, token, revision):
    state = application.get('status', {}).get('operationState', {})
    info = state.get('operation', {}).get('info', [])
    if {'name': 'validation-run', 'value': token} not in info:
        return None
    if state.get('syncResult', {}).get('revision') != revision:
        return None
    return state


def require_project_denial(state, group, kind, name, namespace):
    if state.get('phase') != 'Failed':
        raise RuntimeError('Expected a failed synchronization for the forbidden resource')
    expected_message = f'resource {group}:{kind} is not permitted in project platform-apps-production'
    results = state.get('syncResult', {}).get('resources', [])
    matches = [r for r in results if (r.get('group', ''), r.get('kind'), r.get('name'), r.get('namespace', ''))
               == (group, kind, name, namespace)]
    if len(matches) != 1 or matches[0].get('status') != 'SyncFailed' or expected_message not in matches[0].get('message', ''):
        raise RuntimeError('Synchronization did not prove the specific AppProject resource denial')
    return matches[0]


class GitOpsCheck:
    def __init__(self, cluster):
        self.cluster = cluster
        self.directory = cluster.directory / 'gitops'
        self.directory.mkdir()
        self.sequence = 0
        self.revision = None

    def manifest(self, name, objects):
        path = self.directory / (name + '.yaml')
        path.write_text(yaml.safe_dump_all(objects, sort_keys=False))
        self.cluster.run('apply-' + name, self.cluster.kubectl('apply', '-f', str(path)))

    def git(self, name, *arguments):
        return self.cluster.run('git-' + name, ['git', *arguments]).strip()

    def prepare(self):
        checkout = self.directory / 'checkout'
        checkout.mkdir()
        empty_template = self.directory / 'empty-git-template'
        empty_template.mkdir()
        business = checkout / 'apps/production-demo'
        business.mkdir(parents=True)
        for path in (ROOT / 'apps/production-demo').iterdir():
            if path.suffix in {'.yaml', '.html'}:
                shutil.copyfile(path, business / path.name)
        # This repo contains only public fixture manifests, never host Git history
        # or a copy of the platform checkout's .git directory and credentials.
        forbidden = [
            ('secret', {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': 'forbidden-gitops-secret',
                        'namespace': 'apps-prod'}, 'stringData': {'fixture': 'public-test-value'}}),
            ('clusterrole', {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'ClusterRole',
                             'metadata': {'name': 'forbidden-gitops-clusterrole'}, 'rules': []}),
        ]
        for name, obj in forbidden:
            directory = checkout / 'checks' / name
            directory.mkdir(parents=True)
            (directory / 'resource.yaml').write_text(yaml.safe_dump(obj))
            (directory / 'kustomization.yaml').write_text(yaml.safe_dump({
                'apiVersion': 'kustomize.config.k8s.io/v1beta1', 'kind': 'Kustomization', 'resources': ['resource.yaml']}))
        self.git('init', 'init', '--initial-branch=main', '--template=' + str(empty_template), str(checkout))
        self.git('add', '-C', str(checkout), 'add', '.')
        self.git('commit', '-C', str(checkout), '-c', 'user.name=Platform validation', '-c',
                 'user.email=validation@example.invalid', 'commit', '--no-gpg-sign', '-m', 'Isolated GitOps fixture')
        self.revision = self.git('revision', '-C', str(checkout), 'rev-parse', 'HEAD')
        bare = self.directory / 'config.git'
        self.git('bare', 'clone', '--bare', '--no-hardlinks', '--template=' + str(empty_template), str(checkout), str(bare))
        self.git('server-info', '--git-dir=' + str(bare), 'update-server-info')
        archive = self.directory / 'repository.tgz'
        with tarfile.open(archive, 'w:gz') as tar:
            tar.add(bare, arcname='config.git')
        if archive.stat().st_size > 700 * 1024:
            raise RuntimeError('Fixture repository is too large for a ConfigMap; inspect included files')
        deployment = yaml.safe_load((ROOT / 'apps/production-demo/deployment.yaml').read_text())
        image = deployment['spec']['template']['spec']['containers'][0]['image']
        security = {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}}
        resources = {'requests': {'cpu': '10m', 'memory': '16Mi'}, 'limits': {'cpu': '100m', 'memory': '64Mi'}}
        self.manifest('git-fixture', [
            {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': 'gitops-fixture', 'labels': {
                'pod-security.kubernetes.io/enforce': 'restricted', 'pod-security.kubernetes.io/enforce-version': 'v1.35'}}},
            {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': 'git-fixture', 'namespace': 'gitops-fixture'},
             'binaryData': {'repository.tgz': base64.b64encode(archive.read_bytes()).decode()}},
            {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': 'git-fixture', 'namespace': 'gitops-fixture',
                                                        'labels': {'app': 'git-fixture'}},
             'spec': {'automountServiceAccountToken': False,
                      'securityContext': {'runAsNonRoot': True, 'runAsUser': 65532, 'runAsGroup': 65532,
                                          'fsGroup': 65532, 'seccompProfile': {'type': 'RuntimeDefault'}},
                      'initContainers': [{'name': 'unpack', 'image': image, 'securityContext': security,
                          'resources': resources, 'command': ['tar', '-xzf', '/input/repository.tgz', '-C', '/www'],
                          'volumeMounts': [{'name': 'input', 'mountPath': '/input', 'readOnly': True},
                                           {'name': 'repository', 'mountPath': '/www'}]}],
                      'containers': [{'name': 'http', 'image': image, 'securityContext': security, 'resources': resources,
                          'command': ['httpd', '-f', '-p', '8080', '-h', '/www'],
                          'readinessProbe': {'httpGet': {'path': '/config.git/HEAD', 'port': 8080}, 'periodSeconds': 2},
                          'volumeMounts': [{'name': 'repository', 'mountPath': '/www', 'readOnly': True}]}],
                      'volumes': [{'name': 'input', 'configMap': {'name': 'git-fixture'}},
                                  {'name': 'repository', 'emptyDir': {}}]}},
            {'apiVersion': 'v1', 'kind': 'Service', 'metadata': {'name': 'git-fixture', 'namespace': 'gitops-fixture'},
             'spec': {'selector': {'app': 'git-fixture'}, 'ports': [{'port': 8080, 'targetPort': 8080}]}}])
        self.cluster.run('git-fixture-ready', self.cluster.kubectl('-n', 'gitops-fixture', 'wait',
                         '--for=condition=Ready', 'pod/git-fixture', '--timeout=180s'))
        namespaces = list(yaml.safe_load_all((ROOT / 'clusters/production/namespaces.yaml').read_text()))
        self.manifest('business-namespace', [next(obj for obj in namespaces if obj['metadata']['name'] == 'apps-prod')])
        self.cluster.run('business-guardrails', self.cluster.kubectl('apply', '-f', 'platform/security/apps-prod.yaml'))
        self.cluster.run('business-network-example', self.cluster.kubectl('apply', '-f',
                          'apps/production-demo/admin-networkpolicy.yaml.example'))
        project = yaml.safe_load((ROOT / 'argocd-app-settings/project-production.yaml.example').read_text())
        project['spec']['sourceRepos'] = [REPOSITORY]
        self.manifest('business-project', [project])

    def application(self, name, path):
        app = yaml.safe_load((ROOT / 'argocd-app-settings/production-demo.yaml.example').read_text())
        app['metadata']['name'] = name
        app['spec']['source'].update(repoURL=REPOSITORY, targetRevision=self.revision, path=path)
        self.manifest(name, [app])

    def read_application(self, name):
        return json.loads(subprocess.check_output(self.cluster.kubectl('-n', 'argocd', 'get', 'application', name,
                                                                      '-o', 'json'), text=True, timeout=30))

    def wait_application(self, name, predicate, description, timeout=360):
        deadline = time.monotonic() + timeout
        while True:
            app = self.read_application(name)
            if predicate(app):
                self.sequence += 1
                (self.directory / f'{self.sequence:02d}-{name}-{description}.json').write_text(json.dumps(app, indent=2) + '\n')
                return app
            if time.monotonic() >= deadline:
                (self.directory / (name + '-timeout.json')).write_text(json.dumps(app, indent=2) + '\n')
                raise RuntimeError(f'Timed out waiting for {name}: {description}')
            time.sleep(3)

    def sync(self, name, *, forbidden=None):
        self.wait_application(name, lambda app: not app.get('operation'), 'idle')
        token = uuid.uuid4().hex
        operation = {'operation': {'initiatedBy': {'username': 'isolated-validation'},
                     'info': [{'name': 'validation-run', 'value': token}],
                     'sync': {'prune': False, 'revision': self.revision}}}
        self.cluster.run('sync-' + name, self.cluster.kubectl('-n', 'argocd', 'patch', 'application', name,
                         '--type=merge', '-p', json.dumps(operation)))

        def finished(app):
            state = current_operation(app, token, self.revision)
            return state is not None and state.get('phase') in {'Succeeded', 'Failed', 'Error'}

        app = self.wait_application(name, finished, 'operation')
        state = current_operation(app, token, self.revision)
        if forbidden:
            return require_project_denial(state, *forbidden)
        if state['phase'] != 'Succeeded':
            raise RuntimeError(f'Argo CD synchronization failed: {name}; inspect operation evidence')
        app = self.wait_application(name, lambda app: app.get('status', {}).get('sync', {}).get('status') == 'Synced'
                 and app.get('status', {}).get('sync', {}).get('revision') == self.revision
                 and app.get('status', {}).get('health', {}).get('status') == 'Healthy', 'healthy')
        expected = {'Deployment', 'Service', 'PodDisruptionBudget', 'ConfigMap'}
        resources = app['status'].get('resources', [])
        if len(resources) != 4 or {resource['kind'] for resource in resources} != expected:
            raise RuntimeError('Unexpected business resources in Argo CD inventory')
        if any(resource.get('status') != 'Synced' for resource in resources):
            raise RuntimeError('Not all business resources are synchronized')
        return {'phase': state['phase'], 'revision': self.revision, 'operationToken': token,
                'resources': resources, 'health': app['status']['health']['status']}

    def verify_business(self):
        self.cluster.run('business-rollout', self.cluster.kubectl('-n', 'apps-prod', 'rollout', 'status',
                         'deployment/production-demo', '--timeout=180s'))
        pods = json.loads(self.cluster.run('business-pods', self.cluster.kubectl('-n', 'apps-prod', 'get', 'pods',
                  '-l', 'app.kubernetes.io/name=production-demo', '-o', 'json')))['items']
        ready = [pod for pod in pods if not pod['metadata'].get('deletionTimestamp')
                 and any(c['type'] == 'Ready' and c['status'] == 'True' for c in pod['status'].get('conditions', []))]
        if len(ready) != 2 or len({pod['spec']['nodeName'] for pod in ready}) != 2:
            raise RuntimeError('Expected two Ready business Pods on different nodes')
        deployment = json.loads(self.cluster.run('business-deployment', self.cluster.kubectl('-n', 'apps-prod',
                                'get', 'deployment', 'production-demo', '-o', 'json')))
        if (deployment['spec']['replicas'] != 2 or deployment['status'].get('readyReplicas') != 2
                or deployment['status'].get('updatedReplicas') != 2
                or deployment['status'].get('observedGeneration', 0) < deployment['metadata']['generation']):
            raise RuntimeError('Expected two desired/Ready business replicas')
        response = self.cluster.run('business-http', self.cluster.kubectl('-n', 'apps-prod', 'exec',
                      ready[0]['metadata']['name'], '--', 'wget', '-qO-', 'http://production-demo:8080/'))
        if response != (ROOT / 'apps/production-demo/index.html').read_text():
            raise RuntimeError('Service response differs from committed fixture content')
        return {'nodes': sorted(pod['spec']['nodeName'] for pod in ready), 'replicas': 2,
                'serviceResponseSha256': hashlib.sha256(response.encode()).hexdigest()}

    def execute(self):
        self.prepare()
        evidence = {'fixtureRevision': self.revision, 'projectDenials': []}
        self.cluster.report['gitops'] = evidence
        self.application('production-demo', 'apps/production-demo')
        positive = self.sync('production-demo')
        positive['runtime'] = self.verify_business()
        evidence['firstSync'] = positive
        self.cluster.save()
        self.cluster.run('create-business-drift', self.cluster.kubectl('-n', 'apps-prod', 'scale',
                         'deployment/production-demo', '--replicas=1'))
        self.cluster.run('refresh-business', self.cluster.kubectl('-n', 'argocd', 'annotate', 'application',
                         'production-demo', 'argocd.argoproj.io/refresh=hard', '--overwrite'))
        self.wait_application('production-demo', lambda app: app.get('status', {}).get('sync', {}).get('status') == 'OutOfSync', 'drift')
        reconciled = self.sync('production-demo')
        reconciled['runtime'] = self.verify_business()
        evidence['manualDriftRepair'] = reconciled
        self.cluster.save()
        for name, group, kind, resource, namespace in [
            ('secret', '', 'Secret', 'forbidden-gitops-secret', 'apps-prod'),
            ('clusterrole', 'rbac.authorization.k8s.io', 'ClusterRole', 'forbidden-gitops-clusterrole', ''),
        ]:
            app_name = 'forbidden-' + name
            self.application(app_name, 'checks/' + name)
            # Argo CD v3.5.3 reports the destination namespace even for a
            # rejected cluster-scoped task. Its actual Kubernetes lookup below
            # remains cluster-scoped; never add a namespace to the ClusterRole.
            denied = self.sync(app_name, forbidden=(group, kind, resource, 'apps-prod'))
            args = ['get', kind, resource, '--ignore-not-found', '-o', 'name']
            if namespace:
                args += ['-n', namespace]
            if self.cluster.run('forbidden-absent-' + name, self.cluster.kubectl(*args)).strip():
                raise RuntimeError('Forbidden resource was created despite project rejection')
            evidence['projectDenials'].append(denied)
            self.cluster.save()


def main():
    os.umask(0o077)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    cluster = LocalCluster('platform-gitops-' + uuid.uuid4().hex[:12])
    cluster.report.update(sourceFiles=source_hashes(), scope='real fixture Git sync and project-kind enforcement; no user SSO/RBAC or CNI enforcement evidence')
    print(f'GitOps runtime report: {cluster.directory / "report.json"}', flush=True)
    try:
        cluster.ensure()
        cluster.deploy()
        GitOpsCheck(cluster).execute()
        if source_hashes() != cluster.report['sourceFiles']:
            raise RuntimeError('Sources changed during validation; rerun after edits finish')
        cluster.report['status'] = 'passed'
    except (Exception, KeyboardInterrupt) as exc:
        cluster.report.update(status='failed', error=str(exc) or 'Interrupted')
        print(cluster.report['error'], file=sys.stderr)
        try:
            cluster.run('diagnostics', ['kind', 'export', 'logs', '--name', cluster.name,
                        str(cluster.directory / 'diagnostics')], timeout=120)
        except Exception:
            pass
    finally:
        if cluster.created_now:
            try:
                cluster.owned_state()
                cluster.run('cleanup', ['kind', 'delete', 'cluster', '--name', cluster.name], timeout=180)
                if cluster.docker_nodes():
                    raise RuntimeError('Own cluster nodes remain after cleanup')
                for path in cluster.directory.glob('*.kubeconfig'):
                    path.unlink()
                cluster.report['cleanup'] = 'deleted own temporary cluster and its volumes'
            except Exception as exc:
                cluster.report.update(status='failed', cleanup=str(exc))
        else:
            cluster.report['cleanup'] = 'creation incomplete; inspect partial state before manual cleanup'
        cluster.report['finishedAt'] = datetime.now(timezone.utc).isoformat()
        cluster.save()
    print(f'{cluster.report["status"].upper()}: {cluster.directory / "report.json"}', flush=True)
    return 0 if cluster.report['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
