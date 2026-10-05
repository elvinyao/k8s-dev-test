import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from release import proposal


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.env = {"CI_PIPELINE_SOURCE": "push", "CI_COMMIT_REF_PROTECTED": "true",
                    "CI_DEFAULT_BRANCH": "main", "CI_COMMIT_BRANCH": "main",
                    "CI_COMMIT_SHA": "a" * 40, "CI_PIPELINE_ID": "42",
                    "BUILD_PLATFORM": "linux/amd64", "REGISTRY_HOST": "registry.example.invalid:443",
                    "IMAGE_REPOSITORY": "registry.example.invalid:443/business/demo"}
        self.metadata = {"containerimage.digest": "sha256:" + "b" * 64}

    def test_proposal_is_digest_pinned_without_application_revision(self):
        result = proposal(self.metadata, self.env)
        self.assertEqual(result["image"], self.env["IMAGE_REPOSITORY"] + "@sha256:" + "b" * 64)
        self.assertIsNone(result["applicationRevision"])
        self.assertEqual(result["deploymentPath"], "apps/production-demo")

    def test_rejects_untrusted_pipeline_and_invalid_release_fields(self):
        for key, value in [("CI_PIPELINE_SOURCE", "merge_request_event"), ("CI_COMMIT_REF_PROTECTED", "false"),
                           ("CI_COMMIT_BRANCH", "feature"), ("CI_DEFAULT_BRANCH", ""),
                           ("CI_COMMIT_SHA", "a" * 7), ("CI_PIPELINE_ID", "42\n"),
                           ("CI_DEBUG_TRACE", "true"),
                           ("BUILD_PLATFORM", "linux/amd64,linux/arm64"),
                           ("IMAGE_REPOSITORY", "other.example.invalid/demo"),
                           ("IMAGE_REPOSITORY", "registry.example.invalid:443/demo\nattack"),
                           ("REGISTRY_HOST", "https://registry.example.invalid")]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                proposal(self.metadata, {**self.env, key: value})

    def test_rejects_invalid_digest_and_descriptor(self):
        for metadata in [[], {}, {"containerimage.digest": "sha256:" + "G" * 64},
                         {**self.metadata, "containerimage.descriptor": {"digest": "sha256:" + "c" * 64}},
                         {**self.metadata, "containerimage.descriptor": "invalid"}]:
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                proposal(metadata, self.env)

    def test_rejects_invalid_registry_port(self):
        with self.assertRaises(ValueError):
            proposal(self.metadata, {**self.env, "REGISTRY_HOST": "registry.example.invalid:99999",
                                     "IMAGE_REPOSITORY": "registry.example.invalid:99999/demo"})

    def run_client(self, root, overrides=None):
        cert = root / "cert.pem"
        cert.write_text("test fixture, not a real certificate")
        stub = root / "buildctl"
        stub.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$TEST_ARGS"\n'
                        'cp "$DOCKER_CONFIG/config.json" "$TEST_AUTH"\n'
                        'printf "%s" "$DOCKER_CONFIG" > "$TEST_CONFIG"\nexit "${TEST_FAIL:-0}"\n')
        stub.chmod(0o700)
        env = {**os.environ, **self.env, "PATH": str(root) + os.pathsep + os.environ["PATH"],
               "BUILDKIT_ADDR": "tcp://builder.example.invalid:1234", "BUILDKIT_CA": str(cert),
               "BUILDKIT_CERT": str(cert), "BUILDKIT_KEY": str(cert), "REGISTRY_USER": "ci-user",
               "REGISTRY_PASSWORD": 'secret"with$syntax', "TEST_ARGS": str(root / "args"),
               "TEST_AUTH": str(root / "auth"), "TEST_CONFIG": str(root / "config-path")}
        env.update(overrides or {})
        return subprocess.run(["sh", str(Path(__file__).with_name("build.sh"))], cwd=root, env=env,
                              capture_output=True, text=True, timeout=10)

    def test_client_forwards_mtls_and_sha_tag_then_removes_auth(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.run_client(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            args = (root / "args").read_text().splitlines()
            self.assertIn("--tlscacert", args)
            self.assertIn("--tlscert", args)
            self.assertIn("--tlskey", args)
            self.assertIn("type=image,name=" + self.env["IMAGE_REPOSITORY"] + ":" + "a" * 40 + ",push=true", args)
            auth = json.loads((root / "auth").read_text())["auths"][self.env["REGISTRY_HOST"]]["auth"]
            self.assertEqual(base64.b64decode(auth).decode(), 'ci-user:secret"with$syntax')
            self.assertFalse(Path((root / "config-path").read_text()).exists())
            self.assertNotIn("secret", result.stdout + result.stderr)

    def test_client_rejects_inputs_before_network_access(self):
        for override in [{"CI_PIPELINE_SOURCE": "web"}, {"BUILDKIT_KEY": "/absent"}, {"CI_DEBUG_TRACE": "true"},
                         {"IMAGE_REPOSITORY": self.env["IMAGE_REPOSITORY"] + ",push=false"},
                         {"BUILDKIT_ADDR": "tcp://builder.example.invalid\nattacker"},
                         {"BUILDKIT_ADDR": "tcp://builder.example.invalid:99999"},
                         {"BUILDKIT_ADDR": "tcp://builder.example.invalid"},
                         {"CI_COMMIT_SHA": "a" * 40 + "\n"}, {"REGISTRY_USER": "user:injection"}]:
            with self.subTest(override=override), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                result = self.run_client(root, override)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((root / "args").exists())

    def test_failed_build_still_removes_auth(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.run_client(root, {"TEST_FAIL": "1"})
            self.assertEqual(result.returncode, 1)
            self.assertFalse(Path((root / "config-path").read_text()).exists())


if __name__ == "__main__":
    unittest.main()
