"""Protect the ELK rehearsal's capacity and disposable-resource boundaries.

These tests do not start Docker services or replace ingestion/recovery evidence.
"""
from copy import deepcopy
import importlib.util
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'smoke-logging.py'
SPEC = importlib.util.spec_from_file_location('logging_smoke', SCRIPT)
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)
GIB = 1024 ** 3


class CapacityGuards(unittest.TestCase):
    def setUp(self):
        self.arguments = {
            'info': {'OSType': 'linux', 'MemTotal': 12 * GIB},
            'available_memory': 10 * GIB,
            'map_count': 262144,
            'free_disk': 20 * GIB,
            'limits': [4 * GIB, 2 * GIB, 2 * GIB],
        }

    def test_documented_capacity_boundary_is_accepted(self):
        self.assertEqual(smoke.capacity_issues(**self.arguments), [])

    def test_eight_gib_daemon_is_rejected_before_workload_start(self):
        self.arguments['info']['MemTotal'] = 8 * GIB
        self.arguments['available_memory'] = 7 * GIB
        issues = smoke.capacity_issues(**self.arguments)
        self.assertTrue(any('total memory' in issue for issue in issues))
        self.assertTrue(any('available VM memory' in issue for issue in issues))

    def test_all_capacity_failures_are_reported_together(self):
        self.arguments.update(info={'OSType': 'windows', 'MemTotal': 8 * GIB},
                              available_memory=7 * GIB, map_count=262143,
                              free_disk=20 * GIB - 1)
        self.assertEqual(len(smoke.capacity_issues(**self.arguments)), 5)

    def test_larger_configured_limits_increase_memory_requirement(self):
        self.arguments.update(limits=[8 * GIB, 2 * GIB, 2 * GIB],
                              available_memory=14 * GIB)
        issues = smoke.capacity_issues(**self.arguments)
        self.assertTrue(any('15.0 GiB total memory' in issue for issue in issues))
        self.arguments['info']['MemTotal'] = 15 * GIB
        self.assertEqual(smoke.capacity_issues(**self.arguments), [])


class DisposableResourceGuards(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.fixture = self.root / 'fixture'
        self.fixture.mkdir()
        self.secret = self.fixture / 'elastic_password'
        self.secret.write_text('test-only')
        self.project = 'logging-smoke-abcdef012345'
        self.config = {
            'name': self.project,
            'networks': {'logging': {'name': self.project + '_logging', 'internal': True}},
            'volumes': {'es-data': {'name': self.project + '_es-data'}},
            'secrets': {'elastic_password': {'file': str(self.secret)}},
            'services': {
                'elasticsearch': {
                    'networks': {'logging': None},
                    'volumes': [
                        {'type': 'bind', 'source': str(self.fixture), 'target': '/fixture'},
                        {'type': 'volume', 'source': 'es-data', 'target': '/data'},
                    ],
                },
            },
        }

    def assert_rejected(self, config):
        with self.assertRaises(ValueError):
            smoke.guard_config(self.project, config, self.fixture)

    def test_private_project_resources_and_fixture_mounts_are_accepted(self):
        smoke.guard_config(self.project, self.config, self.fixture)

    def test_existing_resource_names_and_external_resources_are_rejected(self):
        for kind, name in [('volumes', 'es-data'), ('networks', 'logging')]:
            for override in [{'external': True}, {'name': 'production-shared'}]:
                with self.subTest(kind=kind, override=override):
                    config = deepcopy(self.config)
                    config[kind][name].update(override)
                    self.assert_rejected(config)

    def test_another_project_identity_is_rejected(self):
        self.config['name'] = 'production-logging'
        self.assert_rejected(self.config)

    def test_published_ports_privileged_and_host_network_are_rejected(self):
        for override in [{'ports': [{'target': 9200, 'published': '9200'}]},
                         {'privileged': True}, {'network_mode': 'host'}]:
            with self.subTest(override=override):
                config = deepcopy(self.config)
                config['services']['elasticsearch'].update(override)
                self.assert_rejected(config)

    def test_outside_and_prefix_similar_bind_sources_are_rejected(self):
        for path in [self.root / 'production', self.root / 'fixture-other']:
            with self.subTest(path=path):
                config = deepcopy(self.config)
                config['services']['elasticsearch']['volumes'][0]['source'] = str(path)
                self.assert_rejected(config)

    def test_secret_cannot_use_external_or_outside_file(self):
        for secret in [{'external': True, 'file': str(self.secret)},
                       {'file': str(self.root / 'production-password')}]:
            with self.subTest(secret=secret):
                config = deepcopy(self.config)
                config['secrets']['elastic_password'] = secret
                self.assert_rejected(config)

    def test_parent_traversal_in_bind_and_secret_paths_is_rejected(self):
        escaped = str(self.fixture / '..' / 'production')
        for resource in ['bind', 'secret']:
            with self.subTest(resource=resource):
                config = deepcopy(self.config)
                if resource == 'bind':
                    config['services']['elasticsearch']['volumes'][0]['source'] = escaped
                else:
                    config['secrets']['elastic_password']['file'] = escaped
                self.assert_rejected(config)

    def test_symlink_to_outside_fixture_is_rejected(self):
        outside = self.root / 'production-password'
        outside.write_text('test-only')
        link = self.fixture / 'external-link'
        link.symlink_to(outside)
        for resource in ['bind', 'secret']:
            with self.subTest(resource=resource):
                config = deepcopy(self.config)
                if resource == 'bind':
                    config['services']['elasticsearch']['volumes'][0]['source'] = str(link)
                else:
                    config['secrets']['elastic_password']['file'] = str(link)
                self.assert_rejected(config)

    def test_volume_driver_cannot_hide_a_shared_host_bind(self):
        self.config['volumes']['es-data']['driver_opts'] = {
            'type': 'none', 'o': 'bind', 'device': str(self.root / 'production-data')}
        self.assert_rejected(self.config)

    def test_only_local_volumes_and_internal_bridge_networks_are_allowed(self):
        for kind, name, override in [
            ('volumes', 'es-data', {'driver': 'shared-storage-plugin'}),
            ('networks', 'logging', {'driver': 'host'}),
            ('networks', 'logging', {'internal': False}),
            ('networks', 'logging', {'driver_opts': {'com.docker.network.bridge.name': 'production'}}),
        ]:
            with self.subTest(kind=kind, override=override):
                config = deepcopy(self.config)
                config[kind][name].update(override)
                self.assert_rejected(config)

    def test_service_cannot_reference_an_undeclared_volume(self):
        self.config['services']['elasticsearch']['volumes'][1]['source'] = 'production-data'
        self.assert_rejected(self.config)


if __name__ == '__main__':
    unittest.main()
