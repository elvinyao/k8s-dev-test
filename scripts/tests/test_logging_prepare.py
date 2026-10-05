"""Exercise real TLS initialization in disposable container files, never live ELK data."""

from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[2] / "compose/logging/scripts"


def load(name):
    spec = importlib.util.spec_from_file_location("logging_" + name, SCRIPTS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prepare = load("prepare")
admin = load("admin")


@unittest.skipUnless(os.geteuid() == 0 and shutil.which("openssl"), "requires the repository Docker runner")
class LoggingPrepareTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="logging-tls-test-")
        cls.base = Path(cls.temp.name)
        cls.base.chmod(0o755)
        cls.runtime = cls.base / "runtime"
        with redirect_stdout(io.StringIO()):
            prepare.generate(cls.runtime, "kibana.example.test", "logs.example.test")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def run_openssl(self, *args):
        result = subprocess.run(["openssl", *map(str, args)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        return result.stdout

    def test_certificates_have_valid_chain_names_and_key_pairs(self):
        for service, host in (("elasticsearch", "elasticsearch"),
                              ("kibana", "kibana.example.test"),
                              ("logstash", "logs.example.test"),
                              ("collector", "platform-collector")):
            with self.subTest(service=service):
                directory = self.runtime / "tls" / service
                stem = "client" if service == "collector" else "server"
                purpose = "sslclient" if service == "collector" else "sslserver"
                cert = directory / (stem + ".crt")
                key = directory / (stem + ".key")
                self.run_openssl("verify", "-CAfile", directory / "ca.crt",
                                 "-verify_hostname", host, "-purpose", purpose, cert)
                self.run_openssl("x509", "-in", cert, "-checkend", "86400", "-noout")
                cert_pub = self.run_openssl("x509", "-in", cert, "-pubkey", "-noout")
                key_pub = self.run_openssl("pkey", "-in", key, "-pubout")
                self.assertEqual(cert_pub, key_pub)
                self.assertTrue(key.read_bytes().startswith(b"-----BEGIN PRIVATE KEY-----"))

    def test_service_uid_reads_its_secrets_but_cannot_read_ca_private_key(self):
        # UID 1000/GID 0 is declared by Compose. Drop supplementary groups too.
        script = """
import os, pathlib, sys
os.setgroups([])
os.setgid(0)
os.setuid(1000)
root = pathlib.Path(sys.argv[1])
for name in ('secrets/logstash_password', 'tls/logstash/server.key', 'tls/public/ca.crt'):
    assert (root / name).read_bytes()
try:
    (root / 'ca-private/ca.key').read_bytes()
except PermissionError:
    pass
else:
    raise SystemExit('service UID can read CA private key')
probe = root / 'snapshots/write-probe'
probe.write_text('probe')
probe.unlink()
"""
        result = subprocess.run([sys.executable, "-c", script, str(self.runtime)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_unrelated_uid_cannot_read_secrets(self):
        script = """
import os, pathlib, sys
os.setgroups([])
os.setgid(65534)
os.setuid(65534)
try:
    pathlib.Path(sys.argv[1]).read_bytes()
except PermissionError:
    pass
else:
    raise SystemExit('unrelated UID can read secret')
"""
        result = subprocess.run([sys.executable, "-c", script,
                                 str(self.runtime / "secrets/logstash_password")], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())

    def test_repeated_initialization_preserves_all_credentials(self):
        def fingerprints():
            return {str(path.relative_to(self.runtime)): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in self.runtime.rglob("*") if path.is_file()}
        original = fingerprints()
        with redirect_stdout(io.StringIO()):
            prepare.generate(self.runtime, "kibana.example.test", "logs.example.test")
        self.assertEqual(original, fingerprints())

    def test_host_change_does_not_rotate_credentials(self):
        with self.assertRaisesRegex(SystemExit, "Refusing to replace keys"):
            prepare.generate(self.runtime, "changed.example.test", "logs.example.test")

    def test_missing_snapshot_directory_is_incomplete(self):
        target = self.base / "incomplete-runtime"
        shutil.copytree(self.runtime, target)
        (target / "snapshots").rmdir()
        with self.assertRaisesRegex(SystemExit, "missing files"):
            prepare.generate(target, "kibana.example.test", "logs.example.test")

    def test_openssl_failure_cleans_only_new_staging_files(self):
        target = self.base / "failed-runtime"
        with mock.patch.object(prepare, "run", side_effect=subprocess.CalledProcessError(1, "openssl")):
            with self.assertRaises(subprocess.CalledProcessError):
                prepare.generate(target, "kibana.example.test", "logs.example.test")
        self.assertFalse(target.exists())
        self.assertFalse(list(self.base.glob(".logging-prepare-*")))
        self.assertTrue((self.runtime / "prepared.json").is_file())


class LoggingRestoreTests(unittest.TestCase):
    def test_existing_closed_index_alias_or_stream_prevents_restore_request(self):
        for kind in ("indices", "aliases", "data_streams"):
            with self.subTest(kind=kind), mock.patch.object(admin, "request", return_value={kind: [{}]}) as request:
                with self.assertRaisesRegex(SystemExit, "prefix is already in use"):
                    admin.restore_logs("backup", "drill-test")
                request.assert_called_once_with("GET", "/_resolve/index/drill-test-*?expand_wildcards=all")

    def test_missing_zero_or_failed_shards_are_not_reported_as_success(self):
        for response in ({}, {"snapshot": {"shards": {"total": 0, "failed": 0, "successful": 0}}},
                         {"snapshot": {"shards": {"total": 2, "failed": 1, "successful": 1}}}):
            with self.subTest(response=response), mock.patch.object(admin, "request", side_effect=[{}, response]):
                with redirect_stdout(io.StringIO()), self.assertRaisesRegex(SystemExit, "completion was not proven"):
                    admin.restore_logs("backup", "drill-test")

    def test_successful_restore_is_scoped_and_omits_system_state(self):
        response = {"snapshot": {"shards": {"total": 2, "failed": 0, "successful": 2}}}
        with mock.patch.object(admin, "request", side_effect=[{}, response]) as request:
            with redirect_stdout(io.StringIO()):
                admin.restore_logs("backup", "drill-test")
            body = request.call_args.args[2]
            self.assertEqual(body["indices"], "platform-logs-*")
            self.assertEqual(body["rename_replacement"], "drill-test-$1")
            self.assertFalse(body["include_global_state"])
            self.assertFalse(body["include_aliases"])
            self.assertEqual(body["feature_states"], ["none"])
            self.assertEqual(body["ignore_index_settings"], ["index.lifecycle.name"])


if __name__ == "__main__":
    unittest.main()
