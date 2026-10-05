"""Generate reviewed Argo CD projects/applications; never apply or sync them."""

import argparse
from pathlib import Path
import re
import yaml

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--repo-url', required=True)
parser.add_argument('--revision', required=True, help='Use a reviewed Git commit SHA in production')
parser.add_argument('--output', default='.local/gitops')
parser.add_argument('--example', action='store_true', help='Allow placeholder inputs for offline examples only')
args = parser.parse_args()
if not args.example:
    if '.invalid' in args.repo_url or not args.repo_url.startswith(('https://', 'ssh://', 'git@')):
        parser.error('Use an actual HTTPS/SSH repository URL; --example is for offline examples only')
    if not re.fullmatch(r'(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})', args.revision):
        parser.error('Production revision must be a full reviewed Git commit SHA')
output = (ROOT / args.output).resolve()
if not output.is_relative_to(ROOT):
    parser.error('Output must remain inside this repository')
releases = yaml.safe_load((ROOT / 'platform/releases.yaml').read_text())['charts']
if any(release.get('management') not in {'helm', 'argocd'} for release in releases):
    parser.error('Every chart must declare management: helm or argocd')
# cert-manager leader election and monitoring CoreDNS discovery use kube-system.
namespaces = ['argocd', 'monitoring', 'elastic-system', 'gateway-system', 'cert-manager', 'logging', 'kube-system']
repos = {args.repo_url}
for release in releases:
    if release['management'] == 'argocd':
        repos.add(release.get('argocdRepository', release.get('repository', '')))
cluster_resources = {
    '': ['Namespace'],
    'storage.k8s.io': ['StorageClass'],
    'apiextensions.k8s.io': ['CustomResourceDefinition'],
    'rbac.authorization.k8s.io': ['ClusterRole', 'ClusterRoleBinding'],
    'admissionregistration.k8s.io': ['MutatingWebhookConfiguration', 'ValidatingWebhookConfiguration',
                                    'ValidatingAdmissionPolicy', 'ValidatingAdmissionPolicyBinding'],
    'gateway.networking.k8s.io': ['GatewayClass'],
    'cert-manager.io': ['ClusterIssuer'],
}
project = {
    'apiVersion': 'argoproj.io/v1alpha1', 'kind': 'AppProject',
    'metadata': {'name': 'platform-infra', 'namespace': 'argocd'},
    'spec': {
        'description': 'Administrator-owned platform infrastructure; never delegate to app users',
        'sourceRepos': sorted(repos),
        'destinations': [{'server': 'https://kubernetes.default.svc', 'namespace': ns} for ns in namespaces],
        'clusterResourceWhitelist': [{'group': group, 'kind': kind}
                                     for group, kinds in cluster_resources.items() for kind in kinds],
        'namespaceResourceWhitelist': [{'group': '*', 'kind': '*'}],
    },
}
objects = [project]
for release in releases:
    # Bootstrap and lifecycle-sensitive charts remain explicit Helm releases.
    if release['management'] != 'argocd':
        continue
    chart = release['chart'].rsplit('/', 1)[-1]
    objects.append({
        'apiVersion': 'argoproj.io/v1alpha1', 'kind': 'Application',
        'metadata': {'name': release['name'], 'namespace': 'argocd'},
        'spec': {
            'project': 'platform-infra',
            'sources': [
                {'repoURL': release.get('argocdRepository', release.get('repository')),
                 'chart': chart, 'targetRevision': str(release['version']),
                 'helm': {'releaseName': release['name'],
                          'valueFiles': ['$values/' + release['values']['production']]}},
                {'repoURL': args.repo_url, 'targetRevision': args.revision, 'ref': 'values'},
            ],
            'destination': {'server': 'https://kubernetes.default.svc', 'namespace': release['namespace']},
            'syncPolicy': {'syncOptions': ['ServerSideApply=true', 'FailOnSharedResource=true']},
        },
    })
for name, path, namespace in [
    ('platform-networking', 'platform/networking/production', 'gateway-system'),
    ('platform-logging', 'platform/logging/kubernetes/production', 'logging'),
]:
    objects.append({
        'apiVersion': 'argoproj.io/v1alpha1', 'kind': 'Application',
        'metadata': {'name': name, 'namespace': 'argocd'},
        'spec': {
            'project': 'platform-infra',
            'source': {'repoURL': args.repo_url, 'targetRevision': args.revision, 'path': path},
            'destination': {'server': 'https://kubernetes.default.svc', 'namespace': namespace},
            'syncPolicy': {'syncOptions': ['ServerSideApply=true', 'FailOnSharedResource=true']},
        },
    })
output.mkdir(parents=True, exist_ok=True)
(output / 'infrastructure.yaml').write_text(yaml.safe_dump_all(objects, sort_keys=False))
print(f'Generated {len(objects)} objects at {output / "infrastructure.yaml"}; no sync, pruning or apply.')
