from unittest import TestCase
from main import get_changed_files, get_curr_branch, intersection


class TestMain(TestCase):
    def test_intersection(self) -> None:
        self.assertEqual({}, intersection([], []))
        self.assertEqual({}, intersection([1, 2], [3, 4]))
        self.assertEqual({2}, intersection([1, 2], [2, 3]))

    # def test_get_curr_branch(self) -> None:
    #     self.assertNotEqual(get_curr_branch(), False)

    # def test_get_changed_files(self) -> None:
    #     files = get_changed_files("main", get_curr_branch())
    #     self.assertNotEqual(files, False)
    #     self.assertTrue(type(files) is list)
