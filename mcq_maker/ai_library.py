"""Durable AI prompts, copied reference files, and Windows-protected API keys."""
from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from uuid import uuid4

from .template_repository import atomic_write


PROVIDERS = {
    'google': {'name': 'Google Gemini', 'url': 'https://aistudio.google.com/apikey'},
    'openai': {'name': 'OpenAI', 'url': 'https://platform.openai.com/api-keys'},
    'anthropic': {'name': 'Anthropic', 'url': 'https://console.anthropic.com/settings/keys'},
    'openrouter': {'name': 'OpenRouter', 'url': 'https://openrouter.ai/settings/keys'},
}


class AILibraryError(RuntimeError):
    pass


class _Blob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]


def _blob(data: bytes):
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


_crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
_kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
_crypt32.CryptProtectData.argtypes = [ctypes.POINTER(_Blob), wintypes.LPCWSTR,
    ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(_Blob)]
_crypt32.CryptProtectData.restype = wintypes.BOOL
_crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p,
    ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(_Blob)]
_crypt32.CryptUnprotectData.restype = wintypes.BOOL
_kernel32.LocalFree.argtypes = [ctypes.c_void_p]
_kernel32.LocalFree.restype = ctypes.c_void_p


def protect_secret(secret: str) -> str:
    """Encrypt a key for the current Windows user with DPAPI."""
    if not secret.strip():
        raise AILibraryError('Enter an API key first.')
    incoming, keepalive = _blob(secret.encode('utf-8'))
    outgoing = _Blob()
    protected = _crypt32.CryptProtectData(
        ctypes.byref(incoming), 'MCQ Maker API key', None, None, None, 1,
        ctypes.byref(outgoing))
    # A few managed Windows profiles do not expose a user DPAPI master key.
    # Machine-scope DPAPI plus the user's LocalAppData ACL is the supported fallback.
    if not protected and ctypes.get_last_error() == 2:
        protected = _crypt32.CryptProtectData(
            ctypes.byref(incoming), 'MCQ Maker API key', None, None, None, 5,
            ctypes.byref(outgoing))
    if not protected:
        raise AILibraryError('Windows could not protect this API key.')
    try:
        return base64.b64encode(ctypes.string_at(outgoing.pbData, outgoing.cbData)).decode('ascii')
    finally:
        _kernel32.LocalFree(outgoing.pbData)


def unprotect_secret(value: str) -> str:
    try:
        encrypted = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise AILibraryError('A saved API key is damaged. Remove it and add it again.') from exc
    incoming, keepalive = _blob(encrypted)
    outgoing = _Blob()
    if not _crypt32.CryptUnprotectData(
            ctypes.byref(incoming), None, None, None, None, 1,
            ctypes.byref(outgoing)):
        raise AILibraryError('Windows could not unlock this API key for the current user.')
    try:
        return ctypes.string_at(outgoing.pbData, outgoing.cbData).decode('utf-8')
    finally:
        _kernel32.LocalFree(outgoing.pbData)


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class AILibrary:
    version = 1

    def __init__(self, root):
        self.root = Path(root) / 'ai'
        self.path = self.root / 'library.json'
        self.backup = self.root / 'library.backup.json'
        self.references = self.root / 'references'

    def defaults(self):
        return {'version': self.version, 'keys': [], 'prompts': [],
                'active_prompt_id': None, 'references': [], 'models': {}}

    def initialize(self):
        self.references.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write(self.defaults())
        data = self.load()
        if not data['prompts']:
            self.add_prompt(
                'Medical MCQ generation',
                'Create a valid MCQ Maker JSON array from the lecture. The first object must contain only a non-empty "title". Each following object must contain "question", an "options" array, a zero-based integer "correct", and a non-empty "explanation". Use the reference document to calibrate scope and difficulty. Return JSON only.',
            )
        return self.load()

    def load(self):
        try:
            return self._validate(json.loads(self.path.read_text(encoding='utf-8')))
        except (OSError, ValueError, TypeError) as exc:
            try:
                raw = self.backup.read_bytes()
                data = self._validate(json.loads(raw.decode('utf-8')))
                atomic_write(self.path, raw)
                return data
            except (OSError, ValueError, TypeError):
                raise AILibraryError('The AI library could not be opened. Its files have not been changed.') from exc

    def _validate(self, data):
        if not isinstance(data, dict) or data.get('version') != self.version:
            raise ValueError
        for field in ('keys', 'prompts', 'references'):
            if not isinstance(data.get(field), list):
                raise ValueError
        if not isinstance(data.setdefault('models', {}), dict):
            raise ValueError
        return data

    def _write(self, data):
        self.root.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            atomic_write(self.backup, self.path.read_bytes())
        atomic_write(self.path, json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8'))

    def add_key(self, provider, nickname, secret):
        if provider not in PROVIDERS:
            raise AILibraryError('Choose a supported provider.')
        nickname = nickname.strip()
        if not nickname:
            raise AILibraryError('Give this API key a name, such as Personal account.')
        data = self.load()
        item = {'id': str(uuid4()), 'provider': provider, 'nickname': nickname[:80],
                'secret': protect_secret(secret), 'status': 'connected',
                'rate_limited_until': None, 'created_at': _now()}
        data['keys'].append(item)
        self._write(data)
        return item['id']

    def secret(self, key_id):
        item = next((x for x in self.load()['keys'] if x['id'] == key_id), None)
        if item is None:
            raise AILibraryError('That API key is no longer available.')
        return unprotect_secret(item['secret'])

    def update_key_status(self, key_id, status, limited_until=None):
        data = self.load()
        for item in data['keys']:
            if item['id'] == key_id:
                item['status'] = status
                item['rate_limited_until'] = limited_until
                self._write(data)
                return

    def set_models(self, provider, models):
        if provider not in PROVIDERS:
            raise AILibraryError('Choose a supported provider.')
        cleaned = []
        for model in models:
            model_id, display_name = model[:2]
            thinking = bool(model[2]) if len(model) > 2 else True
            if isinstance(model_id, str) and model_id and isinstance(display_name, str) and display_name:
                cleaned.append({'id': model_id[:160], 'name': display_name[:160], 'thinking': thinking})
        data = self.load()
        data['models'][provider] = cleaned
        self._write(data)

    def remove_key(self, key_id):
        data = self.load()
        data['keys'] = [x for x in data['keys'] if x['id'] != key_id]
        self._write(data)

    def add_prompt(self, name, text):
        name, text = name.strip(), text.strip()
        if not name or not text:
            raise AILibraryError('A prompt needs both a name and instructions.')
        data = self.load()
        versions = [x['version'] for x in data['prompts'] if x['name'].casefold() == name.casefold()]
        item = {'id': str(uuid4()), 'name': name[:100], 'version': max(versions, default=0) + 1,
                'text': text, 'created_at': _now()}
        data['prompts'].append(item)
        data['active_prompt_id'] = item['id']
        self._write(data)
        return item['id']

    def set_active_prompt(self, prompt_id):
        data = self.load()
        if not any(x['id'] == prompt_id for x in data['prompts']):
            raise AILibraryError('That prompt version is no longer available.')
        data['active_prompt_id'] = prompt_id
        self._write(data)

    def remove_prompt(self, prompt_id):
        data = self.load()
        remaining = [item for item in data['prompts'] if item['id'] != prompt_id]
        if len(remaining) == len(data['prompts']):
            raise AILibraryError('That prompt version is no longer available.')
        if not remaining:
            raise AILibraryError('Keep at least one prompt so generation always has instructions.')
        data['prompts'] = remaining
        if data.get('active_prompt_id') == prompt_id:
            newest = max(remaining, key=lambda item: item.get('created_at', ''))
            data['active_prompt_id'] = newest['id']
        self._write(data)

    def add_reference(self, source, subject, topic='', course='', notes=''):
        source = Path(source)
        if not source.is_file():
            raise AILibraryError('Choose an available reference file.')
        if source.suffix.casefold() not in {'.pdf', '.txt', '.md'}:
            raise AILibraryError('Choose a PDF, TXT, or Markdown reference file.')
        if source.stat().st_size > 50 * 1024 * 1024:
            raise AILibraryError('Reference files must be 50 MB or smaller.')
        subject = subject.strip()
        if not subject:
            raise AILibraryError('Enter a subject name for this reference.')
        identifier = str(uuid4())
        target = self.references / f'{identifier}{source.suffix.casefold()}'
        self.references.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + '.part')
        shutil.copyfile(source, temporary)
        temporary.replace(target)
        data = self.load()
        data['references'].append({'id': identifier, 'name': source.stem[:100],
            'subject': subject[:100], 'topic': topic.strip()[:100],
            'course': course.strip()[:100], 'notes': notes.strip()[:1000],
            'filename': target.name, 'original_filename': source.name,
            'created_at': _now()})
        self._write(data)
        return identifier

    def reference_path(self, reference_id):
        item = next((x for x in self.load()['references'] if x['id'] == reference_id), None)
        if item is None:
            raise AILibraryError('That reference file is no longer available.')
        path = self.references / item['filename']
        if not path.is_file():
            raise AILibraryError('The stored reference file is missing. Add it again.')
        return path

    def remove_reference(self, reference_id):
        data = self.load()
        item = next((x for x in data['references'] if x['id'] == reference_id), None)
        if item:
            (self.references / item['filename']).unlink(missing_ok=True)
            data['references'] = [x for x in data['references'] if x['id'] != reference_id]
            self._write(data)
