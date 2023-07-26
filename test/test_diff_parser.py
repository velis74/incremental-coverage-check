import os
from unittest import TestCase

from diff_parser import DiffParser


class TestDiffParser(TestCase):
    def test_validate_data(self) -> None:
        parser = DiffParser("      ")
        self.assertFalse(parser.validate_data())

        with open(os.path.join(os.path.dirname(__file__), "resources", "diff.txt"), "r") as file:
            data = file.read()

        parser2 = DiffParser(data)
        self.assertTrue(parser2.validate_data())

    def test_parse_empty(self) -> None:
        self.assertFalse(DiffParser("      ").parse())

    def test_parse(self) -> None:
        with open(os.path.join(os.path.dirname(__file__), "resources", "diff.txt"), "r") as file:
            data = file.read()
        parser = DiffParser(data)
        self.assertTrue(parser.parse())

    def test_parse_header_valid(self):
        diff = "diff --git a/example.py b/example.py"

        parser = DiffParser(diff)
        # Test case 1: Valid diff header with starting line 1
        diff_chunk_1 = "@@ -1,4 +1,4 @@"
        self.assertEqual(parser.parse_header(diff_chunk_1), 1)

        # Test case 2: Valid diff header with starting line 42
        diff_chunk_2 = "@@ -42,10 +42,12 @@"
        self.assertEqual(parser.parse_header(diff_chunk_2), 42)

        # Test case 3: Valid diff header with starting line 1000
        diff_chunk_3 = "@@ -1000,8 +1000,15 @@"
        self.assertEqual(parser.parse_header(diff_chunk_3), 1000)

    def test_parse_header_invalid(self):
        diff = "diff --git a/example.py b/example.py"

        parser = DiffParser(diff)
        # Test case 4: Invalid diff header, should raise ValueError
        invalid_diff_chunk = "@@ -abc,def +ghi,jkl @@"
        with self.assertRaises(ValueError):
            parser.parse_header(invalid_diff_chunk)

        # Test case 5: Invalid diff header, should raise ValueError
        another_invalid_diff_chunk = "This is not a diff header."
        with self.assertRaises(ValueError):
            parser.parse_header(another_invalid_diff_chunk)
