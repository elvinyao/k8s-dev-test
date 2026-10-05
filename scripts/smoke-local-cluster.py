"""Create a fresh temporary local baseline, verify it, then remove only its nodes."""
from datetime import datetime, timezone
import json
import os
import subprocess
import sys
import uuid

from local_cluster import LocalCluster


def main():
    os.umask(0o077)
    cluster = LocalCluster('platform-smoke-' + uuid.uuid4().hex[:12])
    print(f'Local smoke report: {cluster.directory / "report.json"}', flush=True)
    try:
        cluster.ensure()
        cluster.deploy()
        # A second up must reuse the recorded node identities and preserve the
        # existing PVC while applying the same pinned platform configuration.
        original_nodes = cluster.docker_nodes()
        cluster.ensure()
        cluster.deploy()
        if cluster.docker_nodes() != original_nodes:
            raise RuntimeError('Repeated up replaced cluster nodes')
        cluster.report['idempotentUp'] = True
        cluster.verify()
        cluster.report['status'] = 'passed'
    except (Exception, KeyboardInterrupt) as exc:
        cluster.report.update(status='failed', error=str(exc) or 'Interrupted')
        print(cluster.report['error'], file=sys.stderr)
        # Keep diagnostics only for this freshly named cluster, never collect
        # logs or credentials from unrelated contexts.
        try:
            cluster.run('diagnostics', ['kind', 'export', 'logs', '--name', cluster.name,
                        str(cluster.directory / 'diagnostics')], timeout=120)
        except Exception:
            pass
    finally:
        if cluster.created_now:
            try:
                cluster.owned_state()
                cluster.run('cleanup', ['kind', 'delete', 'cluster', '--name', cluster.name], timeout=180)
                if cluster.docker_nodes():
                    raise RuntimeError('Cluster nodes remain after cleanup')
                cluster.report['cleanup'] = 'deleted own temporary cluster and its local volumes'
                for path in cluster.directory.glob('*.kubeconfig'):
                    path.unlink()
            except Exception as exc:
                cluster.report.update(status='failed', cleanup=str(exc))
        else:
            cluster.report['cleanup'] = 'creation did not finish; inspect retained partial state before manual cleanup'
        cluster.report['finishedAt'] = datetime.now(timezone.utc).isoformat()
        cluster.save()
    print(f'{cluster.report["status"].upper()}: {cluster.directory / "report.json"}', flush=True)
    return 0 if cluster.report['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
