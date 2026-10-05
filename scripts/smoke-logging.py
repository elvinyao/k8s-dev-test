"""Exercise the ELK Compose stack and restore logs into a second, empty Elasticsearch.

Run through bash .agent/run.sh --docker --toolbox python scripts/smoke-logging.py.
Only generated fixtures and randomly named test projects are used. No host sysctl
changes, resource-limit reductions, production credentials or published ports.
"""
import argparse
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

import yaml

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'compose/logging'
GIB = 1024 ** 3
SERVICES = ['elasticsearch', 'kibana', 'logstash']
FILEBEAT_IMAGE = 'docker.elastic.co/beats/filebeat:9.5.4'


def source_hashes():
    paths = [SOURCE / 'compose.yaml', SOURCE / '.env.example', Path(__file__),
             *sorted((SOURCE / 'config').rglob('*')), *sorted((SOURCE / 'scripts').glob('*'))]
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in paths if path.is_file()}


def capacity_issues(info, available_memory, map_count, free_disk, limits):
    """Use the documented capacity baseline, not a reduced-memory test profile."""
    required = max(12 * GIB, sum(limits) + 3 * GIB)
    issues = []
    if info.get('OSType') != 'linux':
        issues.append('The Docker daemon must run Linux containers.')
    if info.get('MemTotal', 0) < required:
        issues.append(f'Docker needs at least {required / GIB:.1f} GiB total memory; '
                      f'found {info.get("MemTotal", 0) / GIB:.1f} GiB.')
    if available_memory < sum(limits) + 2 * GIB:
        issues.append('Insufficient available VM memory for the unchanged service limits plus 2 GiB headroom.')
    if map_count < 262144:
        issues.append('vm.max_map_count is below the Elasticsearch bootstrap minimum 262144.')
    if free_disk < 20 * GIB:
        issues.append('At least 20 GiB free Docker filesystem space is required for images, data and snapshots.')
    return issues


def guard_config(project, config, fixture_host):
    """Refuse shared resources and any bind source outside the generated fixture."""
    fixture_host = fixture_host.resolve()
    if config.get('name') != project or not project.startswith('logging-smoke-'):
        raise ValueError('Unexpected test project identity')
    for kind in ('volumes', 'networks'):
        for value in config.get(kind, {}).values():
            if (value.get('external') or value.get('driver_opts')
                    or not value.get('name', '').startswith(project + '_')):
                raise ValueError('Refusing non-test external/shared resources')
            if kind == 'networks' and not value.get('internal'):
                raise ValueError('Test networks must be internal')
            if value.get('driver') not in {None, 'local' if kind == 'volumes' else 'bridge'}:
                raise ValueError('Unexpected test resource driver')
    for secret in config.get('secrets', {}).values():
        if secret.get('external') or not Path(secret.get('file', '/')).resolve().is_relative_to(fixture_host):
            raise ValueError('Refusing non-fixture secrets')
    for service in config['services'].values():
        if service.get('ports') or service.get('privileged') or service.get('network_mode'):
            raise ValueError('Test services must not publish ports or escape their isolated network')
        for volume in service.get('volumes', []):
            if volume['type'] == 'bind' and not Path(volume['source']).resolve().is_relative_to(fixture_host):
                raise ValueError('Refusing a bind mount outside the test fixture')
            if volume['type'] == 'volume' and volume.get('source') not in config.get('volumes', {}):
                raise ValueError('Refusing a volume not declared by the test project')


def recovery_config(config, project, restored):
    recovery = deepcopy(config)
    recovery['name'] = restored
    recovery['services'] = {name: recovery['services'][name] for name in ('elasticsearch', 'runtime-probe')}
    recovery['volumes'] = {'es-data': recovery['volumes']['es-data']}
    for kind in ('volumes', 'networks'):
        for value in recovery[kind].values():
            value['name'] = value['name'].replace(project + '_', restored + '_', 1)
    for mount in recovery['services']['elasticsearch']['volumes']:
        if mount['target'] == '/mnt/snapshots':
            mount['read_only'] = True
    return recovery


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--preflight-only', action='store_true', help='Check prerequisites without pulling or starting ELK')
    mode.add_argument('--check-model', action='store_true', help='Resolve generated test Compose models only; never start services')
    args = parser.parse_args()
    os.umask(0o077)
    host_root = Path(os.environ.get('PLATFORM_HOST_ROOT', ''))
    if not host_root.is_absolute() or str(host_root) == '/workspace':
        sys.exit('Use the repository runner with --docker --toolbox.')
    identity = uuid.uuid4().hex[:12]
    project = 'logging-smoke-' + identity
    directory = ROOT / '.local' / project
    fixture = directory / 'fixture'
    fixture_host = host_root / fixture.relative_to(ROOT)
    directory.mkdir(parents=True, mode=0o700)
    report_path = directory / 'report.json'
    report = {'status': 'running', 'startedAt': datetime.now(timezone.utc).isoformat(),
              'project': project, 'steps': [], 'cleanup': {}, 'sourceFiles': source_hashes(),
              'scope': 'isolated ELK ingestion, restart and log-only snapshot recovery; not full platform disaster recovery',
              'testOverrides': {'ports': [], 'restart': 'no', 'internalNetwork': True,
                                'resourceLimits': 'unchanged', 'filebeat': FILEBEAT_IMAGE}}
    projects = {}
    attempted_runtime = False
    credentials = []
    env = dict(os.environ)
    example_keys = [line.split('=', 1)[0] for line in (SOURCE / '.env.example').read_text().splitlines()
                    if line and not line.startswith('#') and '=' in line]
    for key in list(env):
        if key in example_keys or key.startswith('COMPOSE_'):
            env.pop(key)

    def save():
        report_path.write_text(json.dumps(report, indent=2) + '\n')

    def redact(value):
        for secret in credentials:
            value = value.replace(secret, '[REDACTED]')
        return value

    def run(name, command, timeout=600):
        print('CHECK ' + name, flush=True)
        log_path = directory / (name + '.log')
        step = {'name': name, 'status': 'running', 'log': str(log_path.relative_to(ROOT))}
        report['steps'].append(step)
        save()
        try:
            with log_path.open('w') as log:
                result = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
            step.update(status='passed' if result.returncode == 0 else 'failed', exitCode=result.returncode)
            if result.returncode:
                raise RuntimeError(f'{name} failed; inspect {log_path}')
            return log_path.read_text()
        except (OSError, subprocess.SubprocessError, KeyboardInterrupt):
            step['status'] = 'failed'
            raise
        finally:
            if log_path.exists():
                log_path.write_text(redact(log_path.read_text(errors='replace')))
            save()

    def compose(name, *command):
        return ['docker', 'compose', '-p', name, '-f', str(projects[name]), '--profile', '*', *command]

    def register(name, config):
        guard_config(name, config, fixture_host)
        for command in (['ps', '-aq'], ['volume', 'ls', '-q'], ['network', 'ls', '-q']):
            if subprocess.check_output(['docker', *command, '--filter', f'label=com.docker.compose.project={name}'],
                                       env=env, text=True).strip():
                raise RuntimeError('Refusing to adopt existing resources for ' + name)
        path = directory / (name + '.json')
        path.write_text(json.dumps(config, indent=2) + '\n')
        projects[name] = path

    def probe(name, phase, *arguments):
        output = run(name + '-' + phase, compose(name, 'run', '--rm', '-T', '--no-deps',
                     'runtime-probe', phase, *arguments), timeout=900)
        objects = [json.loads(line) for line in output.splitlines() if line.startswith('{')]
        if len(objects) != 1 or objects[0].get('status') != 'passed':
            raise RuntimeError('Runtime probe did not return successful evidence for ' + phase)
        return objects[0]

    def bind(source, target, read_only=True):
        return {'type': 'bind', 'source': str(source), 'target': target, 'read_only': read_only,
                'bind': {'create_host_path': False}}

    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    print(f'ELK runtime report: {report_path}', flush=True)
    save()
    try:
        # Current runner and daemon must share the same VM: procfs then describes
        # the kernel actually used by the service containers, not a remote host.
        runner = json.loads(subprocess.check_output(['docker', 'inspect', os.environ['HOSTNAME']], env=env))[0]
        if not any(m['Destination'] == '/workspace' and m['Source'] == str(host_root) for m in runner['Mounts']):
            raise RuntimeError('Cannot establish that the current runner belongs to the selected Docker daemon')
        info = json.loads(subprocess.check_output(['docker', 'info', '--format', '{{json .}}'], env=env))
        meminfo = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
        available = int(meminfo['MemAvailable'].split()[0]) * 1024
        map_count = int(Path('/proc/sys/vm/max_map_count').read_text())
        free_disk = shutil.disk_usage('/').free
        # Parse the public example only. Do not read a deployment's .env.
        source_config = json.loads(subprocess.check_output(['docker', 'compose', '--project-directory', str(SOURCE),
             '--env-file', str(SOURCE / '.env.example'), '-f', str(SOURCE / 'compose.yaml'),
             '--profile', '*', 'config', '--format', 'json'], env=env))
        limits = [int(source_config['services'][name]['mem_limit']) for name in SERVICES]
        issues = capacity_issues(info, available, map_count, free_disk, limits)
        report['preflight'] = {'docker': {key: info[key] for key in ('OperatingSystem', 'Architecture', 'NCPU', 'MemTotal')},
                               'availableMemory': available, 'vmMaxMapCount': map_count, 'freeDockerDisk': free_disk,
                               'serviceMemoryLimits': dict(zip(SERVICES, limits)), 'issues': issues,
                               'warnings': ['Elastic recommends vm.max_map_count=1048576'] if map_count < 1048576 else []}
        save()
        if issues and not args.check_model:
            raise RuntimeError('ELK prerequisites not met: ' + ' '.join(issues))
        if args.preflight_only:
            report.update(status='preflight-passed', scope='prerequisites only; no ELK services started')
            return 0

        source = fixture / 'compose/logging'
        shutil.copytree(SOURCE, source, ignore=shutil.ignore_patterns('runtime', '.env', '.env.*', '__pycache__'))
        shutil.copyfile(SOURCE / '.env.example', source / '.env.example')
        run('prepare', ['python', str(source / 'scripts/prepare.py'), '--host-root', str(fixture_host)])
        credentials.extend(path.read_text().strip() for path in (source / 'runtime/secrets').iterdir())
        config = json.loads(subprocess.check_output(['docker', 'compose', '-p', project, '--project-directory', str(source),
             '--env-file', str(source / '.env'), '-f', str(source / 'compose.yaml'), '--profile', '*',
             'config', '--format', 'json'], env=env))
        for network in config['networks'].values():
            network['internal'] = True
        for service in config['services'].values():
            service.pop('ports', None)
            service['restart'] = 'no'
        config['services']['runtime-probe'] = {
            'image': 'python:3.13-bookworm', 'user': '1000:0', 'read_only': True, 'init': True,
            'restart': 'no', 'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
            'mem_limit': '256m', 'cpus': 0.5, 'pids_limit': 128, 'networks': {'logging': None},
            'entrypoint': ['python', '/opt/logging/runtime-probe.py'],
            'environment': {'PYTHONDONTWRITEBYTECODE': '1'},
            'secrets': [{'source': name} for name in ('elastic_password', 'ingest_password', 'logstash_password')],
            'volumes': [bind(fixture_host / 'compose/logging/scripts', '/opt/logging'),
                        bind(fixture_host / 'compose/logging/runtime/tls/public', '/tls'),
                        bind(fixture_host / 'compose/logging/runtime/tls/collector', '/tls/collector')]}
        collector = source / 'collector'
        collector.mkdir(mode=0o755)
        (collector / 'sample.log').write_text(json.dumps({'message': identity + '-beats', 'padding': 'x' * 2048}) + '\n')
        (collector / 'sample.log').chmod(0o644)
        beat = yaml.safe_load((source / 'config/filebeat.yml.example').read_text())
        beat['output.logstash']['hosts'] = ['logstash:5044']
        beat_path = collector / 'filebeat.yml'
        beat_path.write_text(yaml.safe_dump(beat))
        beat_path.chmod(0o644)
        # Exercise the actual filestream/Beats client, not a protocol imitation.
        config['services']['filebeat-probe'] = {
            'image': FILEBEAT_IMAGE, 'user': '1000:0', 'read_only': True, 'init': True,
            'restart': 'no', 'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
            'mem_limit': '256m', 'cpus': 0.5, 'pids_limit': 128, 'networks': {'logging': None},
            'command': ['filebeat', '-e', '-c', '/usr/share/filebeat/filebeat.yml'],
            'tmpfs': ['/usr/share/filebeat/data:rw,nosuid,nodev,size=128m,mode=1777'],
            'volumes': [bind(fixture_host / 'compose/logging/collector', '/var/log/platform'),
                        bind(fixture_host / 'compose/logging/collector/filebeat.yml', '/usr/share/filebeat/filebeat.yml'),
                        bind(fixture_host / 'compose/logging/runtime/tls/collector', '/etc/filebeat/tls')]}
        register(project, config)
        restored = project + '-restore'
        register(restored, recovery_config(config, project, restored))
        for name in projects:
            run('model-' + name, compose(name, 'config', '--quiet'))
        if args.check_model:
            if source_hashes() != report['sourceFiles']:
                raise RuntimeError('Sources changed during model validation; rerun after edits finish')
            report.update(status='model-checked', scope='generated test Compose models only; no ELK services started')
            return 0
        attempted_runtime = True
        run('pull', compose(project, 'pull', *SERVICES, 'filebeat-probe', 'runtime-probe'), timeout=1800)
        images = {service['image'] for service in config['services'].values()}
        report['images'] = [{key: item[key] for key in ('Id', 'RepoTags', 'RepoDigests', 'Architecture')}
                            for item in json.loads(subprocess.check_output(['docker', 'image', 'inspect', *sorted(images)], env=env))]
        run('elasticsearch-start', compose(project, 'up', '-d', '--wait', '--wait-timeout', '480', 'elasticsearch'))
        run('bootstrap', compose(project, 'run', '--rm', '-T', '--no-deps', 'bootstrap'))
        run('application-start', compose(project, 'up', '-d', '--wait', '--wait-timeout', '600', 'kibana', 'logstash'), timeout=720)
        report['seed'] = probe(project, 'seed', '--identity', identity)
        run('filebeat-start', compose(project, 'up', '-d', 'filebeat-probe'))
        report['ingestion'] = probe(project, 'verify', '--identity', identity)
        run('stop-collector', compose(project, 'stop', 'filebeat-probe'))
        run('restart', compose(project, 'up', '-d', '--force-recreate', '--wait', '--wait-timeout', '600', *SERVICES), timeout=720)
        report['restart'] = probe(project, 'verify', '--identity', identity)
        report['snapshot'] = probe(project, 'snapshot')
        # Avoid simultaneous full stacks and a second writer to the repository.
        run('stop-source', compose(project, 'stop', *SERVICES))
        run('restore-elasticsearch-start', compose(restored, 'up', '-d', '--wait', '--wait-timeout', '480', 'elasticsearch'))
        report['restore'] = probe(restored, 'restore', '--snapshot', report['snapshot']['snapshot'], '--identity', identity)
        if source_hashes() != report['sourceFiles']:
            raise RuntimeError('Sources changed during validation; rerun after edits finish')
        report['status'] = 'passed'
    except (Exception, KeyboardInterrupt) as exc:
        report.update(status='failed', error=str(exc) or 'Interrupted')
        print(report['error'], file=sys.stderr)
        for name in projects if attempted_runtime else []:
            try:
                run('diagnostics-' + name, compose(name, 'logs', '--no-color', '--tail', '150'), timeout=30)
            except Exception:
                pass
    finally:
        cleaned = True
        for name in reversed(projects) if attempted_runtime else []:
            try:
                run('cleanup-' + name, compose(name, 'down', '--volumes', '--remove-orphans'), timeout=180)
                for command in (['ps', '-aq'], ['volume', 'ls', '-q'], ['network', 'ls', '-q']):
                    if subprocess.check_output(['docker', *command, '--filter', f'label=com.docker.compose.project={name}'],
                                               env=env, text=True).strip():
                        raise RuntimeError('Own-project resources remain after cleanup')
                report['cleanup'][name] = 'removed own test containers, networks and volumes'
            except Exception as exc:
                cleaned = False
                report.update(status='failed', error='Own-project cleanup failed; fixture retained for recovery')
                report['cleanup'][name] = str(exc)
        if cleaned and fixture.exists():
            shutil.rmtree(fixture)
        report['finishedAt'] = datetime.now(timezone.utc).isoformat()
        save()
        print(f'{report["status"].upper()}: {report_path}', flush=True)
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
