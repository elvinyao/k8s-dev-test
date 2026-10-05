"""Validate real business manifests and negative Project authorization examples."""
import copy
import importlib.util
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('business_contract', ROOT / 'apps/production-demo/check.py')
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)


class BusinessGitOpsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project = yaml.safe_load((ROOT / 'argocd-app-settings/project-production.yaml.example').read_text())
        cls.application = yaml.safe_load((ROOT / 'argocd-app-settings/production-demo.yaml.example').read_text())
        cls.objects = contract.render()

    def check(self, objects=None, application=None, project=None):
        contract.validate_contract(project or self.project, application or self.application,
                                   objects or self.objects, allow_placeholders=True)

    def test_real_render_is_allowed_and_service_selects_ready_workload(self):
        self.check()
        by_kind = {obj['kind']: obj for obj in self.objects}
        self.assertEqual(set(by_kind), {'ConfigMap', 'Deployment', 'Service', 'PodDisruptionBudget'})
        pod = by_kind['Deployment']['spec']['template']
        self.assertEqual(by_kind['Service']['spec']['selector'], pod['metadata']['labels'])
        container = pod['spec']['containers'][0]
        self.assertEqual(by_kind['Service']['spec']['ports'][0]['targetPort'], container['ports'][0]['name'])
        content_name = pod['spec']['volumes'][0]['configMap']['name']
        self.assertEqual(content_name, by_kind['ConfigMap']['metadata']['name'])
        self.assertFalse(pod['spec']['automountServiceAccountToken'])
        self.assertTrue(pod['spec']['securityContext']['runAsNonRoot'])
        self.assertGreater(pod['spec']['securityContext']['runAsUser'], 0)
        self.assertEqual(pod['spec']['securityContext']['seccompProfile']['type'], 'RuntimeDefault')
        self.assertFalse(container['securityContext']['allowPrivilegeEscalation'])
        self.assertTrue(container['securityContext']['readOnlyRootFilesystem'])
        self.assertEqual(container['securityContext']['capabilities']['drop'], ['ALL'])
        self.assertRegex(container['image'], r'@sha256:[0-9a-f]{64}$')

    def test_privileged_kinds_remain_denied(self):
        forbidden = [('', 'PersistentVolume'), ('', 'Namespace'),
                     ('storage.k8s.io', 'StorageClass'),
                     ('rbac.authorization.k8s.io', 'ClusterRole'),
                     ('rbac.authorization.k8s.io', 'Role'),
                     ('rbac.authorization.k8s.io', 'RoleBinding'),
                     ('', 'Secret'), ('', 'Pod'),
                     ('networking.k8s.io', 'NetworkPolicy')]
        for group, kind in forbidden:
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'Project denies'):
                self.check([{'apiVersion': f'{group}/v1' if group else 'v1', 'kind': kind,
                             'metadata': {'name': 'forbidden', 'namespace': 'apps-prod'}}])

    def test_cross_namespace_resource_and_application_are_denied(self):
        objects = copy.deepcopy(self.objects)
        objects[0]['metadata']['namespace'] = 'argocd'
        with self.assertRaisesRegex(ValueError, 'resource namespace'):
            self.check(objects)
        application = copy.deepcopy(self.application)
        application['spec']['destination']['namespace'] = 'argocd'
        with self.assertRaisesRegex(ValueError, 'destination'):
            self.check(application=application)

    def test_other_repository_and_floating_revision_are_denied(self):
        application = copy.deepcopy(self.application)
        application['spec']['source']['repoURL'] = 'https://other.example.invalid/config.git'
        with self.assertRaisesRegex(ValueError, 'source repository'):
            self.check(application=application)
        application = copy.deepcopy(self.application)
        application['spec']['source']['targetRevision'] = 'main'
        with self.assertRaisesRegex(ValueError, 'commit SHA'):
            self.check(application=application)

    def test_example_is_rejected_for_deployment_validation(self):
        with self.assertRaisesRegex(ValueError, 'Replace the example'):
            contract.validate_contract(self.project, self.application, self.objects)

    def test_admin_network_policy_stays_outside_business_application(self):
        policy = yaml.safe_load((ROOT / 'apps/production-demo/admin-networkpolicy.yaml.example').read_text())
        with self.assertRaisesRegex(ValueError, 'Project denies namespaced resource'):
            self.check([policy])


if __name__ == '__main__':
    unittest.main()
