"""Guard against adopting, upgrading or connecting to unrelated clusters."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import local_cluster


class ArgoCDReadiness(unittest.TestCase):
    def setUp(self):
        self.workloads = [
            {'kind': kind, 'metadata': {'name': name, 'generation': 2}, 'spec': {'replicas': 1},
             'status': {'readyReplicas': 1, 'updatedReplicas': 1, 'observedGeneration': 2}}
            for kind, name in local_cluster.ARGOCD_WORKLOADS
        ]

    def test_required_workload_cannot_be_missing_even_if_others_are_ready(self):
        local_cluster.verify_argocd_workloads(self.workloads)
        for missing in self.workloads:
            with self.subTest(name=missing['metadata']['name']), self.assertRaisesRegex(RuntimeError, 'Missing required'):
                local_cluster.verify_argocd_workloads([w for w in self.workloads if w is not missing])

    def test_old_or_scaled_down_workloads_are_not_healthy(self):
        variants = [({'replicas': 0}, {'readyReplicas': 0, 'updatedReplicas': 0, 'observedGeneration': 2}),
                    ({'replicas': 1}, {'readyReplicas': 1, 'updatedReplicas': 0, 'observedGeneration': 2}),
                    ({'replicas': 1}, {'readyReplicas': 1, 'updatedReplicas': 1, 'observedGeneration': 1})]
        for spec, status in variants:
            workloads = copy.deepcopy(self.workloads)
            workloads[0].update(spec=spec, status=status)
            with self.subTest(spec=spec, status=status), self.assertRaisesRegex(RuntimeError, 'not fully rolled out'):
                local_cluster.verify_argocd_workloads(workloads)


class LocalClusterGuards(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / 'clusters/local').mkdir(parents=True)
        (self.root / 'clusters/local/kind.yaml').write_text('kind: Cluster\n')
        self.root_patch = patch.object(local_cluster, 'ROOT', self.root)
        self.root_patch.start()
        self.cluster = local_cluster.LocalCluster('platform-test')

    def tearDown(self):
        self.root_patch.stop()
        self.temporary.cleanup()

    def test_name_cannot_escape_local_state_or_target_arbitrary_cluster(self):
        for name in ['production', '../platform-test', 'platform-TEST', 'platform-a/' , 'platform-' + 'a' * 50]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                local_cluster.valid_name(name)

    def test_existing_cluster_without_record_is_never_adopted(self):
        with patch.object(local_cluster.subprocess, 'check_output', return_value='platform-test\n'), \
                patch.object(self.cluster, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'refusing to adopt'):
                self.cluster.ensure()
            run.assert_not_called()

    def test_replaced_nodes_are_not_treated_as_owned(self):
        self.cluster.state_path.write_text(json.dumps({'cluster': self.cluster.name,
                    'configSha256': self.cluster.config_hash, 'nodes': {'node-a': 'old-id'}}))
        with patch.object(self.cluster, 'docker_nodes', return_value={'node-a': 'new-id'}):
            with self.assertRaisesRegex(RuntimeError, 'identities changed'):
                self.cluster.owned_state()

    def test_changed_config_never_implicitly_upgrades_cluster(self):
        self.cluster.state_path.write_text(json.dumps({'cluster': self.cluster.name,
                    'configSha256': 'another-config', 'nodes': {'node-a': 'old-id'}}))
        with self.assertRaisesRegex(RuntimeError, 'no automatic recreation/upgrade'):
            self.cluster.owned_state()

    def test_runner_rejects_non_loopback_publication(self):
        runner = Path(__file__).resolve().parents[2] / '.agent/run.sh'
        for mapping in ['8443:8443', '0.0.0.0:8443:8443', '127.0.0.1:8443:8443/udp']:
            result = subprocess.run(['bash', str(runner), '--publish', mapping, 'true'], text=True, capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('explicit loopback', result.stderr)


if __name__ == '__main__':
    unittest.main()
