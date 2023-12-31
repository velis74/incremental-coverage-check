from unittest import TestCase

from main import collect_uncovered_lines_2_txt, get_changed_files, get_curr_branch, intersection, is_ignored


class TestMain(TestCase):
    def test_intersection(self) -> None:
        self.assertEqual({}, intersection([], []))
        self.assertEqual({}, intersection([1, 2], [3, 4]))
        self.assertEqual({2}, intersection([1, 2], [2, 3]))

    def test_get_curr_branch(self) -> None:
        # TODO: This should be checked
        self.assertEqual(get_curr_branch("/"), False)

    def test_get_changed_files(self) -> None:
        # TODO: This should be checked
        files = get_changed_files("main", get_curr_branch("/"), "/")
        self.assertNotEqual(files, False)
        self.assertTrue(isinstance(files, list))

    def test_collect_uncovered_lines_2_txt(self) -> None:
        self.assertEqual("6-12", collect_uncovered_lines_2_txt({6, 7, 8, 9, 10, 11, 12}))
        self.assertEqual("1-3, 5, 8, 12-17", collect_uncovered_lines_2_txt({1, 2, 3, 5, 8, 12, 13, 14, 15, 16, 17}))
        self.assertEqual(
            "1-3, 5, 8, 12-17, 22", collect_uncovered_lines_2_txt({1, 2, 3, 5, 8, 12, 13, 14, 15, 16, 17, 22})
        )

    def test_is_ignored(self) -> None:
        self.assertTrue(is_ignored("test/test_main.py"))
        self.assertTrue(is_ignored("LICENSE"))
        self.assertTrue(is_ignored("README.md"))
        self.assertTrue(is_ignored("main.pyc"))
        self.assertTrue(is_ignored(".gitignore"))
        self.assertTrue(is_ignored("test_basic.py"))
        self.assertTrue(is_ignored("test_basic.py", "/abc/migrations/0001_initial.py"))
        self.assertTrue(is_ignored("0001_initial.py", "/abc/migrations"))
        self.assertTrue(is_ignored("tests.py", "some_module"))
        self.assertTrue(is_ignored("one_file.py", "some_module/migrations/"))
