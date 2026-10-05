"""Validate rendered custom resources against the pinned charts' CRD schemas.

This does not execute CEL, admission webhooks or Kubernetes defaulting/pruning.
"""
from pathlib import Path
import sys
import yaml
from jsonschema import Draft7Validator
from rendered_inputs import checked_documents

ROOT = Path(__file__).resolve().parents[1]
yaml.SafeLoader.add_constructor('tag:yaml.org,2002:value', yaml.SafeLoader.construct_scalar)
schemas = {}
for documents in checked_documents(ROOT / 'rendered/production', 'name').values():
    for obj in documents:
        if not obj or obj.get('kind') != 'CustomResourceDefinition':
            continue
        spec = obj['spec']
        for version in spec['versions']:
            schema = version.get('schema', {}).get('openAPIV3Schema')
            if schema:
                schemas[(spec['group'] + '/' + version['name'], spec['names']['kind'])] = schema
if not schemas:
    sys.exit('Render production charts before validating CRDs.')
builtin_groups = {'', 'apps', 'batch', 'policy', 'networking.k8s.io', 'rbac.authorization.k8s.io',
                  'storage.k8s.io', 'autoscaling', 'apiextensions.k8s.io'}
errors = []
validated = 0
skipped = 0
for path, documents in checked_documents(ROOT / 'rendered/kustomize', 'path').items():
    for obj in documents:
        if not obj:
            continue
        key = (obj['apiVersion'], obj['kind'])
        schema = schemas.get(key)
        if schema is None:
            group = obj['apiVersion'].split('/')[0] if '/' in obj['apiVersion'] else ''
            if group not in builtin_groups:
                errors.append(f'{path}: no schema found for {key}')
            skipped += 1
            continue
        for error in Draft7Validator(schema).iter_errors(obj):
            location = '/'.join(str(p) for p in error.absolute_path)
            errors.append(f'{path}:{obj["kind"]}/{obj["metadata"]["name"]}:{location}: {error.message}')
        validated += 1
if not validated:
    errors.append('No custom resources validated; render Kustomize entrypoints first.')
for error in errors:
    print(error, file=sys.stderr)
print(f'CRD schema checks: {validated} custom objects, {skipped} built-in objects skipped, {len(errors)} errors.')
print('Does not cover built-in Kubernetes schemas, CEL rules, admission, or controller/runtime behavior.')
sys.exit(1 if errors else 0)
