"""Versioned SQLite history containing metadata, never question payloads."""
from dataclasses import dataclass
from contextlib import closing
from datetime import datetime, timezone
from hashlib import sha256
import csv
import io
import json
from pathlib import Path
import sqlite3
from threading import RLock
from uuid import uuid4

from .template_repository import atomic_write


class HistoryError(RuntimeError):
    pass


@dataclass(frozen=True)
class HistoryEntry:
    id: str
    created_utc: str
    title: str
    output_filename: str
    output_path: str
    template_id: str
    template_name: str
    template_version: int
    template_hash: str
    question_count: int
    source_mode: str
    source_path: str
    input_hash: str
    outcome: str
    error_summary: str


def payload_hash(payload) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                           sort_keys=True, separators=(',', ':')).encode('utf-8')
    return sha256(canonical).hexdigest()


class HistoryRepository:
    schema_version = 1

    def __init__(self, root):
        self.path = Path(root) / 'history.sqlite3'
        self._lock = RLock()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA busy_timeout = 5000')
        return connection

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._lock, closing(self._connect()) as connection, connection:
                version = connection.execute('PRAGMA user_version').fetchone()[0]
                if version not in (0, self.schema_version):
                    raise HistoryError('This history database was created by a newer version of MCQ Maker.')
                connection.execute('PRAGMA journal_mode = WAL')
                connection.execute('''
                    CREATE TABLE IF NOT EXISTS history (
                        id TEXT PRIMARY KEY,
                        created_utc TEXT NOT NULL,
                        title TEXT NOT NULL,
                        output_filename TEXT NOT NULL,
                        output_path TEXT NOT NULL,
                        template_id TEXT NOT NULL,
                        template_name TEXT NOT NULL,
                        template_version INTEGER NOT NULL,
                        template_hash TEXT NOT NULL,
                        question_count INTEGER NOT NULL,
                        source_mode TEXT NOT NULL,
                        source_path TEXT NOT NULL,
                        input_hash TEXT NOT NULL,
                        outcome TEXT NOT NULL,
                        error_summary TEXT NOT NULL
                    )
                ''')
                connection.execute('CREATE INDEX IF NOT EXISTS history_created ON history(created_utc DESC)')
                connection.execute(f'PRAGMA user_version = {self.schema_version}')
        except sqlite3.DatabaseError as exc:
            raise HistoryError('The history database could not be opened. Your generated exam files are unchanged.') from exc

    def record(self, *, title, template_entry, question_count, source_mode,
               outcome, output_path=None, source_path=None, input_hash='', error_summary=''):
        if outcome not in {'success', 'failed'}:
            raise HistoryError('History outcome must be success or failed.')
        output = Path(output_path) if output_path else None
        values = (
            str(uuid4()), datetime.now(timezone.utc).isoformat(timespec='seconds'),
            str(title or ''), output.name if output else '', str(output) if output else '',
            str(template_entry.get('id', '')), str(template_entry.get('display_name', 'Unknown template')),
            int(template_entry.get('version', 1)), str(template_entry.get('sha256', '')),
            int(question_count or 0), str(source_mode), str(source_path or ''), str(input_hash),
            outcome, str(error_summary)[:1000],
        )
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute('''
                INSERT INTO history (
                    id, created_utc, title, output_filename, output_path,
                    template_id, template_name, template_version, template_hash,
                    question_count, source_mode, source_path, input_hash, outcome,
                    error_summary
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ''', values)
        return values[0]

    def record_success(self, *, quiz=None, title='', question_count=0, input_hash='',
                       output_path, template_entry, source_mode, source_path=None):
        if quiz is not None:
            title = quiz.title
            question_count = len(quiz.questions)
            input_hash = payload_hash(quiz.payload)
        return self.record(title=title, question_count=question_count, input_hash=input_hash,
                           output_path=output_path, template_entry=template_entry,
                           source_mode=source_mode, source_path=source_path,
                           outcome='success')

    def record_failure(self, *, template_entry, source_mode, error_summary,
                       title='', question_count=0, input_hash='', source_path=None):
        return self.record(title=title, question_count=question_count, input_hash=input_hash,
                           template_entry=template_entry, source_mode=source_mode,
                           source_path=source_path, outcome='failed', error_summary=error_summary)

    def list_entries(self, query=''):
        sql = 'SELECT * FROM history'
        parameters = ()
        if query.strip():
            escaped = query.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            pattern = f'%{escaped}%'
            sql += " WHERE title LIKE ? ESCAPE '\\' OR output_filename LIKE ? ESCAPE '\\' OR template_name LIKE ? ESCAPE '\\' OR source_path LIKE ? ESCAPE '\\'"
            parameters = (pattern, pattern, pattern, pattern)
        sql += ' ORDER BY created_utc DESC, id DESC'
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [HistoryEntry(**dict(row)) for row in rows]

    def remove(self, entry_id):
        with self._lock, closing(self._connect()) as connection, connection:
            cursor = connection.execute('DELETE FROM history WHERE id = ?', (entry_id,))
        return cursor.rowcount > 0

    def relocate(self, entry_id, output_path):
        output = Path(output_path)
        with self._lock, closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                'UPDATE history SET output_filename = ?, output_path = ? WHERE id = ?',
                (output.name, str(output), entry_id),
            )
        if not cursor.rowcount:
            raise HistoryError('This history entry is no longer available.')

    def export_csv(self, path, entries=None):
        entries = list(entries if entries is not None else self.list_entries())
        stream = io.StringIO(newline='')
        writer = csv.writer(stream)
        fields = list(HistoryEntry.__dataclass_fields__)
        writer.writerow(fields)
        for entry in entries:
            writer.writerow([getattr(entry, field) for field in fields])
        atomic_write(Path(path), ('\ufeff' + stream.getvalue()).encode('utf-8'))
