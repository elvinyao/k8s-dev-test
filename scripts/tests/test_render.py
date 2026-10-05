"""Regression checks for chart integrity and stale-success handling."""

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

spec = importlib.util.spec_from_file_location('chart_render', Path(__file__).parents[1] / 'render.py')
render = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render)


class ChartIntegrityTests(unittest.TestCase):
    def test_mismatched_or_missing_lock_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / 'example.tgz'
            archive.write_bytes(b'approved archive')
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            self.assertEqual(render.verify_archive(archive, {'name': 'example', 'archiveSha256': digest}), digest)
            for value in [None, '', 'latest', '0' * 64]:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    render.verify_archive(archive, {'name': 'example', 'archiveSha256': value})
            archive.write_bytes(b'changed archive')
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                render.verify_archive(archive, {'name': 'example', 'archiveSha256': digest})

    def test_rejected_archive_invalidates_success_before_helm_runs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'platform').mkdir()
            chart = {'name': 'example', 'chart': 'example', 'version': '1.0.0',
                     'archiveSha256': '0' * 64, 'namespace': 'example',
                     'values': {'production': 'example.yaml'}}
            (root / 'platform/releases.yaml').write_text(yaml.safe_dump({'charts': [chart]}))
            cache = root / '.cache/charts/example-1.0.0'
            cache.mkdir(parents=True)
            (cache / 'example.tgz').write_bytes(b'tampered archive')
            output = root / 'rendered/production'
            output.mkdir(parents=True)
            (output / 'summary.json').write_text(json.dumps([{'name': 'old-success'}]))
            (output / 'example.yaml').write_text('kind: OldManifest\n')
            with patch.object(render, 'ROOT', root), patch.object(render, 'run') as run, \
                    patch('sys.argv', ['render.py', '--environment', 'production']):
                with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                    render.main()
                run.assert_not_called()
            self.assertFalse((output / 'summary.json').exists())
            self.assertFalse((output / 'example.yaml').exists())


if __name__ == '__main__':
    unittest.main()
