"""Run only a disposable local image compatibility check; never deploy Kubernetes."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import subprocess

import yaml

ROOT = Path(__file__).resolve().parents[2]


def main():
    deployment = yaml.safe_load((ROOT / 'apps/production-demo/deployment.yaml').read_text())
    container = deployment['spec']['template']['spec']['containers'][0]
    image = container['image']
    evidence_path = ROOT / 'rendered/business-demo/smoke-image.json'
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.unlink(missing_ok=True)
    # Compose binds are resolved by the Docker daemon, not within /workspace.
    host_root = os.environ['PLATFORM_HOST_ROOT']
    shell = (shlex.join(container['command']) + ' & child=$!; trap "kill $child" EXIT; '
             'for attempt in 1 2 3 4 5; do '
             'if wget -qO- http://127.0.0.1:8080/; then exit 0; fi; sleep 1; '
             'done; exit 1')
    service = {
        'image': image, 'user': '65532:65532', 'read_only': True,
        'network_mode': 'none', 'cap_drop': ['ALL'],
        'security_opt': ['no-new-privileges:true'], 'mem_limit': '32m', 'cpus': 0.1,
        'command': ['sh', '-ec', shell.replace('$', '$$')],
        'volumes': [{'type': 'bind', 'source': host_root + '/apps/production-demo/index.html',
                     'target': '/www/index.html', 'read_only': True,
                     'bind': {'create_host_path': False}}],
    }
    result = subprocess.run(['docker', 'compose', '-p', 'platform-business-image-check', '-f', '-',
                             'run', '--rm', '--no-deps', 'web'],
                            input=yaml.safe_dump({'services': {'web': service}}),
                            text=True, capture_output=True)
    if result.returncode != 0 or 'production-demo-ready' not in result.stdout:
        raise RuntimeError(f'Restricted HTTP image check failed:\n{result.stdout}\n{result.stderr}')
    architecture = subprocess.run(['docker', 'image', 'inspect', image,
                                   '--format', '{{.Os}}/{{.Architecture}}'],
                                  text=True, capture_output=True, check=True).stdout.strip()
    evidence_path.write_text(json.dumps({
        'status': 'passed', 'checkedAt': datetime.now(timezone.utc).isoformat(),
        'image': image, 'platform': architecture, 'user': service['user'],
        'readOnlyRootFilesystem': True, 'capabilitiesDropped': ['ALL'],
        'allowPrivilegeEscalation': False, 'networkMode': 'none',
        'expectedMarkerFound': True,
        'scope': 'Disposable Docker HTTP image check only; no Kubernetes deployment or admission.',
    }, indent=2) + '\n')
    print(f'PASS {image}: serves expected page as UID 65532 with read-only root, '
          'all capabilities dropped and no external network; disposable container removed.')
    print('This checks the image command on this Docker architecture, not Kubernetes admission, '
          'Service routing, CNI enforcement, scheduling or all supported architectures.')
    print(f'Evidence: {evidence_path.relative_to(ROOT)}')


if __name__ == '__main__':
    main()
