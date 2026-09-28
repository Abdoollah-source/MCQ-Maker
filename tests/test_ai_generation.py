import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from mcq_maker.ai_generation import AIGenerationWorker, AIRun, discover_pdfs
from mcq_maker.ai_generation_page import AIGenerationPage
from mcq_maker.ai_library import AILibrary
from mcq_maker.ai_providers import RateLimitError, TemporaryProviderError
from mcq_maker.settings import SettingsStore
from mcq_maker.template_repository import TemplateRepository


BASE = Path(__file__).resolve().parents[1]
QUIZ = '[{"title":"Generated lecture"},{"question":"Q?","options":["A","B","C","D"],"correct":0,"explanation":"A."}]'


class FakeSession:
    def calibrate(self, prompt, reference): return 'Calibrated and ready.'
    def generate(self, lecture): return QUIZ


class SequenceSession(FakeSession):
    def __init__(self, outputs): self.outputs = iter(outputs)
    def generate(self, lecture): return next(self.outputs)


class AIGenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def test_discovery_worker_validation_and_saving(self):
        with tempfile.TemporaryDirectory(dir=BASE/'artifacts') as temporary:
            root=Path(temporary); lectures=root/'lectures'; lectures.mkdir()
            for name in ('B.pdf','a.PDF'): (lectures/name).write_bytes(b'%PDF')
            (lectures/'ignore.txt').write_text('x')
            found=discover_pdfs(lectures); self.assertEqual([x.name for x in found], ['a.PDF','B.pdf'])
            repo=TemplateRepository(root/'data'); repo.initialize(); snapshot=repo.list_templates(); entry=snapshot['templates'][0]
            library=AILibrary(repo.root); data=library.initialize(); library.add_key('google','Personal','key')
            reference=root/'reference.pdf'; reference.write_bytes(b'%PDF-ref'); ref_id=library.add_reference(reference,'Medicine')
            run=AIRun(tuple(found),{str(x):ref_id for x in found},data['prompts'][0],'google','gemini-3.8-flash','high',2,repo.read_template(entry['id']),entry,root/'output')
            with patch('mcq_maker.ai_generation.create_session', return_value=FakeSession()):
                worker=AIGenerationWorker(run,library); worker.run()
            self.assertEqual([x.status for x in worker.results], ['done','done'])
            self.assertEqual(len(list((root/'output').glob('*_mcq.html'))),2)

    def test_page_requires_every_input_and_uses_shared_controls(self):
        with tempfile.TemporaryDirectory(dir=BASE/'artifacts') as temporary:
            root=Path(temporary); repo=TemplateRepository(root/'data'); repo.initialize(); library=AILibrary(repo.root); library.initialize(); library.add_key('google','Personal','key')
            reference=root/'reference.pdf'; reference.write_bytes(b'%PDF-ref'); library.add_reference(reference,'Medicine')
            lectures=root/'lectures'; lectures.mkdir(); (lectures/'lecture.pdf').write_bytes(b'%PDF')
            settings=SettingsStore(repo.root).defaults(); page=AIGenerationPage(repo,library,settings); page.set_template_snapshot(repo.list_templates()); page.set_folder(lectures)
            self.assertEqual(page.table.rowCount(),1); self.assertTrue(page.run_button.isEnabled()); self.assertEqual(page.provider.currentData(),'google')
            page.close()

    def test_bad_response_skips_one_lecture_and_continues(self):
        with tempfile.TemporaryDirectory(dir=BASE/'artifacts') as temporary:
            root=Path(temporary); lectures=root/'lectures'; lectures.mkdir()
            files=[]
            for name in ('one.pdf','two.pdf'):
                path=lectures/name; path.write_bytes(b'%PDF'); files.append(path)
            repo=TemplateRepository(root/'data'); repo.initialize(); entry=repo.list_templates()['templates'][0]
            library=AILibrary(repo.root); data=library.initialize(); library.add_key('google','Personal','key')
            reference=root/'reference.pdf'; reference.write_bytes(b'%PDF-ref'); ref_id=library.add_reference(reference,'Medicine')
            run=AIRun(tuple(files),{str(x):ref_id for x in files},data['prompts'][0],'google','gemini-3.8-flash','high',2,repo.read_template(entry['id']),entry,root/'output')
            session=SequenceSession(('not json', QUIZ))
            with patch('mcq_maker.ai_generation.create_session', return_value=session): AIGenerationWorker.run(worker:=AIGenerationWorker(run,library))
            self.assertEqual([x.status for x in worker.results], ['failed','done'])

    def test_rate_limit_rotates_to_next_saved_key(self):
        class Limited(FakeSession):
            def calibrate(self, prompt, reference): raise RateLimitError('limited', 60)
        with tempfile.TemporaryDirectory(dir=BASE/'artifacts') as temporary:
            root=Path(temporary); lecture=root/'lecture.pdf'; lecture.write_bytes(b'%PDF')
            repo=TemplateRepository(root/'data'); repo.initialize(); entry=repo.list_templates()['templates'][0]
            library=AILibrary(repo.root); data=library.initialize(); library.add_key('google','First','one'); library.add_key('google','Second','two')
            reference=root/'reference.pdf'; reference.write_bytes(b'%PDF-ref'); ref_id=library.add_reference(reference,'Medicine')
            run=AIRun((lecture,),{str(lecture):ref_id},data['prompts'][0],'google','gemini-3.8-flash','high',1,repo.read_template(entry['id']),entry,root/'output')
            with patch('mcq_maker.ai_generation.create_session', side_effect=[Limited(),FakeSession()]): AIGenerationWorker.run(worker:=AIGenerationWorker(run,library))
            self.assertEqual(worker.results[0].status,'done')
            self.assertEqual(library.load()['keys'][0]['status'],'rate limited')

    def test_temporary_provider_failure_retries_same_lecture(self):
        class BusyOnce(FakeSession):
            def __init__(self): self.calls=0
            def generate(self, lecture):
                self.calls += 1
                if self.calls == 1: raise TemporaryProviderError('busy', 1)
                return QUIZ
        with tempfile.TemporaryDirectory(dir=BASE/'artifacts') as temporary:
            root=Path(temporary); lecture=root/'lecture.pdf'; lecture.write_bytes(b'%PDF')
            repo=TemplateRepository(root/'data'); repo.initialize(); entry=repo.list_templates()['templates'][0]
            library=AILibrary(repo.root); data=library.initialize(); library.add_key('google','Personal','key')
            reference=root/'reference.pdf'; reference.write_bytes(b'%PDF-ref'); ref_id=library.add_reference(reference,'Medicine')
            run=AIRun((lecture,),{str(lecture):ref_id},data['prompts'][0],'google','gemini-3.8-flash','high',1,repo.read_template(entry['id']),entry,root/'output')
            session=BusyOnce()
            with patch('mcq_maker.ai_generation.create_session', return_value=session), patch.object(AIGenerationWorker,'_wait_for_provider'):
                AIGenerationWorker.run(worker:=AIGenerationWorker(run,library))
            self.assertEqual(worker.results[0].status,'done')
            self.assertEqual(session.calls,2)


if __name__ == '__main__':
    unittest.main()
