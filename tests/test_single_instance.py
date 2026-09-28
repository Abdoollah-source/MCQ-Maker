from uuid import uuid4
import unittest

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from mcq_maker.single_instance import SingleInstance


class SingleInstanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_second_launch_activates_the_first(self):
        name = f'mcq-maker-test-{uuid4().hex}'
        first = SingleInstance(name)
        second = SingleInstance(name)
        activations = []
        first.activation_requested.connect(lambda: activations.append(True))
        try:
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            QTest.qWait(80)
            self.assertEqual(activations, [True])
        finally:
            second.close()
            first.close()


if __name__ == '__main__':
    unittest.main()
