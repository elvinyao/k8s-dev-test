"""Evidence must fail closed when a check or rendered input changes."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
from rendered_inputs import checked_documents

spec = importlib.util.spec_from_file_location('verification', Path(__file__).parents[1] / 'verify.py')
verification = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verification)


class VerificationTests(unittest.TestCase):
    def test_modified_render_cannot_be_reused_as_valid_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            path = directory / 'example.yaml'
            path.write_text('kind: ConfigMap\n')
            summary = [{'name': 'example', 'objects': 1,
                        'manifestSha256': hashlib.sha256(path.read_bytes()).hexdigest()}]
            (directory / 'summary.json').write_text(json.dumps(summary))
            self.assertEqual(len(checked_documents(directory, 'name')['example']), 1)
            path.write_text('kind: Secret\n')
            with self.assertRaisesRegex(ValueError, 'changed'):
                checked_documents(directory, 'name')
            (directory / 'summary.json').unlink()
            with self.assertRaisesRegex(ValueError, 'Missing complete render summary'):
                checked_documents(directory, 'name')

    def test_failure_replaces_old_passing_report_and_stops_later_steps(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output = root / 'rendered/verification'
            output.mkdir(parents=True)
            (output / 'report.json').write_text('{"status": "passed"}')
            with patch.object(verification, 'ROOT', root), patch.object(verification, 'OUTPUT', output), \
                    patch.object(verification, 'source_state', return_value={'gitCommit': 'test'}), \
                    patch.object(verification, 'STEPS', [('first', ['false']), ('later', ['unused'])]), \
                    patch.object(verification.subprocess, 'run', return_value=subprocess.CompletedProcess(['false'], 1)) as run:
                self.assertEqual(verification.verify(), 1)
            report = json.loads((output / 'report.json').read_text())
            self.assertEqual(report['status'], 'failed')
            self.assertEqual(len(report['steps']), 1)
            self.assertEqual(run.call_count, 1)


if __name__ == '__main__':
    unittest.main()
