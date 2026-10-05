"""Manage this repository's local kind baseline; never adopt or delete another cluster."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import yaml
from render import verify_archive

ROOT = Path(__file__).resolve().parents[1]
ARGOCD_WORKLOADS = {
    ('Deployment', 'argocd-applicationset-controller'),
    ('Deployment', 'argocd-notifications-controller'),
    ('Deployment', 'argocd-redis'),
    ('Deployment', 'argocd-repo-server'),
    ('Deployment', 'argocd-server'),
    ('StatefulSet', 'argocd-application-controller'),
}


def verify_argocd_workloads(workloads):
    actual = {(w['kind'], w['metadata']['name']) for w in workloads}
    missing = ARGOCD_WORKLOADS - actual
    if missing:
        raise RuntimeError('Missing required Argo CD workloads: ' + ', '.join(name for _, name in sorted(missing)))
    for workload in workloads:
        desired = workload['spec'].get('replicas', 1)
        status = workload.get('status', {})
        if (desired < 1 or status.get('readyReplicas', 0) != desired
                or status.get('updatedReplicas', 0) != desired
                or status.get('observedGeneration', 0) < workload['metadata']['generation']):
            raise RuntimeError('Argo CD workload is not fully rolled out: ' + workload['metadata']['name'])


def valid_name(name):
    if not re.fullmatch(r'platform-[a-z0-9]+(?:-[a-z0-9]+)*', name) or len(name) > 40:
        raise ValueError('Use a platform- prefixed lowercase local cluster name, at most 40 characters')
    return name


class LocalCluster:
    def __init__(self, name):
        self.name = valid_name(name)
        self.context = 'kind-' + name
        self.directory = ROOT / '.local/clusters' / name
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_path = self.directory / 'state.json'
        self.kubeconfig = self.directory / 'internal.kubeconfig'
        self.report = {'status': 'running', 'cluster': name, 'context': self.context,
                       'startedAt': datetime.now(timezone.utc).isoformat(), 'steps': []}
        self.config_hash = hashlib.sha256((ROOT / 'clusters/local/kind.yaml').read_bytes()).hexdigest()
        self.created_now = False

    def save(self):
        (self.directory / 'report.json').write_text(json.dumps(self.report, indent=2) + '\n')

    def run(self, step, command, timeout=600):
        print('CHECK ' + step, flush=True)
        log = self.directory / f'{len(self.report["steps"]) + 1:02d}-{step}.log'
        record = {'name': step, 'log': str(log.relative_to(ROOT)), 'status': 'running'}
        self.report['steps'].append(record)
        self.save()
        try:
            result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=timeout)
            log.write_text(result.stdout + result.stderr)
            record.update(status='passed' if result.returncode == 0 else 'failed', exitCode=result.returncode)
            self.save()
            if result.returncode:
                raise RuntimeError(f'{step} failed; inspect {log}')
            return result.stdout
        except subprocess.TimeoutExpired:
            record.update(status='failed', error='timeout')
            self.save()
            raise RuntimeError(f'{step} timed out; retain cluster and inspect its state') from None

    def docker_nodes(self):
        names = subprocess.check_output(['kind', 'get', 'nodes', '--name', self.name], text=True).split()
        if not names:
            return {}
        nodes = json.loads(subprocess.check_output(['docker', 'inspect', *names], text=True))
        if any(n['Config']['Labels'].get('io.x-k8s.kind.cluster') != self.name for n in nodes):
            raise RuntimeError('Unexpected Docker node ownership')
        return {n['Name'].lstrip('/'): n['Id'] for n in nodes}

    def owned_state(self):
        if not self.state_path.is_file():
            raise RuntimeError('Cluster has no repository ownership record; refusing to adopt it')
        state = json.loads(self.state_path.read_text())
        if state.get('configSha256') != self.config_hash or state.get('cluster') != self.name:
            raise RuntimeError('Cluster configuration differs from its record; no automatic recreation/upgrade')
        if not state.get('nodes') or state['nodes'] != self.docker_nodes():
            raise RuntimeError('Cluster node identities changed or creation was incomplete; inspect before recovery')
        return state

    def connect(self):
        self.owned_state()
        self.run('export-internal-kubeconfig', ['kind', 'export', 'kubeconfig', '--name', self.name,
                 '--internal', '--kubeconfig', str(self.kubeconfig)])
        self.kubeconfig.chmod(0o600)
        # Attach only the current short-lived runner container. Nodes/API remain
        # on the existing kind network; the host API listener stays loopback.
        identity = os.environ.get('HOSTNAME', '')
        if not re.fullmatch(r'[0-9a-f]{12,64}', identity):
            raise RuntimeError('Cannot identify current Docker runner container')
        container = json.loads(subprocess.check_output(['docker', 'inspect', identity], text=True))[0]
        host_root = os.environ.get('PLATFORM_HOST_ROOT')
        if not any(m['Destination'] == '/workspace' and m['Source'] == host_root for m in container['Mounts']):
            raise RuntimeError('Refusing to change networking of a container outside this repository runner')
        if 'kind' not in container['NetworkSettings']['Networks']:
            self.run('join-kind-network', ['docker', 'network', 'connect', 'kind', identity])

    def kubectl(self, *args):
        return ['kubectl', '--kubeconfig', str(self.kubeconfig), '--context', self.context, *args]

    def ensure(self):
        existing = subprocess.check_output(['kind', 'get', 'clusters'], text=True).split()
        if self.name in existing:
            self.owned_state()
        else:
            if self.state_path.exists():
                raise RuntimeError('Cluster is absent but a previous state record exists; choose a new name or archive the old record')
            state = {'cluster': self.name, 'configSha256': self.config_hash, 'status': 'creating'}
            self.state_path.write_text(json.dumps(state, indent=2) + '\n')
            self.run('create-kind', ['kind', 'create', 'cluster', '--name', self.name,
                     '--config', 'clusters/local/kind.yaml', '--retain', '--wait', '120s',
                     '--kubeconfig', str(self.directory / 'host.kubeconfig')], timeout=900)
            self.created_now = True
            state.update(status='created', nodes=self.docker_nodes())
            self.state_path.write_text(json.dumps(state, indent=2) + '\n')
        self.connect()
        self.run('nodes-ready', self.kubectl('wait', '--for=condition=Ready', 'node', '--all', '--timeout=240s'))
        nodes = json.loads(self.run('node-state', self.kubectl('get', 'nodes', '-o', 'json')))['items']
        if len(nodes) != 3:
            raise RuntimeError('Expected exactly one control-plane and two worker nodes')
        self.report['nodes'] = [{'name': n['metadata']['name'], 'version': n['status']['nodeInfo']['kubeletVersion']}
                                for n in nodes]

    def deploy(self):
        for name, path in [('namespaces', 'platform/namespaces'), ('storage', 'platform/storage'),
                           ('storage-demo', 'apps/storage-demo')]:
            self.run('apply-' + name, self.kubectl('apply', '-k', path))
        self.run('storage-ready', self.kubectl('-n', 'apps-dev', 'wait', '--for=condition=Ready',
                                               'pod/storage-demo', '--timeout=240s'))
        release = next(c for c in yaml.safe_load((ROOT / 'platform/releases.yaml').read_text())['charts']
                       if c['name'] == 'argocd')
        cache = ROOT / '.cache/charts' / f'argocd-{release["version"]}'
        if not list(cache.glob('*.tgz')):
            self.run('download-argocd', ['python', 'scripts/render.py', '--environment', 'local', '--component', 'argocd'])
        archives = list(cache.glob('*.tgz'))
        if len(archives) != 1:
            raise RuntimeError('Expected one pinned Argo CD chart archive')
        self.report['argocdChartSha256'] = verify_archive(archives[0], release)
        self.run('install-argocd', ['helm', 'upgrade', '--install', 'argocd', str(archives[0]),
                 '--kubeconfig', str(self.kubeconfig), '--kube-context', self.context,
                 '--namespace', 'argocd', '--create-namespace', '-f', release['values']['local'],
                 '--wait', '--timeout', '10m'], timeout=720)

    def verify(self):
        self.owned_state()
        before = self.run('marker-before', self.kubectl('-n', 'apps-dev', 'exec', 'storage-demo', '--', 'cat', '/data/marker'))
        if not before.strip():
            raise RuntimeError('Storage marker is empty')
        claim = json.loads(self.run('claim-state', self.kubectl('-n', 'apps-dev', 'get', 'pvc', 'storage-demo', '-o', 'json')))
        if claim['status']['phase'] != 'Bound':
            raise RuntimeError('Storage demo PVC is not Bound')
        volume = json.loads(self.run('volume-state', self.kubectl('get', 'pv', claim['spec']['volumeName'], '-o', 'json')))
        if volume['spec']['persistentVolumeReclaimPolicy'] != 'Retain':
            raise RuntimeError('Demo PV does not retain data')
        self.run('recreate-storage-pod', self.kubectl('-n', 'apps-dev', 'delete', 'pod', 'storage-demo', '--timeout=90s'))
        self.run('reapply-storage-demo', self.kubectl('apply', '-k', 'apps/storage-demo'))
        self.run('replacement-ready', self.kubectl('-n', 'apps-dev', 'wait', '--for=condition=Ready',
                                                   'pod/storage-demo', '--timeout=180s'))
        after = self.run('marker-after', self.kubectl('-n', 'apps-dev', 'exec', 'storage-demo', '--', 'cat', '/data/marker'))
        if before != after:
            raise RuntimeError('PVC marker changed after Pod recreation')
        workloads = json.loads(self.run('argocd-workloads', self.kubectl('-n', 'argocd', 'get',
                       'deployments,statefulsets', '-l', 'app.kubernetes.io/instance=argocd', '-o', 'json')))['items']
        verify_argocd_workloads(workloads)
        self.report.update(storage={'markerPreserved': True, 'volume': claim['spec']['volumeName'], 'reclaimPolicy': 'Retain'},
                           argocd={'readyWorkloads': [w['metadata']['name'] for w in workloads]})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['up', 'verify'])
    parser.add_argument('--name', default='platform-local')
    args = parser.parse_args()
    os.umask(0o077)
    cluster = LocalCluster(args.name)
    try:
        if args.action == 'up':
            cluster.ensure()
            cluster.deploy()
        else:
            cluster.connect()
            cluster.verify()
        cluster.report['status'] = 'passed'
    except (Exception, KeyboardInterrupt) as exc:
        cluster.report.update(status='failed', error=str(exc) or 'Interrupted')
        print(cluster.report['error'], file=sys.stderr)
    finally:
        cluster.report['finishedAt'] = datetime.now(timezone.utc).isoformat()
        cluster.save()
    print(f'{cluster.report["status"].upper()}: {cluster.directory / "report.json"}')
    print('Cluster/data retained. This is a single-host local baseline, not production availability.')
    return 0 if cluster.report['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
