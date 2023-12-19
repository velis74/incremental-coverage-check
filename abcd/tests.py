from unittest import TestCase

import abcd.abcd


class ABCDTest(TestCase):
    def test_add_one(self):
        self.assertEqual(abcd.abcd.add_one(1), 2)
