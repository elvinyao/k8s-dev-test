"""HTTP assertions executed only inside the isolated monitoring smoke network."""
import argparse
import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

GRAFANA = 'http://grafana:3000'
PROMETHEUS = 'http://prometheus:9090'
ALERTMANAGER = 'http://alertmanager:9093'
FOLDERS = '/apis/folder.grafana.app/v1/namespaces/default/folders'
FOLDER = 'smoke-persistence'


def request(url, payload=None, authenticated=False):
    headers = {}
    if authenticated:
        password = Path('/run/secrets/grafana_admin_password').read_text().strip()
        headers['Authorization'] = 'Basic ' + base64.b64encode(f'admin:{password}'.encode()).decode()
    if payload is not None:
        headers['Content-Type'] = 'application/json'
    req = Request(url, data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
    with urlopen(req, timeout=10) as response:
        return json.load(response)


def eventually(check, description):
    deadline = time.monotonic() + 150
    while True:
        try:
            if check():
                return
        except (HTTPError, URLError, TimeoutError):
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError('Timed out: ' + description)
        time.sleep(3)


def query_up(timestamp=None):
    query = {'query': 'up'}
    if timestamp:
        query['time'] = timestamp
    result = request(PROMETHEUS + '/api/v1/query?' + urlencode(query))
    return {v['metric']['job']: v['value'][1] for v in result['data']['result']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['seed', 'restore'])
    parser.add_argument('--historical-time')
    args = parser.parse_args()
    expected = {'prometheus': '1', 'alertmanager': '1', 'grafana': '1'}
    eventually(lambda: query_up() == expected, 'all three Prometheus scrape targets healthy')
    eventually(lambda: any(a['labels'].get('alertname') == 'Watchdog'
                            for a in request(ALERTMANAGER + '/api/v2/alerts')),
               'Watchdog reaches Alertmanager')
    try:
        request(GRAFANA + '/api/org')
    except HTTPError as exc:
        if exc.code != 401:
            raise RuntimeError('Unexpected unauthenticated Grafana status') from None
    else:
        raise RuntimeError('Grafana protected API unexpectedly permits anonymous access')
    datasource = request(GRAFANA + '/api/datasources/uid/platform-prometheus', authenticated=True)
    if datasource['url'] != PROMETHEUS or datasource['type'] != 'prometheus':
        raise RuntimeError('Provisioned Grafana datasource does not match Prometheus')
    dashboard = request(GRAFANA + '/api/dashboards/uid/compose-monitoring-summary', authenticated=True)
    if dashboard['dashboard']['title'] != 'Platform monitoring overview':
        raise RuntimeError('Provisioned Grafana dashboard missing')
    if args.phase == 'seed':
        request(GRAFANA + FOLDERS, {'metadata': {'name': FOLDER},
                'spec': {'title': 'Smoke persistence'}}, authenticated=True)
        now = datetime.now(timezone.utc)
        silence = request(ALERTMANAGER + '/api/v2/silences', {
            'matchers': [{'name': 'alertname', 'value': 'SmokePersistence', 'isRegex': False}],
            'startsAt': now.isoformat(), 'endsAt': (now + timedelta(hours=1)).isoformat(),
            'createdBy': 'compose-smoke', 'comment': 'Smoke persistence'})
        if not silence.get('silenceID'):
            raise RuntimeError('Alertmanager did not create the test silence')
        print(json.dumps({'status': 'passed', 'phase': 'seed', 'historicalTime': time.time(),
                          'checks': ['scrape-targets', 'watchdog', 'grafana-auth', 'datasource',
                                     'dashboard', 'folder-created', 'silence-created']}))
    else:
        if not args.historical_time:
            parser.error('restore requires --historical-time')
        folder = request(GRAFANA + FOLDERS + '/' + FOLDER, authenticated=True)
        if folder['spec']['title'] != 'Smoke persistence':
            raise RuntimeError('Grafana folder did not survive restore')
        silences = request(ALERTMANAGER + '/api/v2/silences')
        if not any(s['comment'] == 'Smoke persistence' and s['status']['state'] == 'active' for s in silences):
            raise RuntimeError('Alertmanager silence did not survive restore')
        if query_up(args.historical_time) != expected:
            raise RuntimeError('Historical Prometheus samples did not survive restore')
        print(json.dumps({'status': 'passed', 'phase': 'restore',
                          'checks': ['scrape-targets', 'watchdog', 'grafana-auth', 'datasource',
                                     'dashboard', 'folder-restored', 'silence-restored', 'historical-samples']}))


if __name__ == '__main__':
    main()
