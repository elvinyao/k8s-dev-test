"""Keep destructive restore checks and TLS rejection evidence fail-closed."""

import importlib.util
from pathlib import Path
import ssl
import sys
import unittest
from unittest import mock


DIRECTORY = Path(__file__).resolve().parents[2] / "compose/logging/scripts"
sys.path.insert(0, str(DIRECTORY))
try:
    spec = importlib.util.spec_from_file_location("logging_runtime_probe", DIRECTORY / "runtime-probe.py")
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
finally:
    sys.path.remove(str(DIRECTORY))


class LoggingRuntimeProbeTests(unittest.TestCase):
    def test_restore_refuses_existing_index_alias_or_stream_before_repository_mutation(self):
        for kind in ("indices", "aliases", "data_streams"):
            with self.subTest(kind=kind), mock.patch.object(probe, "request", return_value={kind: [{}]}) as request:
                with self.assertRaisesRegex(probe.ProbeError, "already contains"):
                    probe.restore("platform-snapshot", "abcdef012345")
                request.assert_called_once_with("GET", "/_resolve/index/platform-logs-*?expand_wildcards=all")

    def test_restore_registers_readonly_and_refuses_snapshot_without_count_evidence(self):
        responses = [{}, {}, {probe.REPOSITORY: {"settings": {"readonly": "true"}}},
                     {"snapshots": [{"state": "SUCCESS", "metadata": {}}]}]
        with mock.patch.object(probe, "request", side_effect=responses) as request, \
                mock.patch.object(probe.admin, "restore_logs") as restore:
            with self.assertRaisesRegex(probe.ProbeError, "count evidence"):
                probe.restore("platform-snapshot", "abcdef012345")
            restore.assert_not_called()
            self.assertTrue(request.call_args_list[1].args[2]["settings"]["readonly"])

    def test_restore_count_mismatch_cannot_be_reported_as_passed(self):
        responses = [{}, {}, {probe.REPOSITORY: {"settings": {"readonly": True}}},
                     {"snapshots": [{"state": "SUCCESS", "metadata": {
                         "purpose": "platform logging isolated runtime smoke", "expected_log_documents": 3}}]},
                     {"count": 2}]
        with mock.patch.object(probe, "request", side_effect=responses), \
                mock.patch.object(probe.admin, "restore_logs"), \
                mock.patch.object(probe, "marker_counts", return_value={"http": 1, "beats": 1}):
            with self.assertRaisesRegex(probe.ProbeError, "count differs"):
                probe.restore("platform-snapshot", "abcdef012345")

    def test_tls_negative_probe_does_not_accept_server_certificate_failure(self):
        context = mock.MagicMock()
        context.wrap_socket.return_value.__enter__.return_value.version.return_value = "TLSv1.2"
        failure = ssl.SSLError("server certificate error")
        failure.reason = "CERTIFICATE_VERIFY_FAILED"
        bad_context = mock.MagicMock()
        bad_context.wrap_socket.side_effect = failure
        with mock.patch.object(probe.socket, "create_connection"), \
                mock.patch.object(probe, "tls_context", side_effect=[context, bad_context]):
            with self.assertRaisesRegex(probe.ProbeError, "without a client-authentication"):
                probe.beats_tls()

    def test_tls_negative_probe_fails_when_no_certificate_is_accepted(self):
        context = mock.MagicMock()
        context.wrap_socket.return_value.__enter__.return_value.version.return_value = "TLSv1.2"
        with mock.patch.object(probe.socket, "create_connection"), \
                mock.patch.object(probe, "tls_context", return_value=context):
            with self.assertRaisesRegex(probe.ProbeError, "without a client certificate"):
                probe.beats_tls()

    def test_snapshot_partial_state_is_not_success(self):
        with mock.patch.object(probe, "request", side_effect=[{}, {"count": 3}, {
            "snapshot": {"state": "PARTIAL", "shards": {"failed": 1}}},
        ]):
            with self.assertRaisesRegex(probe.ProbeError, "did not complete"):
                probe.snapshot()


if __name__ == "__main__":
    unittest.main()
