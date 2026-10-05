#!/usr/bin/env python3
"""Load example Omnibus settings with the pinned image's real Ruby DSL, isolated."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / 'compose/gitlab'


def hashes():
    paths = [SOURCE / 'compose.yaml', SOURCE / '.env.example',
             SOURCE / 'config-check.compose.yaml', *sorted((SOURCE / 'scripts').glob('*'))]
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths if path.is_file()}


def main():
    os.umask(0o077)
    host_root = Path(os.environ.get('PLATFORM_HOST_ROOT', ''))
    if not host_root.is_absolute() or str(host_root) == '/workspace':
        raise SystemExit('Use bash .agent/run.sh --docker --toolbox python compose/gitlab/scripts/validate-config.py')
    identity = uuid.uuid4().hex[:12]
    work = ROOT / '.local' / ('gitlab-config-check-' + identity)
    fixture = work / 'fixture'
    fixture.mkdir(parents=True)
    fixture.chmod(0o755)  # Contains only public examples, readable by nobody UID.
    project = 'gitlab-config-check-' + identity
    report = {'startedAt': datetime.now(timezone.utc).isoformat(), 'status': 'running',
              'project': project, 'sourceFiles': hashes(),
              'scope': 'isolated packaged Ruby/SettingsDSL check; not GitLab startup, reconfigure or recovery',
              'limits': {'memory': '512MiB', 'cpus': 1, 'network': 'none', 'ports': []}}
    log_path = work / 'config-check.log'
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('GITLAB_', 'COMPOSE_'))}
    env['GITLAB_VALIDATION_DIR'] = str(host_root / fixture.relative_to(ROOT))
    compose = ['docker', 'compose', '-p', project, '--project-directory', str(SOURCE),
               '--env-file', str(SOURCE / '.env.example'), '-f', str(SOURCE / 'config-check.compose.yaml')]
    attempted = False
    cleanup_ok = True
    try:
        configuration = json.loads(subprocess.check_output(
            ['docker', 'compose', '--project-directory', str(SOURCE),
             '--env-file', str(SOURCE / '.env.example'), '-f', str(SOURCE / 'compose.yaml'),
             'config', '--format', 'json'], env=env, timeout=60))
        service = configuration['services']['gitlab']
        image = service['image']
        if '@sha256:' not in image:
            raise ValueError('The GitLab example image must include its immutable digest.')
        report['image'] = image
        fixtures = {
            'omnibus.rb': service['environment']['GITLAB_OMNIBUS_CONFIG'],
            'environment.json': json.dumps({key: service['environment'][key] for key in
                                           ('GITLAB_EXTERNAL_URL', 'GITLAB_SSH_PORT', 'GITLAB_TRUSTED_PROXIES')}),
            'root_password': 'public-validation-fixture-never-deploy\n',
            'check.rb': (SOURCE / 'scripts/check-config.rb').read_text(),
        }
        for name, value in fixtures.items():
            path = fixture / name
            path.write_text(value)
            path.chmod(0o444)
        attempted = True
        with log_path.open('w') as log:
            result = subprocess.run([*compose, 'run', '--rm', '-T', '--no-deps', 'config-check'],
                                    env=env, stdout=log, stderr=subprocess.STDOUT, timeout=900)
        report['exitCode'] = result.returncode
        if result.returncode:
            raise RuntimeError('GitLab configuration check failed; inspect its fixture-only log.')
        details = [json.loads(line) for line in log_path.read_text().splitlines() if line.startswith('{')]
        if len(details) != 1 or details[0].get('status') != 'passed':
            raise RuntimeError('The packaged config checker did not return successful evidence.')
        report['result'] = details[0]
        # Exercise failure paths using only the isolated copy. A broken key must
        # not pass just because the packaged DSL auto-vivifies nested hashes.
        report['negativeControls'] = []
        controls = [
            ('invalid-ruby', fixtures['omnibus.rb'] + '\ninvalid = ]\n',
             fixtures['environment.json'], 'Ruby syntax is invalid'),
            ('unknown-option', fixtures['omnibus.rb'].replace("['listen_port']", "['listen_port_typo']"),
             fixtures['environment.json'], "Option is absent from this image's template"),
            ('invalid-ssh-port', fixtures['omnibus.rb'],
             json.dumps({**json.loads(fixtures['environment.json']), 'GITLAB_SSH_PORT': 'not-an-integer'}),
             'invalid value for Integer()'),
        ]
        for name, ruby, environment, expected_error in controls:
            for filename, content in [('omnibus.rb', ruby), ('environment.json', environment)]:
                path = fixture / filename
                path.chmod(0o644)
                path.write_text(content)
                path.chmod(0o444)
            result = subprocess.run([*compose, 'run', '--rm', '-T', '--no-deps', 'config-check'],
                                    env=env, capture_output=True, text=True, timeout=60)
            with log_path.open('a') as log:
                log.write(f'\nNEGATIVE CONTROL {name}\n{result.stdout}{result.stderr}')
            if result.returncode == 0 or expected_error not in result.stdout + result.stderr:
                raise RuntimeError(f'Negative control did not fail for the expected reason: {name}')
            report['negativeControls'].append({'name': name, 'status': 'passed',
                                               'observedExitCode': result.returncode})
        info = json.loads(subprocess.check_output(['docker', 'image', 'inspect', image], env=env, timeout=60))[0]
        report['imageIdentity'] = {key: info[key] for key in ('Id', 'RepoDigests', 'Architecture', 'Os')}
        if hashes() != report['sourceFiles']:
            raise RuntimeError('GitLab sources changed during validation; rerun on the final files.')
        report['status'] = 'passed'
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
        report['status'] = 'failed'
        report['error'] = str(exc) or 'Interrupted'
    finally:
        if attempted:
            try:
                with log_path.open('a') as log:
                    result = subprocess.run([*compose, 'down', '--volumes', '--remove-orphans'],
                                            env=env, stdout=log, stderr=subprocess.STDOUT, timeout=120)
                report['cleanupExitCode'] = result.returncode
                cleanup_ok = result.returncode == 0
                if cleanup_ok:
                    remaining = subprocess.check_output(
                        ['docker', 'ps', '-aq', '--filter', f'label=com.docker.compose.project={project}'],
                        env=env, timeout=60).decode().strip()
                    cleanup_ok = not remaining
                    report['remainingContainers'] = remaining.splitlines()
            except (OSError, subprocess.SubprocessError) as exc:
                cleanup_ok = False
                report['cleanupError'] = str(exc)
            if not cleanup_ok:
                report['status'] = 'failed'
                report['error'] = 'Own-project cleanup failed; fixture retained for manual cleanup.'
        if cleanup_ok:
            shutil.rmtree(fixture)
        report['finishedAt'] = datetime.now(timezone.utc).isoformat()
        (work / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f'{report["status"].upper()}: {work / "report.json"}')
    print(f'Log: {log_path}')
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
