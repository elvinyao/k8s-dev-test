"""Reject stale sync results and failures unrelated to AppProject enforcement."""
import copy
import importlib.util
from pathlib import Path
import sys
import unittest


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location('gitops_smoke', SCRIPTS / 'smoke-gitops.py')
gitops_smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gitops_smoke)

REVISION = 'a' * 40
TOKEN = 'current-validation-request'


def application_state():
    return {'status': {'operationState': {
        'operation': {'info': [{'name': 'validation-run', 'value': TOKEN}]},
        'phase': 'Succeeded', 'syncResult': {'revision': REVISION},
    }}}


def denial_state(group='', kind='Secret', name='forbidden-gitops-secret', namespace='apps-prod'):
    return {'phase': 'Failed', 'syncResult': {'revision': REVISION, 'resources': [{
        'group': group, 'kind': kind, 'name': name, 'namespace': namespace,
        'status': 'SyncFailed',
        'message': f'resource {group}:{kind} is not permitted in project platform-apps-production',
    }]}}


class OperationCorrelation(unittest.TestCase):
    def test_current_uuid_and_revision_return_the_actual_state(self):
        app = application_state()
        self.assertIs(gitops_smoke.current_operation(app, TOKEN, REVISION), app['status']['operationState'])

    def test_same_revision_with_old_uuid_does_not_reuse_previous_success(self):
        app = application_state()
        self.assertIsNone(gitops_smoke.current_operation(app, 'new-validation-request', REVISION))

    def test_matching_uuid_with_wrong_revision_is_not_current(self):
        self.assertIsNone(gitops_smoke.current_operation(application_state(), TOKEN, 'b' * 40))

    def test_missing_or_unrelated_operation_info_is_not_current(self):
        for app in [{}, {'status': {}}, {'status': {'operationState': {}}}]:
            with self.subTest(app=app):
                self.assertIsNone(gitops_smoke.current_operation(app, TOKEN, REVISION))
        app = application_state()
        app['status']['operationState']['operation']['info'][0]['name'] = 'another-info-field'
        self.assertIsNone(gitops_smoke.current_operation(app, TOKEN, REVISION))


class ProjectDenialEvidence(unittest.TestCase):
    identity = ('', 'Secret', 'forbidden-gitops-secret', 'apps-prod')

    def assert_denied_evidence_rejected(self, state):
        with self.assertRaises(RuntimeError):
            gitops_smoke.require_project_denial(state, *self.identity)

    def test_specific_secret_and_clusterrole_denials_are_accepted(self):
        for identity in [self.identity, ('rbac.authorization.k8s.io', 'ClusterRole',
                                        'forbidden-gitops-clusterrole', 'apps-prod')]:
            state = denial_state(*identity)
            with self.subTest(identity=identity):
                self.assertIs(gitops_smoke.require_project_denial(state, *identity),
                              state['syncResult']['resources'][0])

    def test_failed_repository_fetch_does_not_prove_a_project_denial(self):
        state = {'phase': 'Failed', 'message': 'Unable to generate manifests: repository unavailable',
                 'syncResult': {'revision': REVISION}}
        self.assert_denied_evidence_rejected(state)
        state = denial_state()
        state['syncResult']['resources'][0]['message'] = 'repository unavailable'
        self.assert_denied_evidence_rejected(state)

    def test_other_resource_or_namespace_cannot_prove_the_expected_denial(self):
        for key, value in [('group', 'another.example'), ('kind', 'ConfigMap'),
                           ('name', 'another-secret'), ('namespace', 'argocd')]:
            state = denial_state()
            state['syncResult']['resources'][0][key] = value
            with self.subTest(field=key):
                self.assert_denied_evidence_rejected(state)

    def test_resource_result_must_be_sync_failed(self):
        for status in ['Synced', 'Pruned', 'SyncFailedDueToUnrelatedCause', None]:
            state = denial_state()
            state['syncResult']['resources'][0]['status'] = status
            with self.subTest(status=status):
                self.assert_denied_evidence_rejected(state)

    def test_operation_must_be_failed(self):
        for phase in ['Succeeded', 'Error', 'Running', None]:
            state = denial_state()
            state['phase'] = phase
            with self.subTest(phase=phase):
                self.assert_denied_evidence_rejected(state)

    def test_another_projects_denial_does_not_prove_this_project_enforces_it(self):
        state = denial_state()
        state['syncResult']['resources'][0]['message'] = 'resource :Secret is not permitted in project default'
        self.assert_denied_evidence_rejected(state)

    def test_duplicate_results_are_ambiguous(self):
        state = denial_state()
        state['syncResult']['resources'].append(copy.deepcopy(state['syncResult']['resources'][0]))
        self.assert_denied_evidence_rejected(state)


if __name__ == '__main__':
    unittest.main()
