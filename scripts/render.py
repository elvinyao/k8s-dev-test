"""Download pinned charts, lint values and render manifests without a cluster."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

import yaml

# Kubernetes CRD schemas can contain the literal enum value `=`. YAML 1.1
# labels it specially; it is an ordinary string in Kubernetes YAML.
yaml.SafeLoader.add_constructor('tag:yaml.org,2002:value', yaml.SafeLoader.construct_scalar)

ROOT = Path(__file__).resolve().parents[1]


def run(*args, capture=False):
    return subprocess.run(args, cwd=ROOT, check=True, text=True,
                          stdout=subprocess.PIPE if capture else None).stdout


def verify_archive(archive, chart):
    expected = chart.get('archiveSha256', '')
    if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError(f"Missing or invalid archiveSha256 for {chart['name']}")
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(f"Chart checksum mismatch for {chart['name']}: "
                         f'expected {expected}, got {actual}. '
                         'Investigate the archive; never update the lock automatically.')
    return actual


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment', choices=['local', 'production'], required=True)
    parser.add_argument('--component', help='Render one release name from platform/releases.yaml')
    args = parser.parse_args()
    config = yaml.safe_load((ROOT / 'platform/releases.yaml').read_text())
    output = ROOT / 'rendered' / args.environment
    output.mkdir(parents=True, exist_ok=True)
    summary = []
    charts = [c for c in config['charts']
              if args.environment in c['values']
              and (not args.component or c['name'] == args.component)]
    if not charts:
        parser.error('Unknown component or no values for the requested environment')
    summary_path = output / ('summary.json' if not args.component else f'{args.component}-summary.json')
    # Invalidate previous success before changing any rendered output. A partial
    # component render also invalidates the previous full-environment summary.
    (output / 'summary.json').unlink(missing_ok=True)
    summary_path.unlink(missing_ok=True)
    for chart in charts:
        (output / f"{chart['name']}.yaml").unlink(missing_ok=True)
    for chart in charts:
        print(f"Rendering {chart['name']} {chart['version']} ({args.environment})", flush=True)
        cache = ROOT / '.cache/charts' / f"{chart['name']}-{chart['version']}"
        cache.mkdir(parents=True, exist_ok=True)
        archives = list(cache.glob('*.tgz'))
        if not archives:
            pull = ['helm', 'pull', chart['chart'], '--version', str(chart['version']),
                    '--destination', str(cache)]
            if chart.get('repository'):
                pull += ['--repo', chart['repository']]
            run(*pull)
            archives = list(cache.glob('*.tgz'))
        if len(archives) != 1:
            raise ValueError(f'Expected one chart archive in {cache}')
        archive = archives[0]
        checksum = verify_archive(archive, chart)
        metadata = yaml.safe_load(run('helm', 'show', 'chart', str(archive), capture=True))
        if (str(metadata['version']).lstrip('v') != str(chart['version']).lstrip('v')
                or metadata['name'] != chart['chart'].rsplit('/', 1)[-1]):
            raise ValueError(f'Chart name/version mismatch: {archive}')
        (cache / 'upstream-values.yaml').write_text(
            run('helm', 'show', 'values', str(archive), capture=True))
        values = chart['values'][args.environment]
        run('helm', 'lint', str(archive), '--values', values,
            '--kube-version', str(config['kubernetesVersion']))
        rendered = run('helm', 'template', chart['name'], str(archive),
                       '--namespace', chart['namespace'], '--values', values,
                       '--kube-version', str(config['kubernetesVersion']), '--include-crds', capture=True)
        documents = [doc for doc in yaml.safe_load_all(rendered) if doc]
        if not documents or any(not isinstance(d, dict) or 'kind' not in d for d in documents):
            raise ValueError(f"Invalid rendered documents: {chart['name']}")
        (output / f"{chart['name']}.yaml").write_text(rendered)
        summary.append({'name': chart['name'], 'chartVersion': metadata['version'],
                        'appVersion': metadata.get('appVersion'), 'objects': len(documents),
                        'archiveSha256': checksum,
                        'manifestSha256': hashlib.sha256(rendered.encode()).hexdigest()})
    summary_path.write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary, indent=2))
    print('Rendered and linted only; no API admission, scheduling, storage or runtime verification.')


if __name__ == '__main__':
    main()
