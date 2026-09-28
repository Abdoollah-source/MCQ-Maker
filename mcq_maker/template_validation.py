"""The explicit, non-executing HTML template contract."""
from dataclasses import dataclass
from html.parser import HTMLParser
import json
import re

START = '/* MCQ_MAKER_DATA_START */'
END = '/* MCQ_MAKER_DATA_END */'
MAX_TEMPLATE_BYTES = 5 * 1024 * 1024

class TemplateError(ValueError):
    """An actionable template problem suitable for the user interface."""

@dataclass(frozen=True)
class TemplateContract:
    version: int
    minimum_options: int
    maximum_options: int
    warnings: tuple = ()

@dataclass(frozen=True)
class TemplateWarning:
    """A copied-template limitation that does not prevent its use."""
    line: int
    reference: str
    message: str

class DocumentInspector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.html = False
        self.closed_html = False
        self.charset = False
        self.metadata = {}
        self.scripts = []
        self.script = None
        self.warnings = []
        self.styles = []
        self.in_style = False

    def warning(self, reference, message):
        line, _ = self.getpos()
        reference = ' '.join(str(reference).split())[:140]
        self.warnings.append(TemplateWarning(line, reference, message))

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == 'html':
            self.html = True
        if tag == 'meta':
            if values.get('charset', '').lower().replace('-', '') == 'utf8':
                self.charset = True
            name = values.get('name', '').lower()
            if name.startswith('mcq-maker-'):
                if name in self.metadata:
                    raise TemplateError(f'The template declares {name} more than once.')
                self.metadata[name] = values.get('content', '')
        if tag == 'script':
            self.script = {'text': '', 'type': values.get('type', '').lower(), 'external': 'src' in values}
        if tag == 'style':
            self.in_style = True
        if tag in ('iframe', 'object', 'embed', 'base'):
            self.warning(tag, f'The <{tag}> element refers to content outside this copied template.')
        for attr in ('src', 'poster', 'srcset'):
            if values.get(attr) and not (attr != 'srcset' and values[attr].startswith('data:')):
                self.warning(values[attr], f'The {attr} reference may not work after MCQ Maker copies this template.')
        if tag == 'link' and values.get('href'):
            self.warning(values['href'], 'The linked stylesheet or resource may not work after MCQ Maker copies this template.')
        if 'style' in values:
            line, _ = self.getpos()
            self.styles.append((values['style'], line))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag == 'html':
            self.closed_html = True
        if tag == 'script' and self.script is not None:
            self.scripts.append(self.script)
            self.script = None
        if tag == 'style':
            self.in_style = False

    def handle_data(self, data):
        if self.script is not None:
            self.script['text'] += data
        if self.in_style:
            line, _ = self.getpos()
            self.styles.append((data, line))

def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise TemplateError(f'The existing quiz array repeats the key “{key}”.')
        result[key] = value
    return result

def _reject_constant(value):
    raise TemplateError(f'The existing quiz array contains {value}, which is not valid JSON.')

def decode_template(data: bytes) -> str:
    if len(data) > MAX_TEMPLATE_BYTES:
        raise TemplateError('This template is larger than 5 MiB. Use a smaller, self-contained HTML file.')
    try:
        # Keep one canonical representation in the library.  This prevents a
        # Windows CRLF source from failing integrity checks after an otherwise
        # identical replacement written on another platform.
        return data.decode('utf-8-sig').replace('\r\n', '\n').replace('\r', '\n')
    except UnicodeDecodeError as exc:
        raise TemplateError('This file is not UTF-8 text. Save it as UTF-8 HTML and try again.') from exc

def _css_warnings(styles):
    warnings = []
    for css, starting_line in styles:
        for match in re.finditer(r'@import\s+(?:url\()?\s*["\']?([^\s"\')]+)', css, re.IGNORECASE):
            line = starting_line + css[:match.start()].count('\n')
            warnings.append(TemplateWarning(line, match.group(1)[:140], 'The CSS @import may not work after MCQ Maker copies this template.'))
        for match in re.finditer(r'url\(\s*["\']?([^\s"\')]+)', css, re.IGNORECASE):
            reference = match.group(1)
            if reference.lower().startswith('data:') or reference.startswith('#'):
                continue
            line = starting_line + css[:match.start()].count('\n')
            warnings.append(TemplateWarning(line, reference[:140], 'The CSS url() reference may not work after MCQ Maker copies this template.'))
    return warnings

def validate_template(text: str) -> TemplateContract:
    for marker, description in ((START, 'start'), (END, 'end')):
        count = text.count(marker)
        if count != 1:
            raise TemplateError(f'Expected exactly one {description} marker; found {count}.\nRequired marker: {marker}')
    if text.index(START) >= text.index(END):
        raise TemplateError('The data markers are reversed. Place the start marker before the end marker.')
    inspector = DocumentInspector()
    inspector.feed(text)
    inspector.close()
    if not inspector.html or not inspector.closed_html:
        raise TemplateError('The template needs an opening <html> tag and a closing </html> tag.')
    if not inspector.charset:
        raise TemplateError('Add <meta charset="UTF-8"> inside the template’s <head>.')
    scripts = [s for s in inspector.scripts if START in s['text'] and END in s['text']]
    if len(scripts) != 1 or scripts[0]['external'] or scripts[0]['type'] not in ('', 'text/javascript', 'application/javascript'):
        raise TemplateError('Put both markers inside the same inline JavaScript <script> element, without a src attribute.')
    region = text.split(START, 1)[1].split(END, 1)[0].strip()
    match = re.fullmatch(r'const\s+quizData\s*=\s*(.*?)\s*;\s*', region, re.S)
    if not match:
        raise TemplateError('Between the markers, use only: const quizData = [];')
    try:
        value = json.loads(match[1], object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise TemplateError(f'The existing quiz array is not valid JSON near line {exc.lineno}, column {exc.colno}. Use [] for an empty template.') from exc
    if not isinstance(value, list):
        raise TemplateError('The quizData value must be a JSON array. Use [] for an empty template.')
    metadata = inspector.metadata
    if metadata.get('mcq-maker-template-version') != '1':
        raise TemplateError('Add <meta name="mcq-maker-template-version" content="1"> inside <head>. Only format version 1 is supported.')
    try:
        minimum = int(metadata['mcq-maker-min-options'])
        maximum = int(metadata['mcq-maker-max-options'])
    except (KeyError, ValueError) as exc:
        raise TemplateError('Declare the supported option counts in <head>: mcq-maker-min-options and mcq-maker-max-options, with numeric content values.') from exc
    if not 2 <= minimum <= maximum <= 10:
        raise TemplateError('The supported option range must be between 2 and 10, with the minimum no greater than the maximum.')
    warnings = tuple(inspector.warnings + _css_warnings(inspector.styles))
    return TemplateContract(1, minimum, maximum, warnings)

def render_preview(text: str) -> str:
    contract = validate_template(text)
    payload = [{'title': 'Template preview — sample questions'}, {
        'question': 'Which option is selected as the correct answer in this sample?',
        'options': ['The first option'] + [f'Option {n}' for n in range(2, contract.minimum_options+1)],
        'correct': 0, 'explanation': 'This is sample content for checking the template layout.'}, {
        'question': 'اختبار عرض النص العربي — اختر الإجابة الأولى',
        'options': ['الإجابة الأولى'] + [f'الخيار {n}' for n in range(2, contract.maximum_options+1)],
        'correct': 0, 'explanation': 'Unicode check: Ca²⁺ < 8.5, α, β, & literal <script> text.'}]
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    for char, escaped in (('<', '\\u003c'), ('>', '\\u003e'), ('&', '\\u0026'), ('/', '\\/'), ('\u2028', '\\u2028'), ('\u2029', '\\u2029')):
        encoded = encoded.replace(char, escaped)
    begin = text.index(START) + len(START)
    finish = text.index(END)
    return text[:begin] + '\nconst quizData = ' + encoded + ';\n' + text[finish:]
