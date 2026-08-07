from unittest import TestCase

from incremental_coverage import MARKER
from incremental_coverage.analysis import FileResult, Report, SkippedFile, Status
from incremental_coverage.ignore import SkipReason
from incremental_coverage.render import (
    format_percentage,
    inline_code,
    Linker,
    render_annotations,
    render_markdown,
    render_terminal,
)


def sample_report(threshold: float = 80.0) -> Report:
    return Report(
        files=[
            FileResult("app/models.py", Status.COVERED, 12, 12, []),
            FileResult("vue/src/table.ts", Status.PARTIAL, 20, 12, [12, 13, 14, 22, 30, 31]),
            FileResult("app/new_thing.py", Status.NO_DATA, 16, 0, list(range(1, 17)), note="no coverage data"),
        ],
        skipped=[SkippedFile("README.md", SkipReason("not-source", "not a source file"))],
        threshold=threshold,
        base_label="main",
        head_label="HEAD",
        base_sha="8f3a1c2ddddddd",
        head_sha="1b2c3d4eeeeeee",
        sources=["coverage.json (coverage.py, 42 files)"],
    )


class TestFormatPercentage(TestCase):
    def test_whole_numbers_have_no_decimals(self) -> None:
        self.assertEqual("100%", format_percentage(100.0))
        self.assertEqual("73%", format_percentage(73.0))

    def test_fractions_keep_one_decimal(self) -> None:
        self.assertEqual("73.4%", format_percentage(73.42))
        self.assertEqual("79.9%", format_percentage(79.94))


class TestMarkdown(TestCase):
    def setUp(self) -> None:
        self.report = sample_report()
        self.markdown = render_markdown(self.report)

    def test_starts_with_the_marker_so_comments_can_be_updated(self) -> None:
        self.assertTrue(self.markdown.startswith(MARKER))

    def test_failure_verdict_is_in_the_heading(self) -> None:
        self.assertIn("## ❌ Incremental coverage", self.markdown)

    def test_totals_are_stated(self) -> None:
        self.assertIn("**24 / 48**", self.markdown)
        self.assertIn("threshold **80%**", self.markdown)

    def test_every_graded_file_has_a_row(self) -> None:
        for path in ("app/models.py", "vue/src/table.ts", "app/new_thing.py"):
            self.assertIn(f"`{path}`", self.markdown)

    def test_uncovered_lines_are_rendered_as_ranges(self) -> None:
        self.assertIn("12–14", self.markdown)
        self.assertIn("30–31", self.markdown)

    def test_files_without_coverage_data_say_so(self) -> None:
        self.assertIn("_no coverage data_", self.markdown)

    def test_skipped_files_are_folded_away(self) -> None:
        self.assertIn("<details>", self.markdown)
        self.assertIn("`README.md`", self.markdown)

    def test_worst_file_comes_first(self) -> None:
        self.assertLess(self.markdown.index("app/new_thing.py"), self.markdown.index("app/models.py"))

    def test_links_point_at_the_exact_lines(self) -> None:
        linker = Linker("https://github.com", "velis74/demo", "abc123")
        linked = render_markdown(self.report, linker)
        self.assertIn("https://github.com/velis74/demo/blob/abc123/vue/src/table.ts#L12-L14", linked)
        self.assertIn("https://github.com/velis74/demo/blob/abc123/vue/src/table.ts#L22", linked)

    def test_passing_report(self) -> None:
        report = sample_report(threshold=10.0)
        self.assertIn("## ✅ Incremental coverage", render_markdown(report))

    def test_empty_report(self) -> None:
        markdown = render_markdown(Report(base_label="main", head_label="HEAD"))
        self.assertIn("nothing to grade", markdown)


class TestTerminal(TestCase):
    def test_failure_is_obvious(self) -> None:
        output = render_terminal(sample_report(), colour=False)
        self.assertIn("✗ FAIL", output)
        self.assertIn("threshold 80%", output)

    def test_success_is_obvious(self) -> None:
        output = render_terminal(sample_report(threshold=10.0), colour=False)
        self.assertIn("✓ PASS", output)

    def test_table_lists_files_and_ranges(self) -> None:
        output = render_terminal(sample_report(), colour=False)
        self.assertIn("vue/src/table.ts", output)
        self.assertIn("12-14, 22, 30-31", output)
        self.assertIn("TOTAL", output)

    def test_skipped_summary(self) -> None:
        self.assertIn("1 file not graded (1 not a source file)", render_terminal(sample_report(), colour=False))

    def test_colour_can_be_disabled(self) -> None:
        self.assertNotIn("\033[", render_terminal(sample_report(), colour=False))

    def test_colour_can_be_enabled(self) -> None:
        self.assertIn("\033[", render_terminal(sample_report(), colour=True))


class TestAnnotations(TestCase):
    def setUp(self) -> None:
        self.commands = render_annotations(sample_report())

    def test_one_annotation_per_uncovered_range(self) -> None:
        self.assertEqual(4, len(self.commands))

    def test_covered_files_are_not_annotated(self) -> None:
        self.assertNotIn("app/models.py", "\n".join(self.commands))

    def test_command_shape(self) -> None:
        self.assertIn(
            "::warning file=vue/src/table.ts,line=12,endLine=14,title=Uncovered by tests::",
            "\n".join(self.commands),
        )

    def test_limit_is_reported_rather_than_silent(self) -> None:
        commands = render_annotations(sample_report(), limit=2)
        self.assertIn("::notice::", commands[-1])
        self.assertIn("2 further uncovered ranges", commands[-1])


class TestAwkwardPaths(TestCase):
    """Paths are user data; they must not be able to break the report."""

    def report(self, path: str) -> Report:
        return Report(
            files=[FileResult(path, Status.PARTIAL, 2, 1, [3])],
            skipped=[SkippedFile(path, SkipReason("ignored", "ignored by `x`"))],
            threshold=80.0,
        )

    def row(self, markdown: str, needle: str) -> str:
        return next(line for line in markdown.splitlines() if line.startswith("| ") and needle in line)

    def test_pipe_in_path_is_escaped(self) -> None:
        row = self.row(render_markdown(self.report("app/we|rd.py")), "rd.py")
        self.assertIn("`app/we\\|rd.py`", row)
        # Five columns means six unescaped cell separators.
        self.assertEqual(6, row.count("|") - row.count("\\|"))

    def test_backtick_in_path_gets_a_longer_fence(self) -> None:
        row = self.row(render_markdown(self.report("app/`odd`.py")), "odd")
        self.assertIn("``app/`odd`.py``", row)

    def test_inline_code_pads_when_the_text_ends_in_a_backtick(self) -> None:
        self.assertEqual("`` `x` ``", inline_code("`x`"))
