"""Catch the cross-component regressions found in the initial examples."""
import importlib.util
from pathlib import Path
import sys
import unittest

import yaml

sys.path.insert(0, str(Path(__file__).parents[1]))
spec = importlib.util.spec_from_file_location('integration', Path(__file__).parents[1] / 'validate-integration.py')
integration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(integration)


class IntegrationTests(unittest.TestCase):
    def test_cross_namespace_chart_resource_requires_project_destination(self):
        project = {'spec': {'destinations': [{'namespace': 'monitoring'}], 'clusterResourceWhitelist': []}}
        service = {'apiVersion': 'v1', 'kind': 'Service',
                   'metadata': {'name': 'coredns', 'namespace': 'kube-system'}}
        with self.assertRaisesRegex(ValueError, 'Project denies kube-system'):
            integration.check_permissions(project, [service], 'monitoring', {})
        project['spec']['destinations'].append({'namespace': 'kube-system'})
        integration.check_permissions(project, [service], 'monitoring', {})

    def test_an_additive_allow_all_policy_invalidates_the_server_restriction(self):
        labels = {'app.kubernetes.io/name': 'argocd-server'}
        restricted = {'kind': 'NetworkPolicy', 'metadata': {'namespace': 'argocd'}, 'spec': {
            'podSelector': {'matchLabels': labels}, 'policyTypes': ['Ingress'], 'ingress': [
                {'from': [{'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': namespace}}}],
                 'ports': [{'port': port}]} for namespace, port in [('gateway-system', 8080), ('monitoring', 8083)]]}}
        integration.check_server_ingress([restricted], labels)
        broad = {'kind': 'NetworkPolicy', 'metadata': {'namespace': 'argocd'},
                 'spec': {'podSelector': {}, 'ingress': [{}]}}
        with self.assertRaisesRegex(ValueError, 'unrestricted'):
            integration.check_server_ingress([restricted, broad], labels)

    def test_rails_registry_must_be_disabled_when_deployment_is_absent(self):
        documents = [{'kind': 'ConfigMap', 'data': {'gitlab.yml.erb': '  registry:\n    enabled: true\n'}}] * 3
        with self.assertRaisesRegex(ValueError, 'Rails Registry'):
            integration.check_registry(documents, False)


class GitLabDependencyTests(unittest.TestCase):
    def setUp(self):
        self.contract = yaml.safe_load((integration.ROOT / 'platform/gitlab/required-secrets.yaml').read_text())
        self.pod = {'containers': [{'name': 'gitlab'}], 'volumes': [
            {'name': 'custom-ca', 'projected': {'sources': [
                {'secret': {'name': 'gitlab-backend-ca', 'items': [{'key': 'backend-ca.crt', 'path': 'ca.crt'}]}}
            ]}},
        ]}
        self.documents = [{'kind': 'ConfigMap', 'data': {
            'gitlab.yml.erb': '  gitaly_address: tls://gitaly.example.invalid:8076\n'}} for _ in range(4)]
        self.documents.append({'kind': 'Deployment', 'spec': {'template': {'spec': self.pod}}})

    def check(self):
        integration.check_gitlab_dependencies(self.documents, self.contract)

    def test_internal_gitaly_certificate_regression_blocks_installation(self):
        self.check()
        self.pod['volumes'][0]['projected']['sources'].append({
            'secret': {'name': 'gitlab-gitaly-tls', 'items': [{'key': 'tls.crt', 'path': 'gitaly.crt'}]}})
        with self.assertRaisesRegex(ValueError, 'undocumented Secret gitlab-gitaly-tls'):
            self.check()

    def test_external_key_typo_cannot_pass_documented_secret_inventory(self):
        self.pod['volumes'][0]['projected']['sources'][0]['secret']['items'][0]['key'] = 'misspelled.crt'
        with self.assertRaisesRegex(ValueError, 'undocumented keys'):
            self.check()

    def test_disabling_internal_tls_cannot_disable_external_tls(self):
        self.documents[0]['data']['gitlab.yml.erb'] = '  gitaly_address: tcp://gitaly.example.invalid:8075\n'
        with self.assertRaisesRegex(ValueError, 'keep TLS enabled'):
            self.check()

    def test_optional_secret_is_not_mistaken_for_install_dependency(self):
        self.pod['volumes'].append({'name': 'optional', 'secret': {
            'secretName': 'gitlab-unused-feature', 'optional': True}})
        self.check()

    def test_required_secret_checked_in_job_and_cronjob(self):
        self.pod['initContainers'] = [{'name': 'init', 'env': [{'name': 'PASSWORD', 'valueFrom': {
            'secretKeyRef': {'name': 'forgotten-password', 'key': 'password'}}}]}]
        for kind in ('Job', 'CronJob'):
            with self.subTest(kind=kind):
                job_spec = {'template': {'spec': self.pod}}
                self.documents[-1] = {'kind': kind, 'spec': job_spec if kind == 'Job' else {
                    'jobTemplate': {'spec': job_spec}}}
                with self.assertRaisesRegex(ValueError, 'undocumented Secret forgotten-password'):
                    self.check()

    def test_envfrom_and_imagepull_secrets_must_also_be_supplied(self):
        for change in ({'envFrom': [{'secretRef': {'name': 'missing-env'}}]},
                       {'env': [{'name': 'TOKEN', 'valueFrom': {
                           'secretKeyRef': {'name': 'missing-env', 'key': 'token'}}}]}):
            with self.subTest(change=change):
                self.pod['containers'][0] = {'name': 'gitlab', **change}
                with self.assertRaisesRegex(ValueError, 'undocumented Secret missing-env'):
                    self.check()
        self.pod['containers'][0] = {'name': 'gitlab'}
        self.pod['imagePullSecrets'] = [{'name': 'missing-registry-login'}]
        with self.assertRaisesRegex(ValueError, 'undocumented Secret missing-registry-login'):
            self.check()

    def test_registry_storage_requires_explicit_overlay_contract(self):
        self.pod['volumes'].append({'name': 'registry', 'secret': {
            'secretName': 'gitlab-registry-storage', 'items': [{'key': 'config', 'path': 'config'}]}})
        with self.assertRaisesRegex(ValueError, 'undocumented Secret gitlab-registry-storage'):
            self.check()
        integration.check_gitlab_dependencies(self.documents, self.contract, registry_enabled=True)


if __name__ == '__main__':
    unittest.main()
