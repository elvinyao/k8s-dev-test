"""Check source syntax and local Markdown links without importing project code."""
import ast
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
ignored = {'.git', '.cache', '.local', 'rendered', '.venv', 'data', 'backups', 'secrets', 'secrets.local', '__pycache__'}
files = [p for p in ROOT.rglob('*') if p.is_file()
         and not any(part in ignored for part in p.relative_to(ROOT).parts)]
counts = {'python': 0, 'shell': 0, 'local_links': 0}
errors = []
examples = [str(p.relative_to(ROOT)) for p in files if p.name.endswith('.example')]
if examples:
    result = subprocess.run(['git', '-c', 'safe.directory=' + str(ROOT), 'check-ignore',
                             '--no-index', '--stdin', '-z'], cwd=ROOT,
                            input='\0'.join(examples) + '\0', text=True, capture_output=True)
    if result.returncode not in {0, 1}:
        raise RuntimeError('Unable to check whether configuration examples are ignored by Git')
    errors.extend('Configuration example ignored by Git: ' + name
                  for name in result.stdout.split('\0') if name)
for path in files:
    if path.suffix == '.py':
        ast.parse(path.read_text(), filename=str(path))
        counts['python'] += 1
    elif path.suffix == '.sh':
        subprocess.run(['bash', '-n', str(path)], check=True)
        counts['shell'] += 1
    elif path.suffix == '.md':
        for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)', path.read_text()):
            if '://' in target or target.startswith(('#', 'mailto:')):
                continue
            target = unquote(target.split('#', 1)[0].strip('<>'))
            if not target:
                continue
            counts['local_links'] += 1
            if not (path.parent / target).exists():
                errors.append(f'{path.relative_to(ROOT)}: missing link target {target}')
if errors:
    raise ValueError('\n'.join(errors))
print(f'Source checks passed: {counts}')
