from unittest import TestCase

from incremental_coverage.lines import format_ranges, to_ranges
from incremental_coverage.paths import make_relative, normalize, SuffixIndex


class TestRanges(TestCase):
    def test_to_ranges(self) -> None:
        self.assertEqual([], to_ranges([]))
        self.assertEqual([(6, 12)], to_ranges(range(6, 13)))
        self.assertEqual([(1, 3), (5, 5), (8, 8), (12, 17)], to_ranges([1, 2, 3, 5, 8, 12, 13, 14, 15, 16, 17]))

    def test_unsorted_input_with_duplicates(self) -> None:
        self.assertEqual([(1, 3)], to_ranges([3, 1, 2, 2, 1]))

    def test_format_ranges(self) -> None:
        self.assertEqual("6-12", format_ranges(range(6, 13)))
        self.assertEqual("1-3, 5, 8, 12-17", format_ranges([1, 2, 3, 5, 8, 12, 13, 14, 15, 16, 17]))
        self.assertEqual("1-3, 5, 8, 12-17, 22", format_ranges([1, 2, 3, 5, 8, 12, 13, 14, 15, 16, 17, 22]))

    def test_empty_placeholder(self) -> None:
        self.assertEqual("", format_ranges([]))
        self.assertEqual("–", format_ranges([], empty="–"))

    def test_limit_reports_what_it_dropped(self) -> None:
        self.assertEqual("1, 3, 5 (+2 more)", format_ranges([1, 3, 5, 7, 9], limit=3))

    def test_custom_renderer(self) -> None:
        rendered = format_ranges([1, 2, 9], render=lambda start, end, text: f"<{text}>")
        self.assertEqual("<1-2>, <9>", rendered)


class TestNormalize(TestCase):
    def test_normalises_separators_and_prefixes(self) -> None:
        self.assertEqual("a/b.py", normalize("./a/b.py"))
        self.assertEqual("a/b.py", normalize("a\\b.py"))
        self.assertEqual("a/b.py", normalize('"a/b.py"'))
        self.assertEqual("a/b", normalize("a/b/"))

    def test_make_relative(self) -> None:
        self.assertEqual("src/x.ts", make_relative("/work/repo/src/x.ts", "/work/repo"))
        self.assertEqual("/other/x.ts", make_relative("/other/x.ts", "/work/repo"))


class TestSuffixIndex(TestCase):
    def setUp(self) -> None:
        self.index = SuffixIndex()

    def test_exact_match(self) -> None:
        self.index.add("app/models.py", "hit")
        self.assertEqual("hit", self.index.get("app/models.py"))

    def test_report_path_is_longer_than_the_repo_path(self) -> None:
        self.index.add("/home/runner/work/repo/repo/vue/src/table.ts", "hit")
        self.assertEqual("hit", self.index.get("vue/src/table.ts"))

    def test_repo_path_is_longer_than_the_report_path(self) -> None:
        """vitest run inside vue/ reports paths relative to vue/."""
        self.index.add("src/table.ts", "hit")
        self.assertEqual("hit", self.index.get("vue/src/table.ts"))

    def test_unrelated_paths_do_not_match(self) -> None:
        self.index.add("packages/a/src/index.ts", "a")
        self.assertIsNone(self.index.get("packages/b/lib/index.ts"))

    def test_longest_suffix_wins(self) -> None:
        self.index.add("src/index.ts", "short")
        self.index.add("vue/src/index.ts", "long")
        self.assertEqual("long", self.index.get("vue/src/index.ts"))

    def test_a_tie_is_refused_rather_than_guessed(self) -> None:
        """Crediting one file's coverage to another is how a gate fails open."""
        self.index.add("a/src/index.ts", "one")
        self.index.add("b/src/index.ts", "two")
        self.assertIsNone(self.index.get("src/index.ts"))
        self.assertEqual([("src/index.ts", ["a/src/index.ts", "b/src/index.ts"])], self.index.ambiguous)

    def test_missing_path(self) -> None:
        self.assertIsNone(self.index.get("nowhere.py"))
