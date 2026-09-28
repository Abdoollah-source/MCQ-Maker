"""Render Module 11 without contacting a provider."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

from mcq_maker.ai_library import AILibrary
from mcq_maker.settings import SettingsStore
from mcq_maker.shell import MainWindow
from mcq_maker.template_repository import TemplateRepository
from mcq_maker.theme import apply_theme


app = QApplication([])
root = Path(tempfile.mkdtemp(dir=Path(__file__).resolve().parents[1] / 'artifacts'))
repository = TemplateRepository(root / 'data'); repository.initialize()
library = AILibrary(repository.root); library.initialize(); library.add_key('google', 'Personal account', 'preview-key')
reference = root / 'reference.pdf'; reference.write_bytes(b'%PDF preview'); library.add_reference(reference, 'Anesthesia', 'Finals', 'Year 5')
lectures = root / 'lectures'; lectures.mkdir()
for name in ('Airway management.pdf', 'General anesthesia.pdf', 'Postoperative care.pdf'):
    (lectures / name).write_bytes(b'%PDF preview')
settings = SettingsStore(repository.root).defaults()
apply_theme(app, False)
window = MainWindow(repository=repository, settings=settings, settings_store=SettingsStore(repository.root), ai_library=library)
window.resize(1120, 780); window.show(); window.ai_generation.set_template_snapshot(repository.list_templates()); window.ai_generation.set_folder(lectures); window.navigate(4)
app.processEvents(); window.grab().save(str(Path(__file__).resolve().parents[1] / 'artifacts' / 'module11-ai-generation.png'))
window.open_ai_library(); app.processEvents(); window.ai_library_dialog.grab().save(str(Path(__file__).resolve().parents[1] / 'artifacts' / 'module11-ai-library.png'))
window.close(); app.processEvents()
