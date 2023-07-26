from unittest import TestCase
from main import get_changed_files, get_curr_branch


class TestMain(TestCase):
    def test_get_curr_branch(self) -> None:
        self.assertNotEqual(get_curr_branch(), False)

    def test_get_changed_files(self) -> None:
        files = get_changed_files("main", get_curr_branch())
        self.assertNotEqual(files, False)
        self.assertTrue(type(files) is list)
