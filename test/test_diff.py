from unittest import TestCase

from incremental_coverage.diff import changed_lines, parse_diff

from .helpers import resource


class TestParseDiff(TestCase):
    def test_context_hunk(self) -> None:
        diff = (
            "diff --git a/app.py b/app.py\n"
            "index 111..222 100644\n"
            "--- a/app.py\n"
            "+++ b/app.py\n"
            "@@ -1,4 +1,5 @@\n"
            " one\n"
            "-two\n"
            "+two point five\n"
            "+three\n"
            " four\n"
        )
        self.assertEqual({"app.py": [2, 3]}, changed_lines(diff))

    def test_single_line_hunk_without_counts(self) -> None:
        """git writes "@@ -1 +1 @@" for one-line hunks; the old parser dropped these."""
        diff = "diff --git a/app.py b/app.py\n" "--- a/app.py\n" "+++ b/app.py\n" "@@ -1 +1 @@\n" "-old\n" "+new\n"
        self.assertEqual({"app.py": [1]}, changed_lines(diff))

    def test_mixed_hunks_in_one_file(self) -> None:
        diff = (
            "diff --git a/app.py b/app.py\n"
            "--- a/app.py\n"
            "+++ b/app.py\n"
            "@@ -1,0 +1,2 @@\n"
            "+alpha\n"
            "+beta\n"
            "@@ -10 +12,1 @@\n"
            "+gamma\n"
        )
        self.assertEqual({"app.py": [1, 2, 12]}, changed_lines(diff))

    def test_added_content_that_looks_like_a_header(self) -> None:
        """A diff of a diff: content lines must never be read as file headers."""
        diff = (
            "diff --git a/sample.diff b/sample.diff\n"
            "--- a/sample.diff\n"
            "+++ b/sample.diff\n"
            "@@ -0,0 +1,4 @@\n"
            "+--- a/inner.py\n"
            "++++ b/inner.py\n"
            "+@@ -1,2 +1,2 @@\n"
            "+-gone\n"
        )
        self.assertEqual({"sample.diff": [1, 2, 3, 4]}, changed_lines(diff))

    def test_several_files(self) -> None:
        diff = (
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n"
            "+++ b/a.py\n"
            "@@ -0,0 +1,2 @@\n"
            "+one\n"
            "+two\n"
            "diff --git a/b.ts b/b.ts\n"
            "--- a/b.ts\n"
            "+++ b/b.ts\n"
            "@@ -5,0 +6,1 @@\n"
            "+three\n"
        )
        self.assertEqual({"a.py": [1, 2], "b.ts": [6]}, changed_lines(diff))

    def test_new_file(self) -> None:
        diff = (
            "diff --git a/new.py b/new.py\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/new.py\n"
            "@@ -0,0 +1,3 @@\n"
            "+a\n"
            "+b\n"
            "+c\n"
        )
        self.assertEqual({"new.py": [1, 2, 3]}, changed_lines(diff))

    def test_deleted_file_is_reported_but_empty(self) -> None:
        diff = (
            "diff --git a/gone.py b/gone.py\n"
            "deleted file mode 100644\n"
            "--- a/gone.py\n"
            "+++ /dev/null\n"
            "@@ -1,2 +0,0 @@\n"
            "-a\n"
            "-b\n"
        )
        parsed = parse_diff(diff)
        self.assertIn("gone.py", parsed)
        self.assertTrue(parsed["gone.py"].is_deleted)
        self.assertEqual(set(), parsed["gone.py"].added_lines)

    def test_rename_keeps_the_new_path(self) -> None:
        diff = (
            "diff --git a/old.py b/new.py\n"
            "similarity index 92%\n"
            "rename from old.py\n"
            "rename to new.py\n"
            "--- a/old.py\n"
            "+++ b/new.py\n"
            "@@ -3 +3 @@\n"
            "+changed\n"
        )
        parsed = parse_diff(diff)
        self.assertEqual({"new.py"}, set(parsed))
        self.assertEqual("old.py", parsed["new.py"].old_path)
        self.assertTrue(parsed["new.py"].is_rename)
        self.assertEqual({3}, parsed["new.py"].added_lines)

    def test_binary_file(self) -> None:
        diff = (
            "diff --git a/logo.png b/logo.png\n"
            "index 111..222 100644\n"
            "--- a/logo.png\n"
            "+++ b/logo.png\n"
            "Binary files a/logo.png and b/logo.png differ\n"
        )
        parsed = parse_diff(diff)
        self.assertTrue(parsed["logo.png"].is_binary)
        self.assertEqual(set(), parsed["logo.png"].added_lines)

    def test_no_newline_marker(self) -> None:
        diff = (
            "diff --git a/app.py b/app.py\n"
            "--- a/app.py\n"
            "+++ b/app.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "\\ No newline at end of file\n"
            "+new\n"
            "\\ No newline at end of file\n"
        )
        self.assertEqual({"app.py": [1]}, changed_lines(diff))

    def test_quoted_path_with_spaces(self) -> None:
        diff = (
            'diff --git a/"my file.py" b/"my file.py"\n'
            '--- a/"my file.py"\n'
            '+++ b/"my file.py"\n'
            "@@ -0,0 +1 @@\n"
            "+x\n"
        )
        self.assertEqual({"my file.py": [1]}, changed_lines(diff))

    def test_empty_and_garbage_input(self) -> None:
        self.assertEqual({}, parse_diff(""))
        self.assertEqual({}, parse_diff("not a diff at all\njust text\n"))

    def test_real_world_fixture(self) -> None:
        parsed = changed_lines(resource("diff.txt"))
        self.assertEqual(["example.py"], list(parsed))
        # Added lines counted against the new revision, context lines skipped.
        self.assertEqual([4, 9, 10, 11, 13, 14, 15, 19, 20], parsed["example.py"])


class TestUnusualLineSeparators(TestCase):
    """`str.splitlines` breaks on more than newlines; a diff parser must not.

    A form feed as a page break is an old but living Python convention, and git
    passes it through verbatim. Splitting on it inserts a marker-less fragment into
    the middle of a hunk, which desynchronises the line counter and silently drops
    the rest of the hunk - the gate then grades fewer lines than were changed and
    reports a pass on untested code.
    """

    def test_form_feed_inside_an_added_line(self) -> None:
        diff = (
            "diff --git a/mod.py b/mod.py\n"
            "--- /dev/null\n"
            "+++ b/mod.py\n"
            "@@ -0,0 +1,4 @@\n"
            "+\x0c\n"
            "+def beta():\n"
            "+    return 1\n"
            "+SEP = 1\n"
        )
        self.assertEqual({"mod.py": [1, 2, 3, 4]}, changed_lines(diff))

    def test_line_separator_inside_a_string_literal(self) -> None:
        diff = (
            "diff --git a/mod.py b/mod.py\n"
            "--- /dev/null\n"
            "+++ b/mod.py\n"
            "@@ -0,0 +1,3 @@\n"
            '+SEP = " "\n'
            "+def beta():\n"
            "+    return 1\n"
        )
        self.assertEqual({"mod.py": [1, 2, 3]}, changed_lines(diff))

    def test_deleted_line_with_a_form_feed_does_not_eat_the_hunk(self) -> None:
        diff = (
            "diff --git a/mod.py b/mod.py\n"
            "--- a/mod.py\n"
            "+++ b/mod.py\n"
            "@@ -1,2 +1,2 @@\n"
            "-\x0c\n"
            "-old\n"
            "+def beta():\n"
            "+    return 1\n"
        )
        self.assertEqual({"mod.py": [1, 2]}, changed_lines(diff))

    def test_crlf_line_endings(self) -> None:
        diff = (
            "diff --git a/app.py b/app.py\r\n"
            "--- a/app.py\r\n"
            "+++ b/app.py\r\n"
            "@@ -1 +1,2 @@\r\n"
            " one\r\n"
            "+two\r\n"
        )
        self.assertEqual({"app.py": [2]}, changed_lines(diff))
