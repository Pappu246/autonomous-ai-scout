from __future__ import annotations

import json
import os
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from autonomous_agent.calendar_connector import CALENDAR_API_ROOT, CalendarConnector
from autonomous_agent.gmail_connector import GMAIL_API_ROOT, GmailConnector

def _request_json(token, method, url, *, params=None, body=None, headers=None, timeout=10):
    if not token or any(ord(ch) < 32 for ch in token):
        raise RuntimeError('credential material is missing or malformed')
    parts = urlsplit(url)
    query = urlencode(params or {}, doseq=True)
    request_url = urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))
    request_headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json'}
    if headers:
        request_headers.update(headers)
    payload = None
    if body is not None:
        payload = json.dumps(body).encode('utf-8')
        request_headers['Content-Type'] = 'application/json'
    with urlopen(Request(request_url, data=payload, method=method, headers=request_headers), timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))

class GmailTransport:
    def __init__(self, token):
        self.token = token
    def request(self, method, url, *, params=None, body=None, timeout_seconds=10):
        if not url.startswith(GMAIL_API_ROOT + '/'):
            raise RuntimeError('unexpected Gmail API URL')
        return _request_json(self.token, method, url, params=params, body=body, timeout=timeout_seconds)

class CalendarTransport:
    def __init__(self, token):
        self.token = token
    def request(self, method, url, *, params=None, body=None, headers=None, timeout_seconds=10):
        if not url.startswith(CALENDAR_API_ROOT + '/'):
            raise RuntimeError('unexpected Calendar API URL')
        return _request_json(self.token, method, url, params=params, body=body, headers=headers, timeout=timeout_seconds)

def main():
    gmail_token = os.environ.get('SCOUT_GMAIL_ACCESS_TOKEN', '')
    calendar_token = os.environ.get('SCOUT_CALENDAR_ACCESS_TOKEN', '')
    if not gmail_token or not calendar_token:
        raise SystemExit('Gate 4 requires both live read-only OAuth access tokens.')
    gmail = GmailConnector(GmailTransport(gmail_token), credential_reference='gmail:oauth:gate4-smoke')
    calendar = CalendarConnector(CalendarTransport(calendar_token), credential_reference='calendar:oauth:gate4-smoke')
    gmail_result = gmail.search('in:inbox', results=5)
    calendar_result = calendar.list('primary', results=5)
    print(json.dumps({
        'gate': '4',
        'gmail': {
            'operation': gmail_result.operation,
            'bounded_item_count': len(gmail_result.data.get('messages', [])),
            'evidence_fingerprint': gmail_result.fingerprint,
        },
        'calendar': {
            'operation': calendar_result.operation,
            'bounded_item_count': len(calendar_result.data.get('events', [])),
            'evidence_fingerprint': calendar_result.fingerprint,
        },
        'status': 'LIVE_READ_ONLY_SMOKE_PASSED',
    }, sort_keys=True))

if __name__ == '__main__':
    main()