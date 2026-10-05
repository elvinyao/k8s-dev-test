"""Run the complete offline configuration checks and record evidence; never deploy."""

from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'rendered/verification'
STEPS = [
    ('source', ['python', 'scripts/check-source.py']),
    ('yaml', ['python', 'scripts/validate.py']),
    ('regressions', ['python', '-m', 'unittest', 'discover', '-s', 'scripts/tests', '-v']),
    ('compose', ['python', 'scripts/validate-compose.py']),
    ('helm-local', ['python', 'scripts/render.py', '--environment', 'local']),
    ('helm-production', ['python', 'scripts/render.py', '--environment', 'production']),
    ('kustomize', ['python', 'scripts/validate-kustomize.py']),
    ('crds', ['python', 'scripts/validate-crds.py']),
    ('integration', ['python', 'scripts/validate-integration.py']),
    ('business-gitops', ['python', 'apps/production-demo/check.py', '--example']),
]


def now():
    return datetime.now(timezone.utc).isoformat()


def git(*args):
    return subprocess.check_output(['git', '-c', 'safe.directory=' + str(ROOT), *args], cwd=ROOT)


def source_state():
    # Respect .gitignore, including generated output and local secrets. Include
    # untracked source so a dirty working tree can still be identified precisely.
    paths = sorted(set(git('ls-files', '-c', '-o', '--exclude-standard', '-z').split(b'\0')) - {b''})
    digest = hashlib.sha256()
    count = 0
    for raw in paths:
        path = ROOT / raw.decode()
        if not path.exists() and not path.is_symlink():
            continue
        if not path.is_file() and not path.is_symlink():
            raise ValueError(f'Unsupported source entry: {path.relative_to(ROOT)}')
        contents = str(path.readlink()).encode() if path.is_symlink() else path.read_bytes()
        kind = b'link' if path.is_symlink() else b'file'
        digest.update(raw + b'\0' + kind + b'\0' + hashlib.sha256(contents).digest())
        count += 1
    try:
        revision = git('rev-parse', '--verify', 'HEAD').decode().strip()
    except subprocess.CalledProcessError:
        revision = None
    return {'gitCommit': revision,
            'dirty': bool(git('status', '--porcelain', '--untracked-files=all').strip()),
            'sourceSha256': digest.hexdigest(), 'sourceFiles': count}


def save(report):
    temporary = OUTPUT / 'report.json.tmp'
    temporary.write_text(json.dumps(report, indent=2) + '\n')
    temporary.replace(OUTPUT / 'report.json')


def verify():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    report = {'schemaVersion': 1, 'status': 'running', 'startedAt': now(),
              'scope': 'offline configuration checks; no runtime or recovery evidence', 'steps': []}
    save(report)  # A failed/interrupted run must not leave an old passing report.
    try:
        report['source'] = source_state()
        save(report)
        for name, command in STEPS:
            step = {'name': name, 'command': command, 'status': 'running', 'startedAt': now(),
                    'log': str((OUTPUT / f'{name}.log').relative_to(ROOT))}
            report['steps'].append(step)
            save(report)
            print(f'CHECK {name}', flush=True)
            with (ROOT / step['log']).open('w') as log:
                result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
            step.update(exitCode=result.returncode, finishedAt=now(),
                        status='passed' if result.returncode == 0 else 'failed')
            save(report)
            if result.returncode:
                print((ROOT / step['log']).read_text()[-4000:], file=sys.stderr)
                raise RuntimeError(f'{name} failed; see {step["log"]}')
        if source_state() != report['source']:
            raise RuntimeError('Source changed during verification. Rerun after edits finish.')
        report['status'] = 'passed'
    except (Exception, KeyboardInterrupt) as exc:
        report['status'] = 'failed'
        report['error'] = str(exc) or 'Interrupted'
        print(report['error'], file=sys.stderr)
    finally:
        report['finishedAt'] = now()
        save(report)
    print(f'{report["status"].upper()}: {OUTPUT / "report.json"}', flush=True)
    return 0 if report['status'] == 'passed' else 1


def main():
    lock_path = ROOT / '.cache/verification.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            sys.exit('A verification run is already active; do not overwrite its evidence.')
        return verify()


if __name__ == '__main__':
    sys.exit(main())
