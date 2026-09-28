"""Strict, shared parsing and validation for every MCQ intake mode."""
from dataclasses import dataclass
import json
import re
from typing import Any

from jsonschema import Draft202012Validator

MAX_INPUT_BYTES = 5 * 1024 * 1024


class QuizError(ValueError):
    """An actionable issue that can be displayed beside the paste editor."""


@dataclass(frozen=True)
class QuizValidation:
    title: str
    questions: tuple[dict[str, Any], ...]
    payload: list[dict[str, Any]]
    warnings: tuple[str, ...]


SCHEMA = {
    'type': 'array',
    'minItems': 2,
    'prefixItems': [{'type': 'object', 'required': ['title'], 'properties': {'title': {'type': 'string'}}}],
    'items': {
        'type': 'object',
        'required': ['question', 'options', 'correct', 'explanation'],
        'properties': {
            'question': {'type': 'string'},
            'options': {'type': 'array', 'minItems': 2, 'maxItems': 10, 'items': {'type': 'string'}},
            'correct': {'type': 'integer'},
            'explanation': {'type': 'string'},
        },
    },
}
VALIDATOR = Draft202012Validator(SCHEMA)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise QuizError(f'The JSON repeats the key “{key}”. Give each field one value.')
        result[key] = value
    return result


def _reject_constant(value):
    raise QuizError(f'“{value}” is not valid JSON. Use a normal number instead.')


def _decode(text: str, start: int = 0):
    try:
        return json.JSONDecoder(object_pairs_hook=_unique_object, parse_constant=_reject_constant).raw_decode(text, start)
    except json.JSONDecodeError as exc:
        raise QuizError(f'JSON syntax problem at line {exc.lineno}, column {exc.colno}: {exc.msg}.') from exc


def _looks_like_quiz(value: object) -> bool:
    return isinstance(value, list) and len(value) >= 2 and isinstance(value[0], dict) and 'title' in value[0]


def extract_quiz(text: str) -> list[dict[str, Any]]:
    """Accept plain JSON or one complete root array within prose or a Markdown fence."""
    if len(text.encode('utf-8')) > MAX_INPUT_BYTES:
        raise QuizError('This input is larger than 5 MiB. Split it into a smaller quiz and try again.')
    stripped = text.lstrip('\ufeff\ufeff \t\r\n')
    if not stripped:
        raise QuizError('Paste a complete quiz JSON array to begin.')
    try:
        value, end = _decode(stripped)
        if not stripped[end:].strip():
            if not isinstance(value, list):
                raise QuizError('The quiz must start with [ and end with ].')
            return value
    except QuizError as exc:
        direct_error = exc
    else:
        direct_error = None

    candidates = []
    for match in re.finditer(r'\[', stripped):
        try:
            value, _ = _decode(stripped, match.start())
        except QuizError:
            continue
        if _looks_like_quiz(value):
            candidates.append(value)
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise QuizError('More than one quiz array was found. Paste one complete quiz at a time.')
    if direct_error is not None:
        raise direct_error
    raise QuizError('No complete quiz array was found. Paste JSON beginning with [ and ending with ].')


def _location(error) -> str:
    path = list(error.absolute_path)
    if not path:
        return 'Quiz'
    if isinstance(path[0], int) and path[0] > 0:
        place = f'Question {path[0]}'
        if len(path) > 1:
            place += f' — {path[1]}'
        return place
    return 'Quiz title' if path[0] == 0 else 'Quiz'


def validate_quiz(text: str, minimum_options: int = 2, maximum_options: int = 10) -> QuizValidation:
    if not 2 <= minimum_options <= maximum_options <= 10:
        raise QuizError('The selected template has an invalid option range. Choose another template.')
    payload = extract_quiz(text)
    schema_errors = sorted(VALIDATOR.iter_errors(payload), key=lambda error: list(error.absolute_path))
    messages = [f'{_location(error)}: {error.message}.' for error in schema_errors]
    title = payload[0].get('title', '') if isinstance(payload[0], dict) else ''
    if not isinstance(title, str) or not title.strip():
        messages.append('Quiz title: enter a nonblank title.')
    for index, question in enumerate(payload[1:], 1):
        if not isinstance(question, dict):
            continue
        for field in ('question', 'explanation'):
            if isinstance(question.get(field), str) and not question[field].strip():
                messages.append(f'Question {index} — {field}: enter text.')
        options = question.get('options')
        if isinstance(options, list):
            for option_index, option in enumerate(options, 1):
                if not isinstance(option, str) or not option.strip():
                    messages.append(f'Question {index} — option {option_index}: enter text.')
            if not minimum_options <= len(options) <= maximum_options:
                messages.append(f'Question {index} — options: the selected template supports {minimum_options}–{maximum_options} options.')
        correct = question.get('correct')
        if type(correct) is not int:
            messages.append(f'Question {index} — correct: use a whole-number option index.')
        elif isinstance(options, list) and not 0 <= correct < len(options):
            messages.append(f'Question {index} — correct: choose an index from 0 to {len(options) - 1}.')
    if messages:
        unique = list(dict.fromkeys(messages))
        raise QuizError('\n'.join(unique))

    warnings = []
    known_title = {'title'}
    extra_title = set(payload[0]) - known_title
    if extra_title:
        warnings.append('The title entry has extra fields that this app will keep but does not use.')
    seen_questions = set()
    for index, question in enumerate(payload[1:], 1):
        normalized_question = question['question'].strip().casefold()
        if normalized_question in seen_questions:
            warnings.append(f'Question {index} repeats an earlier question.')
        seen_questions.add(normalized_question)
        options = [option.strip().casefold() for option in question['options']]
        if len(set(options)) != len(options):
            warnings.append(f'Question {index} has repeated options.')
        if not question['explanation'].strip():  # Kept for clarity if the schema changes later.
            warnings.append(f'Question {index} has no explanation.')
        extras = set(question) - {'question', 'options', 'correct', 'explanation'}
        if extras:
            warnings.append(f'Question {index} has extra fields that this app will keep but does not use.')
    return QuizValidation(payload[0]['title'].strip(), tuple(payload[1:]), payload, tuple(warnings))
