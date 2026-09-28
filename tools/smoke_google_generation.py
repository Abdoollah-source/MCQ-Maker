"""Exercise the saved Google account without printing lecture or generated content."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcq_maker.ai_library import AILibrary
from mcq_maker.ai_providers import create_session
from mcq_maker.quiz_validation import validate_quiz


root, model = Path(sys.argv[1]), sys.argv[2]
library = AILibrary(root)
data = library.load()
key = next(item for item in data['keys'] if item['provider'] == 'google')
prompt = next(item for item in data['prompts'] if item['id'] == data['active_prompt_id'])


def write_pdf(path):
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        b'<< /Length 104 >>\nstream\nBT /F1 18 Tf 72 720 Td (Synthetic lecture: hand hygiene reduces infection. Create one four-option MCQ.) Tj ET\nendstream',
    ]
    output = bytearray(b'%PDF-1.4\n'); offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(output)); output.extend(f'{number} 0 obj\n'.encode()); output.extend(obj); output.extend(b'\nendobj\n')
    xref = len(output); output.extend(f'xref\n0 {len(objects)+1}\n'.encode()); output.extend(b'0000000000 65535 f \n')
    for offset in offsets[1:]: output.extend(f'{offset:010d} 00000 n \n'.encode())
    output.extend(f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()); path.write_bytes(output)


with tempfile.TemporaryDirectory() as temporary:
    temporary = Path(temporary)
    reference = temporary / 'reference.txt'
    reference.write_text('Use four options per question. Keep questions clinically clear and explanations concise.', encoding='utf-8')
    lecture = temporary / 'synthetic-lecture.pdf'; write_pdf(lecture)
    session = create_session('google', library.secret(key['id']), model, 'low')
    confirmation = session.calibrate(prompt['text'], reference)
    print(f'CALIBRATION_OK characters={len(confirmation)}')
    response = session.generate(lecture)
    quiz = validate_quiz(response, 2, 10)
    print(f'GENERATION_OK title_characters={len(quiz.title)} questions={len(quiz.questions)} response_characters={len(response)}')
