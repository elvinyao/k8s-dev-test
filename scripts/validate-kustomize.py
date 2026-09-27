"""Render local Kustomize entrypoints; never contact the Kubernetes API."""
from pathlib import Path
import subprocess
import yaml

ROOT = Path(__file__).resolve().parents[1]
paths = [
    'platform/namespaces', 'platform/storage', 'apps/storage-demo',
    'clusters/production', 'platform/networking/production',
    'platform/logging/kubernetes/local', 'platform/logging/kubernetes/production',
]
output = ROOT / 'rendered/kustomize'
output.mkdir(parents=True, exist_ok=True)
for path in paths:
    result = subprocess.run(['kubectl', 'kustomize', path], cwd=ROOT,
                            check=True, text=True, capture_output=True)
    documents = [d for d in yaml.safe_load_all(result.stdout) if d]
    identities = [(d['apiVersion'], d['kind'], d.get('metadata', {}).get('namespace'),
                   d['metadata']['name']) for d in documents]
    if len(identities) != len(set(identities)):
        raise ValueError(f'Duplicate object identities: {path}')
    (output / (path.replace('/', '-') + '.yaml')).write_text(result.stdout)
    print(f'PASS {path}: {len(documents)} objects')
print('Kustomize rendering only; no CRD schema, API admission, or runtime verification.')
