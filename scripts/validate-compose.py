"""Resolve every Compose example and check deployment invariants, without up."""

import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
output = ROOT / 'rendered/compose'
output.mkdir(parents=True, exist_ok=True)
for directory in sorted((ROOT / 'compose').iterdir()):
    manifest = directory / 'compose.yaml'
    if not manifest.is_file():
        continue
    command = ['docker', 'compose', '--project-directory', str(directory),
               '--env-file', str(directory / '.env.example'), '-f', str(manifest),
               '--profile', '*', 'config', '--format', 'json']
    result = subprocess.run(command, check=True, text=True, capture_output=True)
    config = json.loads(result.stdout)
    issues = []
    for name, service in config['services'].items():
        image = service.get('image', '')
        if not image or image.endswith(':latest') or ':' not in image.rsplit('/', 1)[-1]:
            issues.append(f'{name}: image must have an explicit version')
        if not service.get('mem_limit'):
            issues.append(f'{name}: mem_limit is missing')
        for port in service.get('ports', []):
            if port.get('host_ip') not in {'127.0.0.1', '::1'}:
                issues.append(f'{name}: example published port must bind loopback')
        if service.get('privileged'):
            issues.append(f'{name}: privileged mode is not allowed in the base example')
        if service.get('logging', {}).get('options', {}).get('max-size') is None:
            issues.append(f'{name}: bounded container logs required')
        for volume in service.get('volumes', []):
            if volume.get('type') == 'bind' and volume.get('source', '').startswith('/workspace'):
                issues.append(f'{name}: bind source must use daemon-visible host path')
    if issues:
        raise ValueError(f'{directory.name}:\n' + '\n'.join(issues))
    (output / f'{directory.name}.json').write_text(json.dumps(config, indent=2) + '\n')
    print(f"PASS {directory.name}: {len(config['services'])} services; Compose model and safety defaults")
print('No images pulled, containers started, credentials generated, or host directories changed.')
