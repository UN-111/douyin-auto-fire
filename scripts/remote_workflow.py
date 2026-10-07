"""GitHub-hosted standard task; durable daily claim and public, sanitized status."""
import base64
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
TERMINAL = {'success', 'probe_passed', 'failed', 'delivery_unknown', 'duplicate_blocked'}


def api(path, method='GET', data=None):
    url = os.environ.get('GITHUB_API_URL', 'https://api.github.com') + '/repos/' + os.environ['GITHUB_REPOSITORY'] + path
    request = urllib.request.Request(url, method=method,
        headers={'Authorization': 'Bearer ' + os.environ['GH_TOKEN'], 'Accept': 'application/vnd.github+json'},
        data=json.dumps(data).encode() if data is not None else None)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def validate_request(request, today):
    if request.get('mode') not in {'probe', 'send'}:
        raise ValueError('invalid_mode')
    if request.get('date') != today:
        raise ValueError('request_date_mismatch')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', request.get('request_id', '')):
        raise ValueError('invalid_request_id')
    return request


def claim_date(today, commit, request_api=api):
    # GitHub atomically refuses creation of an existing ref (422).
    try:
        request_api('/git/refs', 'POST', {'ref': 'refs/tags/douyin-daily-' + today, 'sha': commit})
        return True
    except urllib.error.HTTPError as exc:
        if exc.code == 422:
            return False
        raise


def get_report(path):
    try:
        data = api('/contents/' + path + '?ref=main')
        return json.loads(base64.b64decode(data['content'])), data['sha']
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None, None
        raise


def publish(path, report):
    _, sha = get_report(path)
    data = {'message': 'Record Douyin workflow status [skip ci]', 'branch': 'main',
            'content': base64.b64encode((json.dumps(report, indent=2) + '\n').encode()).decode()}
    if sha:
        data['sha'] = sha
    api('/contents/' + path, 'PUT', data)


def run_standard(mode, runner=subprocess.run):
    env = os.environ.copy()
    # The browser child receives no GitHub write token.
    env.pop('GH_TOKEN', None)
    env.pop('GITHUB_TOKEN', None)
    tasks = json.loads((ROOT / 'config/github-actions.tasks.json').read_text())
    if len(tasks) != 1 or len(tasks[0].get('targets', [])) != 5:
        raise ValueError('unexpected_target_configuration')
    cookies = json.loads(env.get('COOKIES_' + tasks[0]['unique_id'].upper(), 'null'))
    if not isinstance(cookies, list) or not cookies or not all(isinstance(c, dict) for c in cookies):
        raise ValueError('credential_missing_or_invalid')
    if mode == 'probe':
        tasks[0]['targets'] = tasks[0]['targets'][:1]
    env.update(TASKS=json.dumps(tasks), DEBUG='false', LOG_LEVEL='Info', CLOAKBROWSER_AUTO_UPDATE='false')
    # GitHub-hosted runners use their normal egress; inherited proxies are preserved.
    if env.get('HTTPS_PROXY') or env.get('HTTP_PROXY'):
        env['PROXY_ADDRESS'] = env.get('HTTPS_PROXY') or env['HTTP_PROXY']
    try:
        proc = runner([sys.executable, 'main.py', 'probe' if mode == 'probe' else 'task'],
                      cwd=ROOT, env=env, capture_output=True, timeout=900)
    except subprocess.TimeoutExpired:
        return {'status': 'delivery_unknown' if mode == 'send' else 'failed', 'error': 'timeout', 'retry_allowed': False}
    text = (proc.stdout + proc.stderr).decode('utf-8', errors='replace')
    if mode == 'probe':
        p = ROOT / 'artifacts/sticker-probe/account-01.json'
        result = json.loads(p.read_text()) if p.exists() else {}
        return {'status': 'probe_passed' if proc.returncode == 0 and result.get('ok') else 'failed',
                'exit_code': proc.returncode, 'panel_verified': bool(result.get('ok')),
                'error': result.get('error'), 'message_sent': False}
    counts = re.findall(r'发送成功=(\d+) 发送失败=(\d+)', text)
    success, failure = map(int, counts[-1]) if counts else (0, 0)
    return {'status': 'success' if proc.returncode == 0 and success == 5 and failure == 0 else 'failed',
            'exit_code': proc.returncode, 'confirmed_count': success, 'failed_count': failure,
            'login_failure': any(s in text for s in ['登录已失效', '未登录', '掉登录']),
            'unconfirmed_dispatch': '已点击，但未确认资源回执' in text, 'retry_allowed': False}


def main():
    today = datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
    mode = os.environ.get('DOUYIN_MODE', 'probe')
    request = validate_request({'mode': mode, 'date': today, 'request_id': today + ('-send' if mode == 'send' else '-probe-' + os.environ['GITHUB_RUN_ID'])}, today)
    path = '.codex/results/' + request['request_id'] + '.json'
    prior, _ = get_report(path)
    if prior and prior.get('status') in TERMINAL:
        print(json.dumps({'status': 'already_recorded', 'prior_status': prior['status']}))
        return 0 if prior['status'] in {'success', 'probe_passed'} else 1
    report = {'request_id': request['request_id'], 'date': today, 'mode': request['mode'],
              'run_url': os.environ['GITHUB_SERVER_URL'] + '/' + os.environ['GITHUB_REPOSITORY'] + '/actions/runs/' + os.environ['GITHUB_RUN_ID'],
              'status': 'running', 'retry_allowed': False}
    publish(path, report)
    try:
        if request['mode'] == 'send' and not claim_date(today, os.environ['GITHUB_SHA']):
            result = {'status': 'duplicate_blocked', 'retry_allowed': False}
        else:
            result = run_standard(request['mode'])
    except Exception as exc:
        result = {'status': 'failed', 'error_type': type(exc).__name__, 'retry_allowed': False}
    report.update(result, finished_utc=datetime.now(ZoneInfo('UTC')).isoformat())
    publish(path, report)
    print(json.dumps(report))
    return 0 if report['status'] in {'success', 'probe_passed'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
