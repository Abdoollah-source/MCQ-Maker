"""Deterministic, cancellable folder processing through the shared quiz pipeline."""
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from PySide6.QtCore import QObject, QRunnable, Signal

from .exam_generator import save_exam
from .quiz_validation import MAX_INPUT_BYTES, QuizError, validate_quiz
from .template_validation import TemplateError
from .history import payload_hash

SUPPORTED_EXTENSIONS = {'.json', '.txt', '.md'}


@dataclass(frozen=True)
class ScanResult:
    source_path: Path
    relative_path: str
    status: str
    reason: str = ''
    output_path: Path | None = None
    title: str = ''
    question_count: int = 0
    input_hash: str = ''
    warnings: tuple[str, ...] = ()


def discover_files(folder: Path, recursive=False) -> list[Path]:
    folder = Path(folder)
    if not folder.is_dir():
        raise OSError('The selected folder is no longer available.')
    candidates = folder.rglob('*') if recursive else folder.iterdir()
    files = [path for path in candidates if path.is_file() and path.suffix.casefold() in SUPPORTED_EXTENSIONS]
    return sorted(files, key=lambda path: str(path.relative_to(folder)).casefold())


def process_quiz_file(source: Path, root: Path, template: str, template_entry: dict,
                      output_folder: Path) -> ScanResult:
    source = Path(source)
    relative = str(source.relative_to(root))
    try:
        with source.open('rb') as stream:
            data = stream.read(MAX_INPUT_BYTES + 1)
        if len(data) > MAX_INPUT_BYTES:
            raise QuizError('This file is larger than 5 MiB. Split it into a smaller quiz and try again.')
        try:
            text = data.decode('utf-8-sig')
        except UnicodeDecodeError as exc:
            raise QuizError('This file is not UTF-8 text. Save it as UTF-8 and scan again.') from exc
        quiz = validate_quiz(text, template_entry['minimum_options'], template_entry['maximum_options'])
        output = save_exam(template, quiz, output_folder)
        return ScanResult(source, relative, 'created', output_path=output, title=quiz.title,
                          question_count=len(quiz.questions), input_hash=payload_hash(quiz.payload),
                          warnings=quiz.warnings)
    except (QuizError, TemplateError) as exc:
        return ScanResult(source, relative, 'failed', reason=str(exc))
    except OSError:
        return ScanResult(source, relative, 'failed', reason='Windows could not read this file or save its exam. Check that both folders are available and try again.')


class ScanSignals(QObject):
    progress = Signal(object, int, int)
    finished = Signal(object, bool, str)


class FolderScanWorker(QRunnable):
    def __init__(self, folder: Path, recursive: bool, template: str, template_entry: dict,
                 output_folder: Path, cancellation: Event | None = None):
        super().__init__()
        self.folder = Path(folder)
        self.recursive = recursive
        self.template = template
        self.template_entry = dict(template_entry)
        self.output_folder = Path(output_folder)
        self.cancellation = cancellation or Event()
        self.signals = ScanSignals()

    def cancel(self):
        self.cancellation.set()

    def run(self):
        try:
            files = discover_files(self.folder, self.recursive)
        except OSError as exc:
            self.signals.finished.emit(tuple(), False, str(exc))
            return
        results = []
        total = len(files)
        for source in files:
            if self.cancellation.is_set():
                result = ScanResult(source, str(source.relative_to(self.folder)), 'skipped',
                                    reason='The scan was cancelled before this file was processed.')
            else:
                result = process_quiz_file(source, self.folder, self.template,
                                           self.template_entry, self.output_folder)
            results.append(result)
            self.signals.progress.emit(result, len(results), total)
        self.signals.finished.emit(tuple(results), self.cancellation.is_set(), '')
