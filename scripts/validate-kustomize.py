"""Render local Kustomize entrypoints; never contact the Kubernetes API."""
from pathlib import Path
import hashlib
import json
import subprocess
import yaml

ROOT = Path(__file__).resolve().parents[1]
paths = [
    'platform/namespaces', 'platform/storage', 'apps/storage-demo',
    'apps/production-demo',
    'clusters/production', 'platform/networking/production',
    'platform/logging/kubernetes/local', 'platform/logging/kubernetes/production',
]
output = ROOT / 'rendered/kustomize'
output.mkdir(parents=True, exist_ok=True)
(output / 'summary.json').unlink(missing_ok=True)
summary = []
for path in paths:
    result = subprocess.run(['kubectl', 'kustomize', path], cwd=ROOT,
                            check=True, text=True, capture_output=True)
    documents = [d for d in yaml.safe_load_all(result.stdout) if d]
    identities = [(d['apiVersion'], d['kind'], d.get('metadata', {}).get('namespace'),
                   d['metadata']['name']) for d in documents]
    if len(identities) != len(set(identities)):
        raise ValueError(f'Duplicate object identities: {path}')
    (output / (path.replace('/', '-') + '.yaml')).write_text(result.stdout)
    summary.append({'path': path, 'objects': len(documents),
                    'manifestSha256': hashlib.sha256(result.stdout.encode()).hexdigest()})
    print(f'PASS {path}: {len(documents)} objects')
(output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print('Kustomize rendering only; no CRD schema, API admission, or runtime verification.')
