"""Versioned, recoverable per-user preferences."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

from PySide6.QtCore import QStandardPaths

from .template_repository import atomic_write


def default_output_folder() -> str:
    documents = Path(QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation))
    return str(documents / 'MCQ Maker Exams')


DEFAULTS = {
    'version': 1,
    'output_folder': None,
    'open_after_manual_generation': False,
    'manual_conflicts': 'save_copy',
    'include_subfolders': False,
    'clipboard_watcher': False,
    'close_behavior': 'quit',
    'tray_explanation_shown': False,
    'start_with_windows': False,
    'notifications': True,
    'notification_sounds': False,
    'theme': 'system',
    'ai_studio_brave_executable': None,
    'ai_studio_model': None,
    'ai_studio_thinking': None,
    # Keep the live-proven single-tab workflow as the default.  Two tabs is an
    # explicitly selected, experimental queue setting.
    'ai_studio_parallel_tabs': 1,
    'window': None,
}


class SettingsError(ValueError):
    pass


class SettingsStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = self.root / 'settings.json'
        self.backup = self.root / 'settings.backup.json'

    def defaults(self):
        settings = deepcopy(DEFAULTS)
        settings['output_folder'] = default_output_folder()
        return settings

    def validate(self, candidate):
        if not isinstance(candidate, dict) or candidate.get('version') != 1:
            raise SettingsError('Settings format is not supported.')
        settings = self.defaults()
        for key in settings:
            if key in candidate:
                settings[key] = candidate[key]
        if not isinstance(settings['output_folder'], str) or not Path(settings['output_folder']).is_absolute():
            raise SettingsError('Output folder must be an absolute Windows folder path.')
        if settings['manual_conflicts'] not in {'ask', 'save_copy'}:
            raise SettingsError('Manual conflict preference is invalid.')
        if settings['close_behavior'] not in {'quit', 'tray'}:
            raise SettingsError('Close behavior is invalid.')
        if settings['theme'] not in {'system', 'light', 'dark'}:
            raise SettingsError('Theme preference is invalid.')
        brave = settings['ai_studio_brave_executable']
        if brave is not None:
            if not isinstance(brave, str):
                raise SettingsError('Brave executable preference is invalid.')
            brave = brave.strip()
            if brave and not Path(brave).is_absolute():
                raise SettingsError('Brave executable must be a complete Windows path.')
            settings['ai_studio_brave_executable'] = brave or None
        for key, label in (('ai_studio_model', 'AI Studio model'),
                           ('ai_studio_thinking', 'AI Studio thinking preference')):
            value = settings[key]
            if value is not None and not isinstance(value, str):
                raise SettingsError(f'{label} preference is invalid.')
            value = value.strip() if isinstance(value, str) else None
            if value is not None and len(value) > 160:
                raise SettingsError(f'{label} preference is too long.')
            settings[key] = value or None
        if type(settings['ai_studio_parallel_tabs']) is not int or settings['ai_studio_parallel_tabs'] not in {1, 2}:
            raise SettingsError('AI Studio parallel tabs preference must be 1 or 2.')
        for key in ('open_after_manual_generation', 'include_subfolders', 'clipboard_watcher',
                    'start_with_windows', 'notifications', 'notification_sounds',
                    'tray_explanation_shown'):
            if type(settings[key]) is not bool:
                raise SettingsError(f'{key} must be on or off.')
        window = settings['window']
        if window is not None and (not isinstance(window, dict) or not all(type(window.get(key)) is int for key in ('x', 'y', 'width', 'height'))):
            raise SettingsError('Saved window position is invalid.')
        return settings

    def load(self):
        if not self.path.exists():
            return self.defaults()
        try:
            return self.validate(json.loads(self.path.read_text(encoding='utf-8')))
        except (OSError, json.JSONDecodeError, SettingsError):
            self.root.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
            preserved = self.root / f'settings.corrupt.{stamp}.json'
            try:
                self.path.replace(preserved)
            except OSError:
                pass
            return self.defaults()

    def save(self, settings):
        settings = self.validate(settings)
        self.root.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                atomic_write(self.backup, self.path.read_bytes())
            except OSError as exc:
                raise SettingsError('MCQ Maker could not back up your previous settings.') from exc
        try:
            atomic_write(self.path, json.dumps(settings, ensure_ascii=False, indent=2).encode('utf-8'))
        except OSError as exc:
            raise SettingsError('MCQ Maker could not save settings. Check that its local data folder is writable.') from exc
        return settings
