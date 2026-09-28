"""Safe payload injection, Windows filename rules, and exclusive output saving."""
from datetime import datetime
import json
import os
from pathlib import Path
import re
import tempfile
import unicodedata

from .quiz_validation import QuizValidation
from .template_validation import END, START, TemplateError, validate_template


def serialize_payload(payload) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    for character, escaped in (('<', '\\u003c'), ('>', '\\u003e'), ('&', '\\u0026'), ('/', '\\/'),
                               ('\u2028', '\\u2028'), ('\u2029', '\\u2029')):
        encoded = encoded.replace(character, escaped)
    return encoded


def inject_quiz_data(template: str, quiz: QuizValidation) -> str:
    """Preserve every template byte outside the exact marker bounds."""
    validate_template(template)
    begin = template.index(START) + len(START)
    end = template.index(END)
    return template[:begin] + '\nconst quizData = ' + serialize_payload(quiz.payload) + ';\n' + template[end:]


def _utf16_length(value: str) -> int:
    return len(value.encode('utf-16-le')) // 2


def filename_stem(title: str, now=None) -> str:
    title = unicodedata.normalize('NFC', title).strip()
    title = ''.join(character for character in title if unicodedata.category(character) not in {'Cc', 'Cf'})
    title = re.sub(r'[<>:"/\\|?*\s]+', '_', title)
    title = re.sub(r'_+', '_', title).strip(' _.')
    device = title.split('.', 1)[0].upper().replace('¹', '1').replace('²', '2').replace('³', '3')
    if device in {'CON', 'PRN', 'AUX', 'NUL'} or re.fullmatch(r'(COM|LPT)[1-9]', device):
        title = 'exam_' + title
    if not title:
        title = 'untitled_' + (now or datetime.now()).strftime('%Y%m%d_%H%M%S')
    return title[:60]


def proposed_filename(title: str, folder: Path, counter: int = 1, now=None) -> str:
    suffix = '' if counter == 1 else f'_{counter}'
    extension = '_mcq.html'
    stem = filename_stem(title, now)
    while stem and _utf16_length(str(folder / f'{stem}{suffix}{extension}')) > 240:
        stem = stem[:-1]
    if not stem:
        stem = 'untitled'
    return f'{stem}{suffix}{extension}'


def save_exam(template: str, quiz: QuizValidation, output_folder: Path, now=None, overwrite=False) -> Path:
    output_folder = Path(output_folder)
    output_folder.mkdir(parents=True, exist_ok=True)
    rendered = inject_quiz_data(template, quiz).encode('utf-8')
    if overwrite:
        target = output_folder / proposed_filename(quiz.title, output_folder, now=now)
        descriptor, temporary = tempfile.mkstemp(prefix='.mcq-maker-', suffix='.html', dir=output_folder)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(rendered)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            temporary = None
            return target
        except OSError as exc:
            raise TemplateError(f'MCQ Maker could not replace “{target.name}”. Try again.') from exc
        finally:
            if temporary is not None and os.path.exists(temporary):
                os.unlink(temporary)
    for counter in range(1, 10000):
        target = output_folder / proposed_filename(quiz.title, output_folder, counter, now)
        descriptor, temporary = tempfile.mkstemp(prefix='.mcq-maker-', suffix='.html', dir=output_folder)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(rendered)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                continue
            except OSError:
                # Hard links are unavailable on a few removable filesystems. Reserve
                # the final name before replacing it so an existing exam is never lost.
                try:
                    reservation = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    os.close(reservation)
                except FileExistsError:
                    continue
                try:
                    os.replace(temporary, target)
                    temporary = None
                except OSError as exc:
                    target.unlink(missing_ok=True)
                    raise TemplateError(f'MCQ Maker could not finish saving “{target.name}”. Try again.') from exc
            if temporary is not None:
                os.unlink(temporary)
            return target
        except OSError as exc:
            raise TemplateError(f'MCQ Maker could not save the exam in “{output_folder}”. Check the folder and try again.') from exc
        finally:
            if temporary is not None and os.path.exists(temporary):
                os.unlink(temporary)
    raise TemplateError('Too many files have the same name in this folder. Rename the quiz title and try again.')
