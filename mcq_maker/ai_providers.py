"""Provider-isolated HTTP adapters for AI generation."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
import json
import mimetypes
from pathlib import Path
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


MODELS = {
    'google': [('gemini-3.8-flash', 'Gemini 3.8 Flash', True)],
    'openai': [('gpt-5-mini', 'GPT-5 mini')],
    'anthropic': [('claude-sonnet-5', 'Claude Sonnet 5')],
    'openrouter': [('google/gemini-3.8-flash', 'Gemini 3.8 Flash via OpenRouter'),
                   ('openrouter/auto', 'OpenRouter Auto')],
}

GOOGLE_INLINE_FILE_BYTES = 8 * 1024 * 1024
GOOGLE_FILE_POLL_INTERVAL = 1.0
GOOGLE_FILE_POLL_ATTEMPTS = 60


class ProviderError(RuntimeError):
    pass


class AuthenticationError(ProviderError):
    pass


class RateLimitError(ProviderError):
    def __init__(self, message, retry_after=60):
        super().__init__(message)
        self.retry_after = max(1, min(int(retry_after), 24 * 60 * 60))


class TemporaryProviderError(ProviderError):
    def __init__(self, message, retry_after=10):
        super().__init__(message)
        self.retry_after = max(1, min(int(retry_after), 10 * 60))


def _retry_seconds(headers, body):
    value = headers.get('Retry-After') if headers else None
    if value:
        try:
            return max(1, int(float(value)))
        except ValueError:
            try:
                return max(1, int((parsedate_to_datetime(value).timestamp() - time.time())))
            except Exception:
                pass
    try:
        details = body.get('error', {}).get('details', [])
        for detail in details:
            delay = detail.get('retryDelay') or detail.get('retry_delay')
            if isinstance(delay, str) and delay.endswith('s'):
                return max(1, int(float(delay[:-1])))
    except (AttributeError, ValueError):
        pass
    return 60


def request_json(url, *, key, payload=None, headers=None, method=None, timeout=180):
    headers = {'Content-Type': 'application/json', **(headers or {})}
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode('utf-8')
    request = Request(url, data=data, headers=headers, method=method or ('POST' if data else 'GET'))
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8')), response.headers
    except HTTPError as exc:
        raw = exc.read().decode('utf-8', errors='replace')
        try:
            body = json.loads(raw)
            message = body.get('error', {}).get('message') or body.get('message') or raw
        except ValueError:
            body, message = {}, raw
        message = str(message).strip()[:500]
        if exc.code == 401:
            raise AuthenticationError('Google rejected this API key. It may be invalid or revoked; check it in AI Library.') from exc
        if exc.code == 403:
            raise AuthenticationError('Google denied this request for the configured project, account, region, or terms-of-service status.') from exc
        if exc.code == 429:
            raise RateLimitError('This account reached its current API limit.', _retry_seconds(exc.headers, body)) from exc
        if exc.code in (408, 500, 502, 503, 504):
            wait = _retry_seconds(exc.headers, body)
            if wait == 60 and exc.code == 503:
                wait = 10
            raise TemporaryProviderError(
                'The provider is temporarily busy or unavailable.', wait) from exc
        raise ProviderError(f'The provider returned error {exc.code}: {message}') from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ProviderError('The provider could not be reached. Check the internet connection and try again.') from exc
    except ValueError as exc:
        raise ProviderError('The provider returned an unreadable response.') from exc


def _file_part(path, google=False):
    path = Path(path)
    if path.stat().st_size > 50 * 1024 * 1024:
        raise ProviderError(f'“{path.name}” is larger than the 50 MB direct-upload limit.')
    mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
    encoded = base64.b64encode(path.read_bytes()).decode('ascii')
    if google:
        return {'inlineData': {'mimeType': mime, 'data': encoded}}
    return mime, encoded


def _plain_instruction():
    return ('Generate the MCQ Maker quiz for this lecture now. Return one JSON array only, '
            'with the title object first and question objects after it. Do not use Markdown fences.')


class ProviderSession:
    def calibrate(self, prompt, reference):
        raise NotImplementedError

    def generate(self, lecture):
        raise NotImplementedError

    def close(self):
        """Release session-owned resources when a conversation group ends."""
        return None


class GoogleSession(ProviderSession):
    def __init__(self, key, model, thinking):
        self.key, self.model, self.thinking = key, model, thinking
        self.history = []
        self.system_prompt = ''
        self._uploaded_files = []

    @property
    def url(self):
        return (f'https://generativelanguage.googleapis.com/v1beta/models/'
                f'{quote(self.model)}:generateContent?key={quote(self.key)}')

    def _upload_file(self, path):
        path = Path(path)
        if path.stat().st_size > 50 * 1024 * 1024:
            raise ProviderError('The selected file is too large for Google Gemini.')
        mime = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        boundary = 'mcq-maker-' + uuid.uuid4().hex
        metadata = json.dumps({'file': {'display_name': path.name}}, ensure_ascii=False).encode('utf-8')
        content = path.read_bytes()
        body = (b'--' + boundary.encode() + b'\r\n'
                + b'Content-Type: application/json; charset=UTF-8\r\n\r\n' + metadata + b'\r\n'
                + b'--' + boundary.encode() + b'\r\n'
                + f'Content-Type: {mime}\r\n\r\n'.encode() + content + b'\r\n'
                + b'--' + boundary.encode() + b'--\r\n')
        request = Request(
            f'https://generativelanguage.googleapis.com/upload/v1beta/files?key={quote(self.key)}',
            data=body,
            headers={'Content-Type': f'multipart/related; boundary={boundary}'},
            method='POST')
        try:
            with urlopen(request, timeout=180) as response:
                created = json.loads(response.read().decode('utf-8'))
        except HTTPError as exc:
            if exc.code == 403:
                raise AuthenticationError('Google denied file upload for this project or account.') from exc
            raise ProviderError(f'Google could not upload the file (HTTP {exc.code}).') from exc
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            raise ProviderError('Google could not upload the file. Check the internet connection and try again.') from exc
        info = created.get('file', created) if isinstance(created, dict) else {}
        name = info.get('name') if isinstance(info, dict) else None
        uri = info.get('uri') if isinstance(info, dict) else None
        state = str(info.get('state', '')).upper() if isinstance(info, dict) else ''
        if not name or not uri:
            raise ProviderError('Google returned an unusable uploaded-file record.')
        for _ in range(GOOGLE_FILE_POLL_ATTEMPTS):
            if state in {'ACTIVE', 'READY'}:
                self._uploaded_files.append(name)
                return {'fileData': {'mimeType': mime, 'fileUri': uri}}
            if state in {'FAILED', 'ERROR'}:
                raise ProviderError('Google could not finish processing the uploaded file.')
            try:
                details, _ = request_json(
                    f'https://generativelanguage.googleapis.com/v1beta/{quote(name, safe="/")}?key={quote(self.key)}',
                    key=self.key, timeout=30)
            except ProviderError as exc:
                raise ProviderError('Google could not check the uploaded file status.') from exc
            info = details.get('file', details) if isinstance(details, dict) else {}
            state = str(info.get('state', '')).upper()
            uri = info.get('uri', uri) if isinstance(info, dict) else uri
            if state in {'ACTIVE', 'READY'}:
                self._uploaded_files.append(name)
                return {'fileData': {'mimeType': mime, 'fileUri': uri}}
            time.sleep(GOOGLE_FILE_POLL_INTERVAL)
        raise TemporaryProviderError('Google is still processing the uploaded file.', 10)

    def _google_file_part(self, path):
        path = Path(path)
        if not path.is_file():
            raise ProviderError(f'The selected file "{path.name}" is unavailable.')
        if path.stat().st_size <= GOOGLE_INLINE_FILE_BYTES:
            return _file_part(path, True)
        return self._upload_file(path)

    def _delete_uploaded_files(self):
        names, self._uploaded_files = list(self._uploaded_files), []
        for name in names:
            try:
                request = Request(
                    f'https://generativelanguage.googleapis.com/v1beta/{quote(name, safe="/")}?key={quote(self.key)}',
                    method='DELETE')
                with urlopen(request, timeout=30):
                    pass
            except Exception:
                continue

    def _call(self, contents, structured=False):
        config = {}
        if self.thinking:
            config['thinkingConfig'] = {'thinkingLevel': self.thinking.upper()}
        if structured:
            # JSON mode accepts the required root array. The local validator is
            # deliberately authoritative because provider schema subsets differ.
            config['responseMimeType'] = 'application/json'
        payload = {'contents': contents, 'generationConfig': config}
        if self.system_prompt:
            payload['systemInstruction'] = {'parts': [{'text': self.system_prompt}]}
        body, _ = request_json(self.url, key=self.key, payload=payload)
        try:
            content = body['candidates'][0]['content']
            text = ''.join(part.get('text', '') for part in content.get('parts', [])
                           if not part.get('thought'))
        except (KeyError, IndexError, TypeError) as exc:
            reason = body.get('promptFeedback', {}).get('blockReason', 'empty response')
            raise ProviderError(f'Google did not return usable text ({reason}).') from exc
        if not text.strip():
            raise ProviderError('Google returned an empty response.')
        return content, text

    def calibrate(self, prompt, reference):
        self.system_prompt = prompt
        user = {'role': 'user', 'parts': [
            self._google_file_part(reference),
            {'text': 'Study the attached reference for calibration only. Reply with exactly CALIBRATION COMPLETE.'},
        ]}
        model, text = self._call([user])
        if text.strip() != 'CALIBRATION COMPLETE':
            raise ProviderError('Google did not confirm calibration. The lecture was not started.')
        self.history = [user, model]
        return text

    def generate(self, lecture):
        # The calibration turn already contains the instructions. The lecture
        # turn must contain only the new PDF part.
        user = {'role': 'user', 'parts': [self._google_file_part(lecture)]}
        model, text = self._call(self.history + [user], True)
        self.history.extend((user, model))
        return text

    def close(self):
        self._delete_uploaded_files()


class OpenAIResponsesSession(ProviderSession):
    def __init__(self, key, model, thinking):
        self.key, self.model, self.thinking = key, model, thinking
        self.previous = None

    def _call(self, text, path, structured=False):
        mime, encoded = _file_part(path)
        payload = {'model': self.model, 'reasoning': {'effort': self.thinking},
                   'input': [{'role': 'user', 'content': [
                       {'type': 'input_text', 'text': text},
                       {'type': 'input_file', 'filename': Path(path).name,
                        'file_data': f'data:{mime};base64,{encoded}'},
                   ]}]}
        if self.previous:
            payload['previous_response_id'] = self.previous
        body, _ = request_json('https://api.openai.com/v1/responses', key=self.key, payload=payload,
                               headers={'Authorization': f'Bearer {self.key}'})
        self.previous = body.get('id')
        text_out = ''.join(part.get('text', '') for item in body.get('output', [])
                           for part in item.get('content', []) if part.get('type') == 'output_text')
        if not text_out.strip():
            raise ProviderError('OpenAI returned an empty response.')
        return text_out

    def calibrate(self, prompt, reference):
        return self._call(prompt + '\n\nConfirm calibration briefly.', reference)

    def generate(self, lecture):
        return self._call(_plain_instruction(), lecture, True)


class AnthropicSession(ProviderSession):
    def __init__(self, key, model, thinking):
        self.key, self.model, self.thinking = key, model, thinking
        self.history = []

    def _call(self, text, path):
        mime, encoded = _file_part(path)
        if mime == 'application/pdf':
            content = [{'type': 'document', 'source': {'type': 'base64', 'media_type': mime, 'data': encoded}},
                       {'type': 'text', 'text': text}]
        else:
            source_text = Path(path).read_text(encoding='utf-8-sig')
            content = [{'type': 'text', 'text': f'{text}\n\nREFERENCE FILE:\n{source_text}'}]
        messages = self.history + [{'role': 'user', 'content': content}]
        payload = {'model': self.model, 'max_tokens': 32000, 'messages': messages,
                   'thinking': {'type': 'adaptive'},
                   'output_config': {'effort': self.thinking}}
        body, _ = request_json('https://api.anthropic.com/v1/messages', key=self.key, payload=payload,
                               headers={'x-api-key': self.key, 'anthropic-version': '2023-06-01'})
        text_out = ''.join(block.get('text', '') for block in body.get('content', [])
                           if block.get('type') == 'text')
        if not text_out.strip():
            raise ProviderError('Anthropic returned an empty response.')
        self.history.extend((messages[-1], {'role': 'assistant', 'content': body['content']}))
        return text_out

    def calibrate(self, prompt, reference):
        return self._call(prompt + '\n\nConfirm calibration briefly.', reference)

    def generate(self, lecture):
        return self._call(_plain_instruction(), lecture)


class OpenRouterSession(ProviderSession):
    def __init__(self, key, model, thinking):
        self.key, self.model, self.thinking = key, model, thinking
        self.history = []

    def _call(self, text, path):
        mime, encoded = _file_part(path)
        content = [{'type': 'text', 'text': text},
                   {'type': 'file', 'file': {'filename': Path(path).name,
                                             'file_data': f'data:{mime};base64,{encoded}'}}]
        self.history.append({'role': 'user', 'content': content})
        payload = {'model': self.model, 'messages': self.history,
                   'reasoning': {'effort': self.thinking}}
        body, _ = request_json('https://openrouter.ai/api/v1/chat/completions', key=self.key,
                               payload=payload, headers={'Authorization': f'Bearer {self.key}',
                               'HTTP-Referer': 'https://mcq-maker.local', 'X-Title': 'MCQ Maker'})
        try:
            message = body['choices'][0]['message']
            text_out = message['content']
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError('OpenRouter returned an empty response.') from exc
        self.history.append(message)
        return text_out

    def calibrate(self, prompt, reference):
        return self._call(prompt + '\n\nConfirm calibration briefly.', reference)

    def generate(self, lecture):
        return self._call(_plain_instruction(), lecture)


def create_session(provider, key, model, thinking):
    classes = {'google': GoogleSession, 'openai': OpenAIResponsesSession,
               'anthropic': AnthropicSession, 'openrouter': OpenRouterSession}
    try:
        return classes[provider](key, model, thinking)
    except KeyError as exc:
        raise ProviderError('That provider is not supported yet.') from exc


def test_connection(provider, key):
    if provider == 'google':
        url = f'https://generativelanguage.googleapis.com/v1beta/models?key={quote(key)}&pageSize=1'
        request_json(url, key=key, timeout=30)
    elif provider == 'anthropic':
        request_json('https://api.anthropic.com/v1/models?limit=1', key=key, timeout=30,
                     headers={'x-api-key': key, 'anthropic-version': '2023-06-01'})
    else:
        url = 'https://api.openai.com/v1/models' if provider == 'openai' else 'https://openrouter.ai/api/v1/models'
        request_json(url, key=key, timeout=30, headers={'Authorization': f'Bearer {key}'})
    return True


def list_models(provider, key):
    """Return currently available generation models for the configured account."""
    if provider == 'google':
        body, _ = request_json(
            f'https://generativelanguage.googleapis.com/v1beta/models?key={quote(key)}&pageSize=1000',
            key=key, timeout=45)
        models = []
        unavailable = {'gemini-2.5-flash-lite', 'gemini-3.1-flash-lite-preview'}
        for item in body.get('models', []):
            methods = item.get('supportedGenerationMethods', [])
            model_id = str(item.get('name', '')).removeprefix('models/')
            unsuitable = ('image', 'tts', 'live', 'embedding', 'transcribe',
                          'robotics', 'omni', 'computer-use', 'customtools')
            if (model_id.startswith('gemini-') and 'generateContent' in methods
                    and model_id not in unavailable
                    and not any(word in model_id.casefold() for word in unsuitable)):
                # Google's model listing does not consistently publish a
                # dedicated thinking-capability field. Gemini 2.5 and newer
                # accept thinkingConfig; older generations keep the control
                # disabled so selecting them cannot create an invalid request.
                supports_thinking = (bool(item.get('thinking', False))
                                     or model_id.startswith(('gemini-2.5-', 'gemini-3')))
                models.append((model_id, item.get('displayName') or model_id,
                               supports_thinking))
        if not models:
            raise ProviderError('Google did not report any compatible Gemini models for this key.')
        unique = sorted(set(models), key=lambda item: item[1].casefold())
        return sorted(unique, key=lambda item: (item[0] != 'gemini-3.8-flash', item[1].casefold()))
    # Other providers remain on their tested defaults until their dedicated
    # verification pass; their model APIs do not expose identical capabilities.
    return MODELS.get(provider, [])
