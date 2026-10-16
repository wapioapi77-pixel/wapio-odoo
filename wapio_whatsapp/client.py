import re
from urllib.parse import urlsplit

import requests


class WapioError(Exception):
    """Safe to display: never includes request bodies, credentials or URLs."""


class WapioClient:
    def __init__(self, api_key):
        self.api_key = api_key.strip()
        if not self.api_key:
            raise WapioError('Configure a Wapio API key first.')

    def request(self, method, path, **kwargs):
        if path not in ('/v1/whatsapp-sessions', '/api/send-message', '/api/media'):
            raise WapioError('Unsupported Wapio endpoint.')
        headers = kwargs.pop('headers', {})
        headers.update(Authorization=f'Bearer {self.api_key}', Accept='application/json')
        try:
            response = requests.request(method, 'https://api.wapio.io' + path,
                                        headers=headers, timeout=(5, 30),
                                        allow_redirects=False, **kwargs)
        except requests.RequestException:
            raise WapioError('Wapio could not be reached. Retry the same queued message.') from None
        return self._response(response)

    @staticmethod
    def _response(response):
        if response.status_code == 401:
            raise WapioError('Wapio rejected the API key. Ask your administrator to reconnect.')
        if not 200 <= response.status_code < 300:
            raise WapioError(f'Wapio returned HTTP {response.status_code}. Check your plan, session and inputs.')
        try:
            envelope = response.json()
        except ValueError:
            raise WapioError('Wapio returned an invalid response. Retry with the same message ID.') from None
        if not isinstance(envelope, dict) or envelope.get('success') is not True:
            raise WapioError('Wapio did not confirm the request. Retry with the same message ID.')
        return envelope

    def sessions(self):
        sessions, cursor, seen = [], None, set()
        for _page in range(20):
            params = {'limit': 50}
            if cursor:
                params['before_id'] = cursor
            envelope = self.request('GET', '/v1/whatsapp-sessions', params=params)
            page = envelope.get('data')
            if not isinstance(page, list):
                raise WapioError('Wapio returned an invalid session list.')
            sessions.extend(page)
            cursor = envelope.get('next_cursor')
            if not cursor:
                return sessions
            if cursor in seen:
                break
            seen.add(cursor)
        raise WapioError('Session pagination exceeded its safety limit. Contact Wapio support.')

    def send(self, payload, message_key):
        envelope = self.request('POST', '/api/send-message', json=payload, headers={
            'x-session-id': payload['session_id'], 'Idempotency-Key': message_key,
        })
        submission = envelope.get('data')
        if not isinstance(submission, dict) or not submission.get('msgId'):
            raise WapioError('Wapio returned no message ID. Retry the same queued message.')
        return submission['msgId']

    def upload_pdf(self, pdf_bytes):
        grant = self.request('POST', '/api/media', json={
            'kind': 'document', 'mime_type': 'application/pdf', 'size_bytes': len(pdf_bytes),
        })
        upload_url = grant.get('upload_url', '')
        parsed = urlsplit(upload_url)
        if parsed.scheme != 'https' or parsed.hostname != 'media.wapio.io' or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise WapioError('Unexpected media upload host. Contact Wapio support.')
        if not grant.get('media_handle') or not isinstance(grant.get('required_headers'), dict):
            raise WapioError('Wapio returned an invalid media upload grant.')
        try:
            response = requests.put(upload_url, data=pdf_bytes,
                                    headers=grant['required_headers'],
                                    timeout=(5, 60), allow_redirects=False)
        except requests.RequestException:
            raise WapioError('PDF upload failed. Retry the same queued message.') from None
        if not 200 <= response.status_code < 300:
            raise WapioError('Wapio could not store the PDF. Retry the same queued message.')
        return grant['media_handle']


def international_phone(phone):
    normalized = re.sub(r'[\s().-]', '', phone or '')
    if not re.fullmatch(r'\+[1-9][0-9]{6,14}', normalized):
        raise WapioError('Use an international phone number beginning with + and country code.')
    return normalized
