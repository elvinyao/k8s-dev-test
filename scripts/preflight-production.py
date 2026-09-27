"""Read-only production prerequisite checks. Never installs or changes resources."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
SECRETS = {
    'argocd': {'argocd-secret': ['server.secretkey', 'oidc.platform.clientSecret']},
    'monitoring': {'grafana-admin': ['admin-user', 'admin-password'],
                   'alertmanager-config': ['alertmanager.yaml']},
    'logging': {'logging-kibana-secure-settings': ['xpack.security.encryptionKey',
                 'xpack.encryptedSavedObjects.encryptionKey', 'xpack.reporting.encryptionKey']},
    'gitlab': {'gitlab-initial-root-password': ['password'],
               'gitlab-postgresql': ['password'],
               'gitlab-postgresql-tls': ['ca.crt', 'tls.crt', 'tls.key'],
               'gitlab-redis': ['password'], 'gitlab-backend-ca': ['backend-ca.crt'],
               'gitlab-gitaly-token': ['token'], 'gitlab-shell-token': ['secret'],
               'gitlab-object-storage': ['connection'], 'gitlab-backup-storage': ['config'],
               'gitlab-smtp': ['password']},
}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--component', choices=['all', *SECRETS], default='all')
    parser.add_argument('--context', help='Explicit context enables read-only live checks')
    parser.add_argument('--storage-class', default='production-rwo')
    args = parser.parse_args()
    releases = yaml.safe_load((ROOT / 'platform/releases.yaml').read_text())['charts']
    paths = [ROOT / c['values']['production'] for c in releases
             if 'production' in c['values'] and (args.component == 'all' or c['name'] == args.component)]
    if args.component in {'all', 'argocd'}:
        paths.append(ROOT / 'platform/networking/production/routes.yaml')
    errors = []
    for path in paths:
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if line.lstrip().startswith('#'):
                continue
            if 'example.invalid' in line or 'REPLACE_' in line or 'CHANGE_ME' in line:
                errors.append(f'{path.relative_to(ROOT)}:{number}: unresolved environment placeholder')
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        return 1
    if not args.context:
        print('Environment placeholders resolved. OFFLINE ONLY: no production readiness claim.')
        return 0

    def get(*resource):
        command = ['kubectl', '--context', args.context, '--request-timeout=20s', *resource, '-o', 'json']
        result = subprocess.run(command, text=True, capture_output=True)
        if result.returncode:
            raise RuntimeError('Unable to read ' + ' '.join(resource) + '; check context, RBAC and API connectivity')
        return json.loads(result.stdout)

    try:
        nodes = get('get', 'nodes')['items']
        ready = [n for n in nodes if any(c['type'] == 'Ready' and c['status'] == 'True'
                 for c in n.get('status', {}).get('conditions', []))]
        if len(ready) < 3:
            errors.append('Production baseline requires at least 3 Ready nodes; verify schedulability separately')
        storage = get('get', 'storageclass', args.storage_class)
        if storage.get('provisioner') in {'rancher.io/local-path', 'kubernetes.io/no-provisioner'}:
            errors.append('Production data baseline requires an approved CSI storage backend')
        if storage.get('reclaimPolicy') != 'Retain':
            errors.append('StorageClass must use Retain or be redesigned with documented data deletion policy')
        selected = SECRETS if args.component == 'all' else {args.component: SECRETS[args.component]}
        for namespace, secrets in selected.items():
            for name, keys in secrets.items():
                data = get('-n', namespace, 'get', 'secret', name).get('data', {})
                for key in keys:
                    if not data.get(key):
                        errors.append(f'{namespace}/{name}: missing secret key {key}')
    except (RuntimeError, json.JSONDecodeError) as exc:
        errors.append(str(exc))
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        return 1
    print('Read-only prerequisites passed. Still verify TLS/DNS, backend connectivity, capacity and restoration.')
    return 0

if __name__ == '__main__':
    sys.exit(main())
