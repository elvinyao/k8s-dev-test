"""Read only complete, unchanged render outputs described by their summaries."""
import hashlib
import json
import yaml


def checked_documents(directory, key):
    summary_path = directory / 'summary.json'
    if not summary_path.is_file():
        raise ValueError(f'Missing complete render summary: {summary_path}. Rerun the full render.')
    summary = json.loads(summary_path.read_text())
    if not summary:
        raise ValueError(f'Empty render summary: {summary_path}')
    rendered = {}
    for entry in summary:
        name = entry[key]
        filename = name.replace('/', '-') if key == 'path' else name
        path = directory / f'{filename}.yaml'
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry.get('manifestSha256'):
            raise ValueError(f'Render output changed or lacks integrity metadata: {path}. Rerun the full render.')
        rendered[name] = [obj for obj in yaml.safe_load_all(path.read_text()) if obj]
        if len(rendered[name]) != entry['objects']:
            raise ValueError(f'Render object count mismatch: {path}')
    return rendered
