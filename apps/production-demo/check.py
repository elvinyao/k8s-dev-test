"""Check this business application's rendered resources against its AppProject."""
import argparse
from pathlib import Path
import re
import subprocess

import yaml

ROOT = Path(__file__).resolve().parents[2]
CLUSTER_RESOURCES = {
    ('', 'Namespace'), ('', 'Node'), ('', 'PersistentVolume'),
    ('rbac.authorization.k8s.io', 'ClusterRole'),
    ('rbac.authorization.k8s.io', 'ClusterRoleBinding'),
    ('storage.k8s.io', 'StorageClass'),
    ('apiextensions.k8s.io', 'CustomResourceDefinition'),
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_contract(project, application, objects, *, allow_placeholders=False):
    """Validate the explicit allow-list contract; this is not Argo CD authorization."""
    p = project['spec']
    app = application['spec']
    require(app['project'] == project['metadata']['name'], 'Application references another Project')
    require(application['metadata'].get('namespace') == project['metadata'].get('namespace') == 'argocd',
            'Project and Application must be administrator-owned in argocd')
    source = app['source']
    require(source['repoURL'] in p['sourceRepos'], 'Project denies source repository')
    require(source.get('path') == 'apps/production-demo', 'Unexpected application source path')
    require(app['destination'] in p['destinations'], 'Project denies destination')
    require(app['destination']['namespace'] == 'apps-prod', 'Business app must target apps-prod')
    revision = source['targetRevision']
    require(isinstance(revision, str) and bool(re.fullmatch(r'[0-9a-fA-F]{40}|[0-9a-fA-F]{64}', revision)),
            'Use a full reviewed commit SHA')
    if not allow_placeholders:
        require('.invalid' not in source['repoURL'] and int(revision, 16) != 0,
                'Replace the example repository and all-zero commit SHA before deployment')
    require(not p.get('clusterResourceWhitelist'), 'Business Project must not allow cluster resources')
    require({'group': '*', 'kind': '*'} in p.get('clusterResourceBlacklist', []),
            'Business Project must explicitly deny all cluster resources')
    allowed = {(entry['group'], entry['kind']) for entry in p['namespaceResourceWhitelist']}
    require(all('*' not in pair for pair in allowed), 'Review wildcard namespace resource permissions')
    blocked = {(entry['group'], entry['kind']) for entry in p.get('namespaceResourceBlacklist', [])}
    require('automated' not in app.get('syncPolicy', {}), 'Review automatic synchronization separately')
    require(not application['metadata'].get('finalizers'), 'Review cascading deletion separately')
    require(objects, 'No rendered business resources')
    for obj in objects:
        group = obj['apiVersion'].split('/')[0] if '/' in obj['apiVersion'] else ''
        resource = (group, obj['kind'])
        require(resource not in CLUSTER_RESOURCES, f'Project denies cluster resource {resource}')
        require(resource in allowed, f'Project denies namespaced resource {resource}')
        require(not any((g in {group, '*'}) and (k in {obj['kind'], '*'}) for g, k in blocked),
                f'Project blacklist denies resource {resource}')
        require(obj.get('metadata', {}).get('namespace') == app['destination']['namespace'],
                f'Project denies resource namespace for {obj["kind"]}')


def render():
    result = subprocess.run(['kubectl', 'kustomize', 'apps/production-demo'], cwd=ROOT,
                            check=True, text=True, capture_output=True)
    return [obj for obj in yaml.safe_load_all(result.stdout) if obj]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', default='argocd-app-settings/project-production.yaml.example')
    parser.add_argument('--application', default='argocd-app-settings/production-demo.yaml.example')
    parser.add_argument('--example', action='store_true', help='Allow known placeholder URL/SHA offline only')
    args = parser.parse_args()
    project = yaml.safe_load((ROOT / args.project).read_text())
    application = yaml.safe_load((ROOT / args.application).read_text())
    objects = render()
    validate_contract(project, application, objects, allow_placeholders=args.example)
    print(f'PASS {len(objects)} rendered business resources and explicit Project permissions; '
          'no API admission, synchronization or network enforcement tested.')
    if args.example:
        print('Example mode: repository/commit placeholders are accepted only for this offline check.')


if __name__ == '__main__':
    main()
