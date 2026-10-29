#!/usr/bin/env python3
"""Independent public HTTPS probes. No VPS, SSH, R2 or Management credentials."""
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

TARGETS = [
    ('management_https', 'https://management.puplabs-media.com/login', 'html'),
    ('player_loader', 'https://player.puplabs-media.com/loader/v1.js', 'javascript'),
    ('player_stable', 'https://player.puplabs-media.com/channels/stable.json', 'json'),
]

class MonitorError(Exception):
    pass

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, url):
        return None

def probe(target):
    name, url, kind = target
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'PupVSL-external-monitor/1', 'Cache-Control': 'no-cache', 'Connection': 'close'})
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=10) as response:
                body = response.read(1048577)
                mime = response.headers.get_content_type()
                if response.status != 200 or len(body) > 1048576:
                    raise MonitorError('INVALID_RESPONSE')
                if kind == 'html' and (mime != 'text/html' or b'PupVSL' not in body):
                    raise MonitorError('INVALID_RESPONSE')
                if kind == 'javascript' and mime not in ['application/javascript', 'text/javascript']:
                    raise MonitorError('INVALID_RESPONSE')
                if kind == 'json':
                    data = json.loads(body)
                    if mime != 'application/json' or not isinstance(data, dict) or not isinstance(data.get('version'), str):
                        raise MonitorError('INVALID_RESPONSE')
            return {'name': name, 'healthy': True, 'code': 'HTTPS_OK'}
        except Exception:
            if attempt < 2:
                time.sleep(2)
    return {'name': name, 'healthy': False, 'code': 'HTTPS_UNAVAILABLE'}

class GitHubState:
    def __init__(self, repository, token):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository) or not token:
            raise MonitorError('GITHUB_CONFIGURATION_REQUIRED')
        self.url = 'https://api.github.com/repos/' + repository + '/contents/state.json'
        self.token = token

    def request(self, method, payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(self.url, data=body, method=method, headers={
            'Authorization': 'Bearer ' + self.token, 'User-Agent': 'PupVSL-external-monitor',
            'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
            'Content-Type': 'application/json',
        })
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                data = response.read(65537)
                if len(data) > 65536:
                    raise MonitorError('GITHUB_RESPONSE_TOO_LARGE')
                return json.loads(data)
        except urllib.error.HTTPError as error:
            if method == 'GET' and error.code == 404:
                return None
            raise MonitorError('GITHUB_STATE_UNAVAILABLE') from None
        except Exception:
            raise MonitorError('GITHUB_STATE_UNAVAILABLE') from None

    def read(self):
        data = self.request('GET')
        if data is None:
            return {}, None
        try:
            state = json.loads(base64.b64decode(data['content']))
            if state.get('schemaVersion') != 1 or not isinstance(state.get('healthy'), bool):
                raise ValueError
            return state, data['sha']
        except Exception:
            raise MonitorError('GITHUB_STATE_INVALID') from None

    def write(self, state, sha):
        payload = {'message': 'chore: record public uptime state', 'content': base64.b64encode((json.dumps(state, indent=2) + '\n').encode()).decode()}
        if sha:
            payload['sha'] = sha
        return self.request('PUT', payload)['content']['sha']

def telegram(token, chat, text):
    if not token or not chat:
        raise MonitorError('TELEGRAM_CONFIGURATION_REQUIRED')
    try:
        request = urllib.request.Request('https://api.telegram.org/bot' + token + '/sendMessage', data=json.dumps({'chat_id': chat, 'text': text, 'disable_web_page_preview': True}).encode(), headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=15) as response:
            data = response.read(16385)
            if len(data) > 16384 or not json.loads(data).get('ok'):
                raise ValueError
    except Exception:
        # Telegram URLs contain the token; never propagate the original exception.
        raise MonitorError('NOTIFICATION_FAILED') from None

def run(store, checks, notify, now=None, test_notification=False):
    now = now or datetime.now(timezone.utc).isoformat()
    previous, sha = store.read()
    healthy = all(item['healthy'] for item in checks)
    notified = previous.get('notifiedHealthy', True)
    transition = notified != healthy
    state = {'schemaVersion': 1, 'checkedAt': now, 'healthy': healthy, 'checks': checks, 'notifiedHealthy': notified}
    # Persist pending notifications first. A failed send is retried by the next run.
    # Daily healthy commits also prevent public-repository schedule inactivity expiry.
    if transition or previous.get('checkedAt', '')[:10] != now[:10] or previous.get('healthy') != healthy:
        sha = store.write(state, sha)
    if transition:
        failures = '\n'.join(item['name'] + ': ' + item['code'] for item in checks if not item['healthy'])
        notify('PupVSL external monitor: ' + ('OK\nPublic HTTPS checks recovered.' if healthy else 'CRITICAL\n' + failures))
        state['notifiedHealthy'] = healthy
        store.write(state, sha)
    if test_notification:
        notify('PupVSL external monitor: TEST\nGitHub-hosted HTTPS checks are running independently of the VPS. Current state: ' + ('OK' if healthy else 'CRITICAL'))
    return {'healthy': healthy, 'checks': checks, 'notificationSent': transition, 'testNotificationSent': test_notification}

def main():
    try:
        store = GitHubState(os.environ.get('GITHUB_REPOSITORY', ''), os.environ.get('MONITOR_GITHUB_TOKEN', ''))
        token = os.environ.get('MONITOR_TELEGRAM_TOKEN', '')
        chat = os.environ.get('MONITOR_TELEGRAM_CHAT', '')
        if not token or not chat:
            raise MonitorError('TELEGRAM_CONFIGURATION_REQUIRED')
        with ThreadPoolExecutor(max_workers=3) as executor:
            checks = list(executor.map(probe, TARGETS))
        result = run(store, checks, lambda text: telegram(token, chat, text), test_notification=os.environ.get('MONITOR_TEST_NOTIFICATION') == 'true')
        print(json.dumps(result))
        return 0 if result['healthy'] else 1
    except MonitorError as error:
        print(json.dumps({'error': str(error)}))
        return 2
    except Exception:
        print('{"error":"MONITOR_FAILED"}')
        return 2

if __name__ == '__main__':
    sys.exit(main())
