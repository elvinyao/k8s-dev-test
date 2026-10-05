"""Regression checks for read-only prerequisites; no cluster or Docker daemon use."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'preflight-production.py'
SPEC = importlib.util.spec_from_file_location('preflight_production', SCRIPT)
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)


def worker(name):
    return {
        'metadata': {'name': name, 'labels': {'kubernetes.io/os': 'linux',
                                            'kubernetes.io/hostname': name}},
        'spec': {}, 'status': {'conditions': [{'type': 'Ready', 'status': 'True'}]},
    }


def csi_node(name, driver='ebs.csi.aws.com', node_id='worker-id'):
    return {'metadata': {'name': name}, 'spec': {'drivers': [{'name': driver, 'nodeID': node_id}]}}


class NodeChecks(unittest.TestCase):
    def test_ready_is_not_enough_to_count_a_worker(self):
        cases = {
            'cordoned': lambda n: n['spec'].update(unschedulable=True),
            'control-plane': lambda n: n['metadata']['labels'].update({'node-role.kubernetes.io/control-plane': ''}),
            'master': lambda n: n['metadata']['labels'].update({'node-role.kubernetes.io/master': ''}),
            'tainted': lambda n: n['spec'].update(taints=[{'key': 'dedicated', 'effect': 'NoSchedule'}]),
            'soft-taint': lambda n: n['spec'].update(taints=[{'key': 'dedicated', 'effect': 'PreferNoSchedule'}]),
            'windows': lambda n: n['metadata']['labels'].update({'kubernetes.io/os': 'windows'}),
            'pressure': lambda n: n['status']['conditions'].append({'type': 'DiskPressure', 'status': 'True'}),
            'not-ready': lambda n: n['status'].update(conditions=[{'type': 'Ready', 'status': 'False'}]),
            'missing-hostname': lambda n: n['metadata']['labels'].pop('kubernetes.io/hostname'),
        }
        for description, mutate in cases.items():
            with self.subTest(description=description):
                node = worker('node-a')
                mutate(node)
                eligible, excluded = preflight.eligible_workers([node])
                self.assertEqual(eligible, [])
                self.assertEqual(len(excluded), 1)

    def test_duplicate_hostname_does_not_satisfy_anti_affinity(self):
        nodes = [worker(name) for name in ['node-a', 'node-b', 'node-c']]
        nodes[2]['metadata']['labels']['kubernetes.io/hostname'] = 'node-b'
        eligible, excluded = preflight.eligible_workers(nodes)
        self.assertEqual(len(eligible), 2)
        self.assertIn('duplicate hostname', excluded[0])

    def test_three_distinct_workers_are_eligible(self):
        nodes = [worker(name) for name in ['node-a', 'node-b', 'node-c']]
        self.assertEqual(preflight.eligible_workers(nodes), (nodes, []))


class StorageChecks(unittest.TestCase):
    def setUp(self):
        self.workers = [worker(name) for name in ['node-a', 'node-b', 'node-c']]
        self.storage = {'provisioner': 'ebs.csi.aws.com', 'reclaimPolicy': 'Retain'}
        self.driver = {'metadata': {'name': 'ebs.csi.aws.com'}}
        self.registrations = {'items': [csi_node(n['metadata']['name']) for n in self.workers]}

    def get(self, *resource):
        return {('get', 'storageclass', 'production-rwo'): self.storage,
                ('get', 'csidriver', 'ebs.csi.aws.com'): self.driver,
                ('get', 'csinodes'): self.registrations}[resource]

    def errors(self, expected=None):
        return preflight.storage_errors(self.get, 'production-rwo', self.workers, expected)

    def test_missing_empty_local_or_legacy_provisioner_fails(self):
        for value in (None, '', ' ', 'rancher.io/local-path', 'kubernetes.io/no-provisioner',
                      'kubernetes.io/aws-ebs', 'hostpath.csi.k8s.io', 'local.csi.openebs.io'):
            with self.subTest(provisioner=value):
                self.storage['provisioner'] = value
                self.assertIn('explicit, non-local CSI', ' '.join(self.errors()))

    def test_registered_driver_on_eligible_workers_passes(self):
        self.assertEqual(self.errors('ebs.csi.aws.com'), [])

    def test_explicit_driver_contract_mismatch_fails(self):
        self.assertIn('--expected-csi-driver', ' '.join(self.errors('disk.csi.azure.com')))

    def test_csi_driver_identity_mismatch_fails(self):
        self.driver['metadata']['name'] = 'another.csi.driver'
        self.assertIn('registered CSIDriver', ' '.join(self.errors()))

    def test_registrations_on_other_nodes_do_not_count(self):
        self.registrations['items'][2] = csi_node('control-plane')
        self.assertIn('3 eligible workers', ' '.join(self.errors()))

    def test_empty_node_id_or_wrong_driver_does_not_count(self):
        for registration in (csi_node('node-c', node_id=''), csi_node('node-c', driver='other.csi.driver')):
            with self.subTest(registration=registration):
                self.registrations['items'][2] = registration
                self.assertIn('nonempty nodeID', ' '.join(self.errors()))

    def test_delete_reclaim_policy_fails(self):
        self.storage['reclaimPolicy'] = 'Delete'
        self.assertIn('Retain', ' '.join(self.errors()))


class OfflineChecks(unittest.TestCase):
    def paths(self, component):
        return {str(p.relative_to(preflight.ROOT)) for p in preflight.input_paths(component)}

    def test_monitoring_checks_route_certificate_and_alert_delivery_inputs(self):
        self.assertTrue({
            'platform/networking/production/routes.yaml',
            'platform/security/public-certificate.yaml.example',
            'platform/security/clusterissuer-cloudflare.yaml.example',
            'platform/monitoring/alertmanager.yaml.example',
        } <= self.paths('monitoring'))

    def test_registry_preflight_includes_its_own_overlay_placeholders(self):
        paths = {str(p.relative_to(preflight.ROOT))
                 for p in preflight.input_paths('gitlab', include_registry=True)}
        self.assertIn('platform/gitlab/values-registry.yaml.example', paths)
        self.assertNotIn('platform/gitlab/values-registry.yaml.example', self.paths('gitlab'))

    def test_logging_checks_operator_and_production_custom_resources(self):
        paths = self.paths('logging')
        self.assertTrue({
            'platform/logging/operator-values.yaml',
            'platform/logging/kubernetes/base/elasticsearch.yaml',
            'platform/logging/kubernetes/base/certificates.yaml',
            'platform/logging/kubernetes/production/kustomization.yaml',
            'platform/logging/kubernetes/operations/index-template.production.json',
        } <= paths)
        self.assertNotIn('platform/logging/kubernetes/local/kustomization.yaml', paths)

    def test_placeholder_detection_reports_location_never_input_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'config.yaml'
            path.write_text('# ignored.example.invalid\nurl: https://custom.invalid/token-private\npassword: CHANGE_ME_private\n')
            errors = preflight.placeholder_errors([path], root)
            self.assertEqual(len(errors), 2)
            self.assertIn('config.yaml:2:', errors[0])
            self.assertNotIn('token-private', ' '.join(errors))
            self.assertNotIn('CHANGE_ME_private', ' '.join(errors))

    def test_missing_required_input_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertIn('unable to read', preflight.placeholder_errors([root / 'missing.yaml'], root)[0])


class MainChecks(unittest.TestCase):
    def run_main(self, args, responses=None):
        output = io.StringIO()
        errors = io.StringIO()
        with patch.object(preflight, 'input_paths', return_value=[]), \
                patch.object(preflight.subprocess, 'run', side_effect=responses) as run, \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            result = preflight.main(args)
        return result, output.getvalue(), errors.getvalue(), run

    def test_offline_never_contacts_cluster(self):
        result, output, errors, run = self.run_main(['--component', 'logging'])
        self.assertEqual(result, 0)
        self.assertIn('OFFLINE ONLY', output)
        self.assertEqual(errors, '')
        run.assert_not_called()

    def test_api_failure_does_not_expose_stdout_or_stderr(self):
        result, _, errors, run = self.run_main(['--context', 'production'], [
            subprocess.CompletedProcess([], 1, 'secret-stdout', 'secret-stderr'),
        ])
        self.assertEqual(result, 1)
        self.assertNotIn('secret-', errors)
        self.assertIn('context, RBAC', errors)
        self.assertEqual(run.call_args.args[0][:4], ['kubectl', '--context', 'production', '--request-timeout=20s'])

    def test_registry_live_check_reads_storage_secret_without_changing_base_contract(self):
        resources = [
            {'items': [worker(name) for name in ['node-a', 'node-b', 'node-c']]},
            {'provisioner': 'ebs.csi.aws.com', 'reclaimPolicy': 'Retain'},
            {'metadata': {'name': 'ebs.csi.aws.com'}},
            {'items': [csi_node(name) for name in ['node-a', 'node-b', 'node-c']]},
        ] + [{'data': {key: 'private-secret-value' for key in keys}}
             for keys in preflight.SECRETS['gitlab'].values()] + [{'data': {'config': 'private-registry-config'}}]
        result, output, errors, run = self.run_main([
            '--context', 'production', '--component', 'gitlab', '--gitlab-registry'], [
            subprocess.CompletedProcess([], 0, json.dumps(resource), '') for resource in resources])
        self.assertEqual(result, 0)
        self.assertEqual(errors, '')
        self.assertIn('gitlab-registry-storage', run.call_args_list[-1].args[0])
        self.assertNotIn('gitlab-registry-storage', preflight.SECRETS['gitlab'])
        self.assertNotIn('private-registry-config', output)

    def test_invalid_api_json_does_not_expose_response(self):
        result, _, errors, _ = self.run_main(['--context', 'production'], [
            subprocess.CompletedProcess([], 0, 'private-json-content', ''),
        ])
        self.assertEqual(result, 1)
        self.assertIn('Invalid JSON', errors)
        self.assertNotIn('private-json-content', errors)

    def test_live_healthy_checks_are_read_only_and_never_claim_readiness(self):
        resources = [
            {'items': [worker(name) for name in ['node-a', 'node-b', 'node-c']]},
            {'provisioner': 'ebs.csi.aws.com', 'reclaimPolicy': 'Retain'},
            {'metadata': {'name': 'ebs.csi.aws.com'}},
            {'items': [csi_node(name) for name in ['node-a', 'node-b', 'node-c']]},
            {'data': {key: 'private-secret-value' for key in preflight.SECRETS['logging']['logging-kibana-secure-settings']}},
        ]
        result, output, errors, run = self.run_main(['--context', 'production', '--component', 'logging'], [
            subprocess.CompletedProcess([], 0, json.dumps(resource), '') for resource in resources
        ])
        self.assertEqual(result, 0)
        self.assertEqual(errors, '')
        self.assertIn('not production readiness', output)
        self.assertNotIn('private-secret-value', output)
        for call in run.call_args_list:
            self.assertIn('get', call.args[0])
            self.assertEqual(call.args[0][-2:], ['-o', 'json'])


if __name__ == '__main__':
    unittest.main()
