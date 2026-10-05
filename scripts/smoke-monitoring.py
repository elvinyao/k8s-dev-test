"""Exercise and restore an isolated copy of Monitoring Compose; delete only its test volumes.

Run with: bash .agent/run.sh --docker --toolbox python scripts/smoke-monitoring.py
No production .env, credentials, volumes or published ports are used.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ['prometheus', 'alertmanager', 'grafana']


def main():
    host_root = os.environ.get('PLATFORM_HOST_ROOT')
    if not host_root or not host_root.startswith('/'):
        sys.exit('Run through the repository runner with --docker --toolbox.')
    os.umask(0o077)
    run_id = 'monitoring-smoke-' + uuid.uuid4().hex[:12]
    directory = ROOT / '.local' / run_id
    directory.mkdir(parents=True, mode=0o700)
    report_path = directory / 'report.json'
    report = {'status': 'running', 'startedAt': datetime.now(timezone.utc).isoformat(),
              'project': run_id, 'steps': [], 'cleanup': {},
              'testOverrides': {'publishedPorts': False, 'internalNetwork': True, 'restartPolicy': 'no'}}
    projects = {}
    print(f'Runtime report: {report_path}', flush=True)

    def save():
        report_path.write_text(json.dumps(report, indent=2) + '\n')

    def run(name, command, timeout=600):
        print('CHECK ' + name, flush=True)
        step = {'name': name, 'log': name + '.log', 'status': 'running'}
        report['steps'].append(step)
        save()
        log_path = directory / step['log']
        with log_path.open('w') as log:
            result = subprocess.run(command, cwd=ROOT, text=True, stdout=log,
                                    stderr=subprocess.STDOUT, timeout=timeout)
        step.update(exitCode=result.returncode, status='passed' if result.returncode == 0 else 'failed')
        save()
        if result.returncode:
            raise RuntimeError(f'{name} failed; inspect {log_path}')
        return log_path.read_text()

    def compose(project, *args):
        return ['docker', 'compose', '--project-name', project, '-f', str(projects[project]),
                '--profile', 'tools', '--profile', 'smoke', *args]

    def register(project, config):
        # Never adopt an existing project, even if a test name unexpectedly collides.
        for resource, filters in [('ps', ['-a']), ('volume', ['ls']), ('network', ['ls'])]:
            command = ['docker', resource, *filters, '--filter', f'label=com.docker.compose.project={project}', '-q']
            if subprocess.check_output(command, text=True).strip():
                raise RuntimeError(f'Test project already exists: {project}; refusing to reuse it')
        for kind in ('volumes', 'networks'):
            for value in config[kind].values():
                if value.get('external') or not value['name'].startswith(project + '_'):
                    raise RuntimeError('Refusing non-test external/shared resource')
        path = directory / f'{project}.json'
        path.write_text(json.dumps(config, indent=2) + '\n')
        projects[project] = path

    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    save()
    try:
        source = ROOT / 'compose/monitoring'
        fixture = directory / 'monitoring'
        shutil.copytree(source, fixture, ignore=shutil.ignore_patterns('.env', '.env.*', 'secrets', 'backups', '__pycache__'))
        shutil.copyfile(source / '.env.example', fixture / '.env.example')
        fixture_host = Path(host_root) / fixture.relative_to(ROOT)
        run('prepare', ['python', str(fixture / 'prepare.py'), '--host-config-dir', str(fixture_host / 'config')])
        environment = fixture / '.env'
        environment.write_text(environment.read_text().replace('MONITORING_PROJECT_NAME=platform-monitoring',
                                                               f'MONITORING_PROJECT_NAME={run_id}'))
        report['sourceHashes'] = {str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
                                  for path in source.rglob('*') if path.is_file()
                                  and path.name != '.env' and not any(p in {'secrets', 'backups', '__pycache__'}
                                                                     for p in path.relative_to(source).parts)}
        resolved = subprocess.check_output(['docker', 'compose', '--project-directory', str(fixture),
                    '--env-file', str(environment), '-f', str(fixture / 'compose.yaml'), '--profile', '*',
                    'config', '--format', 'json'], text=True)
        config = json.loads(resolved)
        for network in config['networks'].values():
            network['internal'] = True
        for service in config['services'].values():
            service.pop('ports', None)
            service['restart'] = 'no'
        probe_source = str(Path(host_root) / 'scripts/monitoring_probe.py')
        config['services']['smoke-probe'] = {
            'image': 'python:3.13-bookworm', 'profiles': ['smoke'], 'restart': 'no', 'init': True,
            'read_only': True, 'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
            'mem_limit': '128m', 'cpus': 0.5, 'pids_limit': 64,
            'entrypoint': ['python', '/opt/probe.py'], 'networks': {'monitoring': None},
            'secrets': [{'source': 'grafana_admin_password'}],
            'volumes': [{'type': 'bind', 'source': probe_source, 'target': '/opt/probe.py', 'read_only': True}],
        }
        register(run_id, config)
        run('pull', compose(run_id, 'pull', *SERVICES, 'smoke-probe'))
        run('promtool', compose(run_id, 'run', '--rm', '--no-deps', '--entrypoint', '/bin/promtool',
                               'prometheus', 'check', 'config', '/etc/prometheus/prometheus.yml'))
        run('amtool', compose(run_id, 'run', '--rm', '--no-deps', '--entrypoint', '/bin/amtool',
                             'alertmanager', 'check-config', '/etc/alertmanager/alertmanager.yml'))
        run('startup', compose(run_id, 'up', '-d', '--wait', '--wait-timeout', '240', *SERVICES))
        state = run('seed', compose(run_id, 'run', '--rm', '--no-deps', 'smoke-probe', 'seed'))
        seeded = json.loads(next(line for line in reversed(state.splitlines()) if line.startswith('{')))
        report['seed'] = seeded
        report['images'] = json.loads(subprocess.check_output(compose(run_id, 'images', '--format', 'json'), text=True))
        run('stop', compose(run_id, 'stop', *SERVICES))
        run('backup', compose(run_id, 'run', '--rm', '--no-deps', 'volume-tools', '-ec',
            'umask 077; test ! -e /backups/smoke.tar.gz; '
            'tar -czpf /backups/smoke.tar.gz -C /volumes prometheus alertmanager grafana; '
            'tar -tzf /backups/smoke.tar.gz >/dev/null; sha256sum /backups/smoke.tar.gz'))
        report['backupSha256'] = hashlib.sha256((fixture / 'backups/smoke.tar.gz').read_bytes()).hexdigest()
        restored = run_id + '-restore'
        recovery = deepcopy(config)
        recovery['name'] = restored
        for kind in ('volumes', 'networks'):
            for value in recovery[kind].values():
                value['name'] = value['name'].replace(run_id + '_', restored + '_', 1)
        register(restored, recovery)
        run('restore', compose(restored, 'run', '--rm', '--no-deps', 'volume-tools', '-ec',
            'test -z "$(ls -A /volumes/prometheus)"; test -z "$(ls -A /volumes/alertmanager)"; '
            'test -z "$(ls -A /volumes/grafana)"; tar -xzpf /backups/smoke.tar.gz -C /volumes'))
        run('restore-startup', compose(restored, 'up', '-d', '--wait', '--wait-timeout', '240', *SERVICES))
        state = run('restore-probe', compose(restored, 'run', '--rm', '--no-deps', 'smoke-probe',
                                           'restore', '--historical-time', str(seeded['historicalTime'])))
        report['restore'] = json.loads(next(line for line in reversed(state.splitlines()) if line.startswith('{')))
        report['status'] = 'passed'
    except (Exception, KeyboardInterrupt) as exc:
        report.update(status='failed', error=str(exc) or 'Interrupted')
        print(report['error'], file=sys.stderr)
        for project in projects:
            with (directory / f'{project}-services.log').open('w') as log:
                subprocess.run(compose(project, 'logs', '--no-color', '--tail', '100'), stdout=log,
                               stderr=subprocess.STDOUT, timeout=30)
    finally:
        for project in reversed(projects):
            try:
                run('cleanup-' + project, compose(project, 'down', '--volumes', '--remove-orphans'), timeout=180)
                report['cleanup'][project] = 'removed test containers, networks and volumes'
            except Exception as exc:
                report['status'] = 'failed'
                report['cleanup'][project] = str(exc)
        report['finishedAt'] = datetime.now(timezone.utc).isoformat()
        save()
    print(f'{report["status"].upper()}: {report_path}', flush=True)
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
