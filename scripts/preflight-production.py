"""Read-only production prerequisite checks. Never installs or changes resources."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
SECRETS = {
    'argocd': {'argocd-secret': ['server.secretkey', 'oidc.platform.clientSecret']},
    'monitoring': {'grafana-admin': ['admin-user', 'admin-password'],
                   'grafana-encryption': ['secret-key'],
                   'alertmanager-config': ['alertmanager.yaml']},
    'logging': {'logging-kibana-secure-settings': ['xpack.security.encryptionKey',
                 'xpack.encryptedSavedObjects.encryptionKey', 'xpack.reporting.encryptionKey']},
    'gitlab': yaml.safe_load((ROOT / 'platform/gitlab/required-secrets.yaml').read_text())['external'],
}

PLACEHOLDER = re.compile(r'(?:[a-z0-9-]+\.)+invalid\b|REPLACE_|CHANGE_ME', re.IGNORECASE)
NODE_POLICY = ('at least 3 Ready, uncordoned, untainted Linux worker nodes with '
               'distinct kubernetes.io/hostname labels; control-plane/master nodes are excluded')


def input_paths(component, root=ROOT, include_registry=False):
    """Include files consumed outside Helm as well as production chart values."""
    releases = yaml.safe_load((root / 'platform/releases.yaml').read_text())['charts']
    chart_names = {component}
    if component == 'logging':
        chart_names = {'elastic-operator', 'cert-manager'}
    if component in {'argocd', 'monitoring', 'gitlab'}:
        chart_names.update({'envoy-gateway', 'cert-manager'})
    paths = {root / chart['values']['production'] for chart in releases
             if 'production' in chart['values'] and
             (component == 'all' or chart['name'] in chart_names)}
    if component in {'all', 'argocd', 'monitoring', 'gitlab'}:
        paths.update((root / 'platform/networking/production').glob('*.yaml'))
        paths.update(root / path for path in (
            'platform/security/public-certificate.yaml.example',
            'platform/security/clusterissuer-cloudflare.yaml.example',
        ))
    if component in {'all', 'monitoring'}:
        paths.add(root / 'platform/monitoring/alertmanager.yaml.example')
    if component in {'all', 'logging'}:
        for directory in ('base', 'production'):
            paths.update((root / 'platform/logging/kubernetes' / directory).glob('*.yaml'))
        paths.update(root / 'platform/logging/kubernetes/operations' / name for name in (
            'ilm-policy.json', 'index-template.production.json',
        ))
    if include_registry:
        paths.add(root / 'platform/gitlab/values-registry.yaml.example')
    return sorted(paths)


def placeholder_errors(paths, root=ROOT):
    errors = []
    for path in paths:
        try:
            lines = path.read_text().splitlines()
        except OSError:
            errors.append(f'{path.relative_to(root)}: unable to read required input')
            continue
        for number, line in enumerate(lines, 1):
            if not line.lstrip().startswith('#') and PLACEHOLDER.search(line):
                # Report location only: configuration may contain sensitive material.
                errors.append(f'{path.relative_to(root)}:{number}: unresolved environment placeholder')
    return errors


def eligible_workers(nodes):
    """Conservative baseline only; this does not simulate pod scheduling."""
    eligible = []
    excluded = []
    hostnames = set()
    for node in nodes:
        metadata = node.get('metadata', {})
        name = metadata.get('name', '<unnamed>')
        labels = metadata.get('labels', {})
        spec = node.get('spec', {})
        conditions = {item.get('type'): item.get('status')
                      for item in node.get('status', {}).get('conditions', [])}
        reasons = []
        if conditions.get('Ready') != 'True':
            reasons.append('not Ready')
        if spec.get('unschedulable'):
            reasons.append('cordoned')
        if any(label in labels for label in ('node-role.kubernetes.io/control-plane',
                                             'node-role.kubernetes.io/master')):
            reasons.append('control-plane/master')
        if spec.get('taints'):
            reasons.append('tainted (including PreferNoSchedule)')
        if labels.get('kubernetes.io/os') != 'linux':
            reasons.append('missing Linux OS label')
        hostname = labels.get('kubernetes.io/hostname')
        if not hostname or hostname in hostnames:
            reasons.append('missing or duplicate hostname label')
        if any(conditions.get(condition) == 'True' for condition in
               ('MemoryPressure', 'DiskPressure', 'PIDPressure', 'NetworkUnavailable')):
            reasons.append('pressure or network condition')
        if reasons:
            excluded.append(f'{name}: ' + ', '.join(reasons))
        else:
            eligible.append(node)
            hostnames.add(hostname)
    return eligible, excluded


def storage_errors(get, storage_class, workers, expected_driver=None):
    errors = []
    storage = get('get', 'storageclass', storage_class)
    provisioner = storage.get('provisioner')
    if storage.get('reclaimPolicy') != 'Retain':
        errors.append('StorageClass must use Retain or be redesigned with documented data deletion policy')
    if (not isinstance(provisioner, str) or not provisioner.strip() or
            provisioner.startswith('kubernetes.io/') or provisioner in {
                'rancher.io/local-path', 'hostpath.csi.k8s.io', 'local.csi.openebs.io',
            }):
        errors.append('StorageClass requires an explicit, non-local CSI provisioner')
        return errors
    if expected_driver and provisioner != expected_driver:
        errors.append('StorageClass provisioner does not match --expected-csi-driver')
    driver = get('get', 'csidriver', provisioner)
    if driver.get('metadata', {}).get('name') != provisioner:
        errors.append('StorageClass provisioner must match a registered CSIDriver')
    registrations = get('get', 'csinodes').get('items', [])
    registered = {node.get('metadata', {}).get('name') for node in registrations
                  if any(item.get('name') == provisioner and item.get('nodeID')
                         for item in node.get('spec', {}).get('drivers', []))}
    storage_workers = [node for node in workers
                       if node.get('metadata', {}).get('name') in registered]
    if len(storage_workers) < 3:
        errors.append('CSI driver must have a nonempty nodeID registration on at least 3 eligible workers')
    # Local CSI registration does not prove remote storage availability or zone fit.
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--component', choices=['all', *SECRETS], default='all')
    parser.add_argument('--context', help='Explicit context enables read-only live checks')
    parser.add_argument('--storage-class', default='production-rwo')
    parser.add_argument('--expected-csi-driver', help='Require this exact StorageClass CSI provisioner')
    parser.add_argument('--gitlab-registry', action='store_true',
                        help='Also check the optional GitLab Registry overlay and its storage Secret')
    args = parser.parse_args(argv)
    if args.gitlab_registry and args.component not in {'all', 'gitlab'}:
        parser.error('--gitlab-registry requires --component gitlab or all')
    try:
        paths = input_paths(args.component, include_registry=args.gitlab_registry)
        errors = placeholder_errors(paths)
    except (OSError, KeyError, TypeError, yaml.YAMLError):
        print('Unable to load production input paths; check platform/releases.yaml', file=sys.stderr)
        return 1
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        return 1
    if not args.context:
        print(f'Checked {len(paths)} source inputs for known placeholders. '
              'OFFLINE ONLY: no production readiness claim; this is not manifest validation.')
        return 0

    def get(*resource):
        command = ['kubectl', '--context', args.context, '--request-timeout=20s', *resource, '-o', 'json']
        result = subprocess.run(command, text=True, capture_output=True)
        if result.returncode:
            raise RuntimeError('Unable to read ' + ' '.join(resource) + '; check context, RBAC and API connectivity')
        try:
            document = json.loads(result.stdout)
        except json.JSONDecodeError:
            raise RuntimeError('Invalid JSON returned for ' + ' '.join(resource)) from None
        if not isinstance(document, dict):
            raise RuntimeError('Invalid resource returned for ' + ' '.join(resource))
        return document

    print('Conservative node baseline: ' + NODE_POLICY + '.')
    print('CSI checks cover API registration only; backend approval, topology, volume '
          'provisioning, attach/mount and recovery must be verified separately.')
    try:
        nodes = get('get', 'nodes')['items']
        workers, excluded = eligible_workers(nodes)
        print(f'Eligible workers: {len(workers)}; excluded: {len(excluded)}.')
        for reason in excluded:
            print('Excluded node ' + reason)
        if len(workers) < 3:
            errors.append('Production baseline requires ' + NODE_POLICY)
        errors.extend(storage_errors(get, args.storage_class, workers, args.expected_csi_driver))
        selected = {name: dict(secrets) for name, secrets in SECRETS.items()
                    if args.component in {'all', name}}
        if args.gitlab_registry:
            contract = yaml.safe_load((ROOT / 'platform/gitlab/required-secrets.yaml').read_text())
            selected['gitlab'].update(contract['registryOverlay'])
        for namespace, secrets in selected.items():
            for name, keys in secrets.items():
                data = get('-n', namespace, 'get', 'secret', name).get('data', {})
                for key in keys:
                    if not data.get(key):
                        errors.append(f'{namespace}/{name}: missing secret key {key}')
    except RuntimeError as exc:
        errors.append(str(exc))
    except (KeyError, TypeError):
        errors.append('Unexpected Kubernetes resource shape; check API responses and client compatibility')
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        return 1
    print('Read-only prerequisite checks passed; this is not production readiness. '
          'Still verify TLS/DNS, pod scheduling, capacity, storage topology, backend connectivity and restoration.')
    return 0

if __name__ == '__main__':
    sys.exit(main())
