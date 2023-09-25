from unittest import TestCase
from en_testni import EnTestni


class TestEnTestni(TestCase):
    def test_duplicate(self):
        self.assertEqual(EnTestni().duplicate(), 2)
