"""Local template snapshots, metadata, default selection, and recovery."""
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from uuid import uuid4, UUID
from .app_data import app_data_root
from .default_resources import DefaultResourceStore
from .template_validation import TemplateError, MAX_TEMPLATE_BYTES, decode_template, validate_template, render_preview

def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def write_json(path, data):
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8'))

class TemplateRepository:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else app_data_root()
        self.templates = self.root / 'templates'
        self.catalog = self.templates / 'catalog.json'

    def _directory(self, template_id):
        try:
            if str(UUID(template_id)) != template_id:
                raise ValueError
        except (ValueError, TypeError, AttributeError) as exc:
            raise TemplateError('The template identifier is invalid. Refresh the template library.') from exc
        target = self.templates / template_id
        if target.resolve().parent != self.templates.resolve() or target.is_symlink():
            raise TemplateError('The template folder is outside the managed library.')
        return target

    def _catalog(self):
        try:
            data = json.loads(self.catalog.read_text(encoding='utf-8'))
            if data.get('version') != 1:
                raise ValueError
            return data
        except (json.JSONDecodeError, ValueError, AttributeError) as exc:
            raise TemplateError('The template catalog needs attention. Your stored HTML files are still intact.') from exc

    def initialize(self):
        self.templates.mkdir(parents=True, exist_ok=True)
        if not self.catalog.exists():
            # A first-run marker is committed before importing; an empty library is recoverable.
            write_json(self.catalog, {'version': 1, 'default_id': None, 'seeded': False})
        catalog = self._catalog()
        if not catalog.get('seeded'):
            existing = self.list_templates()['templates']
            if not existing:
                # Import an independent per-user seed, rather than relying on a
                # checkout-only path or a mutable bundled location at runtime.
                bundled = DefaultResourceStore(self.root).default_path('template')
                self.import_template(bundled, 'Standard exam', bundled=True)
            catalog = self._catalog()
            catalog['seeded'] = True
            write_json(self.catalog, catalog)

    def _read_source(self, path):
        path = Path(path).resolve()
        if path.suffix.lower() not in ('.html', '.htm'):
            raise TemplateError('Choose an HTML file ending in .html or .htm.')
        if not path.is_file():
            raise TemplateError('That template file could not be found. Choose it again.')
        with path.open('rb') as stream:
            data = stream.read(MAX_TEMPLATE_BYTES+1)
        text = decode_template(data)
        contract = validate_template(text)
        render_preview(text)  # Exercise the exact replacement path before committing an import.
        return path, data, text, contract

    def _name(self, name, excluding=None):
        name = name.strip()
        if not name or len(name) > 100 or any(ord(c) < 32 for c in name):
            raise TemplateError('Use a template name with 1–100 characters and no line breaks.')
        existing = {t['display_name'].casefold() for t in self.list_templates()['templates'] if t['id'] != excluding}
        if name.casefold() in existing:
            raise TemplateError('A template already has that name. Choose a different display name.')
        return name

    def import_template(self, path, name=None, bundled=False):
        path, data, text, contract = self._read_source(path)
        name = self._name(name or path.stem)
        template_id = str(uuid4())
        directory = self._directory(template_id)
        normalized = text.encode('utf-8')
        metadata = {'metadata_version': 1, 'id': template_id, 'display_name': name,
                    'original_filename': path.name, 'source_path': None if bundled else str(path),
                    'source_mtime_ns': path.stat().st_mtime_ns, 'source_sha256': sha256(data).hexdigest(),
                    'sha256': sha256(normalized).hexdigest(), 'imported_at': timestamp(), 'updated_at': timestamp(),
                    'revision': 1, 'warnings_acknowledged': False, **asdict(contract)}
        # Metadata is the commit point: a partial HTML-only directory is not a library entry.
        atomic_write(directory / 'template.html', normalized)
        write_json(directory / 'metadata.json', metadata)
        catalog = self._catalog()
        if catalog.get('default_id') is None:
            catalog['default_id'] = template_id
            write_json(self.catalog, catalog)
        return template_id

    def _metadata(self, template_id):
        directory = self._directory(template_id)
        if not directory.is_dir():
            raise TemplateError('This template is no longer in the library. Refresh the list.')
        journal = directory / 'update.json'
        if journal.exists():
            self._recover_update(directory)
        try:
            metadata = json.loads((directory / 'metadata.json').read_text(encoding='utf-8'))
            if metadata['id'] != template_id or metadata['metadata_version'] != 1:
                raise ValueError
            for key in ('display_name', 'sha256', 'updated_at'):
                if not isinstance(metadata[key], str):
                    raise ValueError
            return metadata
        except (ValueError, KeyError, TypeError) as exc:
            raise TemplateError('The stored template details are damaged. Re-import the original HTML file.') from exc

    def read_template(self, template_id):
        metadata = self._metadata(template_id)
        path = self._directory(template_id) / 'template.html'
        with path.open('rb') as stream:
            data = stream.read(MAX_TEMPLATE_BYTES+1)
        if sha256(data).hexdigest() != metadata['sha256']:
            raise TemplateError('The stored copy has changed outside MCQ Maker. Re-import a trusted HTML file to update it.')
        text = decode_template(data)
        validate_template(text)
        return text

    def list_templates(self):
        catalog = self._catalog()
        entries = []
        for directory in sorted(self.templates.iterdir()):
            if not directory.is_dir() or not (directory / 'metadata.json').exists():
                continue
            try:
                entry = self._metadata(directory.name)
            except (TemplateError, OSError):
                # Keep damaged entries visible so they can be removed or re-imported.
                try:
                    self._directory(directory.name)
                except TemplateError:
                    continue
                entry = {'id': directory.name, 'display_name': 'Template needs recovery', 'updated_at': '',
                         'minimum_options': 2, 'maximum_options': 10, 'source_path': None}
            entry = dict(entry)
            entry['is_default'] = entry['id'] == catalog.get('default_id')
            entry['valid'] = True
            entry['problem'] = ''
            entry['source_changed'] = False
            try:
                self.read_template(entry['id'])
            except (TemplateError, OSError) as exc:
                entry['valid'] = False
                entry['problem'] = str(exc) if isinstance(exc, TemplateError) else 'The stored HTML file cannot be read. Re-import its source.'
            source = entry.get('source_path')
            if source:
                try:
                    with Path(source).open('rb') as stream:
                        data = stream.read(MAX_TEMPLATE_BYTES+1)
                    entry['source_changed'] = sha256(data).hexdigest() != entry.get('source_sha256')
                except OSError:
                    pass  # Missing originals do not invalidate the independent imported copy.
            entries.append(entry)
        entries.sort(key=lambda item: (not item['is_default'], item['display_name'].casefold()))
        return {'templates': entries, 'default_id': catalog.get('default_id')}

    def rename(self, template_id, name):
        name = self._name(name, excluding=template_id)
        metadata = self._metadata(template_id)
        metadata['display_name'] = name
        write_json(self._directory(template_id) / 'metadata.json', metadata)
        return template_id

    def set_default(self, template_id):
        self.read_template(template_id)
        catalog = self._catalog()
        catalog['default_id'] = template_id
        write_json(self.catalog, catalog)
        return template_id

    def acknowledge_warnings(self, template_id):
        metadata = self._metadata(template_id)
        metadata['warnings_acknowledged'] = True
        write_json(self._directory(template_id) / 'metadata.json', metadata)
        return template_id

    def remove(self, template_id):
        directory = self._directory(template_id)
        entries = self.list_templates()['templates']
        remaining = [t for t in entries if t['id'] != template_id and t['valid']]
        catalog = self._catalog()
        if catalog.get('default_id') == template_id:
            catalog['default_id'] = remaining[0]['id'] if remaining else None
        archive = self.templates / '.removed'
        archive.mkdir(exist_ok=True)
        destination = archive / (template_id + '-' + uuid4().hex[:8])
        directory.rename(destination)
        try:
            write_json(self.catalog, catalog)
        except OSError:
            destination.rename(directory)
            raise
        return catalog.get('default_id')

    def replace(self, template_id, path):
        path, raw, text, contract = self._read_source(path)
        directory = self._directory(template_id)
        old = self._metadata(template_id)
        old_data = (directory / 'template.html').read_bytes()
        backup_id = uuid4().hex
        backup = directory / 'revisions' / backup_id
        atomic_write(backup.with_suffix('.html'), old_data)
        write_json(backup.with_suffix('.json'), old)
        normalized = text.encode('utf-8')
        updated = dict(old, source_path=str(path), original_filename=path.name,
                       source_mtime_ns=path.stat().st_mtime_ns, source_sha256=sha256(raw).hexdigest(),
                       sha256=sha256(normalized).hexdigest(), updated_at=timestamp(), revision=old['revision']+1,
                       warnings_acknowledged=False, **asdict(contract))
        write_json(directory / 'update.json', {'backup_id': backup_id, 'old': old, 'new': updated})
        try:
            atomic_write(directory / 'template.html', normalized)
            write_json(directory / 'metadata.json', updated)
            (directory / 'update.json').unlink()
        except OSError:
            self._recover_update(directory)
            raise
        return template_id

    def _recover_update(self, directory):
        journal = json.loads((directory / 'update.json').read_text(encoding='utf-8'))
        backup_id = journal['backup_id']
        if not isinstance(backup_id, str) or len(backup_id) != 32 or any(c not in '0123456789abcdef' for c in backup_id):
            raise TemplateError('The interrupted template update needs recovery.')
        try:
            current = json.loads((directory / 'metadata.json').read_text(encoding='utf-8'))
            data = (directory / 'template.html').read_bytes()
            committed = current == journal['new'] and sha256(data).hexdigest() == current['sha256']
        except (OSError, ValueError, KeyError):
            committed = False
        if not committed:
            atomic_write(directory / 'template.html', (directory / 'revisions' / f'{backup_id}.html').read_bytes())
            write_json(directory / 'metadata.json', journal['old'])
        (directory / 'update.json').unlink()

    def preview(self, template_id):
        rendered = render_preview(self.read_template(template_id))
        target = self.root / 'previews' / f'{template_id}.html'
        atomic_write(target, rendered.encode('utf-8'))
        return target

    def recheck(self, template_id):
        metadata = self._metadata(template_id)
        source = metadata.get('source_path')
        if not source:
            raise TemplateError('This included template has no original file to recheck.')
        path = Path(source)
        if not path.is_file():
            raise TemplateError('The original template file is no longer available. Use Re-import to choose its current location.')
        return self.replace(template_id, path)
