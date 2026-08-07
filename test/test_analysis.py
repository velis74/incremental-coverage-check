from unittest import TestCase

from incremental_coverage.analysis import analyse, Status
from incremental_coverage.coverage import CoverageData
from incremental_coverage.diff import FileDiff
from incremental_coverage.ignore import FileFilter


def diffs(**files):
    return {path: FileDiff(path=path, added_lines=set(lines)) for path, lines in files.items()}


class AnalysisTestCase(TestCase):
    def setUp(self) -> None:
        self.filter = FileFilter()
        self.coverage = CoverageData()

    def analyse(self, changed, threshold: float = 70.0):
        return analyse(changed, self.coverage, self.filter, threshold)


class TestGrading(AnalysisTestCase):
    def test_fully_covered_file(self) -> None:
        self.coverage.add("app/models.py", covered=[1, 2, 3], uncovered=[])
        report = self.analyse(diffs(**{"app/models.py": [1, 2, 3]}))
        entry = report.files[0]
        self.assertEqual(Status.COVERED, entry.status)
        self.assertEqual(100.0, entry.percentage)
        self.assertTrue(report.passed)

    def test_partially_covered_file(self) -> None:
        self.coverage.add("app/models.py", covered=[1, 2], uncovered=[3, 4])
        report = self.analyse(diffs(**{"app/models.py": [1, 2, 3, 4]}))
        entry = report.files[0]
        self.assertEqual(Status.PARTIAL, entry.status)
        self.assertEqual([3, 4], entry.uncovered)
        self.assertEqual(50.0, entry.percentage)

    def test_only_changed_lines_are_graded(self) -> None:
        """Lines the change did not touch never count, covered or not."""
        self.coverage.add("app/models.py", covered=[1], uncovered=[2, 3, 4, 5])
        report = self.analyse(diffs(**{"app/models.py": [1, 2]}))
        self.assertEqual(2, report.total_relevant)
        self.assertEqual(1, report.total_covered)
        self.assertEqual([2], report.files[0].uncovered)

    def test_non_executable_changed_lines_are_ignored(self) -> None:
        """Comments and blank lines are in neither coverage set, so they do not count."""
        self.coverage.add("app/models.py", covered=[10], uncovered=[])
        report = self.analyse(diffs(**{"app/models.py": [1, 2, 3, 10]}))
        self.assertEqual(1, report.total_relevant)
        self.assertEqual(100.0, report.percentage)

    def test_change_with_no_executable_lines_at_all(self) -> None:
        self.coverage.add("app/models.py", covered=[10], uncovered=[])
        report = self.analyse(diffs(**{"app/models.py": [1, 2]}))
        self.assertEqual(0, report.total_relevant)
        self.assertFalse(report.has_content)
        self.assertTrue(report.passed)
        self.assertEqual("no executable lines changed", report.files[0].note)


class TestFilesWithoutCoverageData(AnalysisTestCase):
    def test_every_changed_line_counts_as_uncovered(self) -> None:
        report = self.analyse(diffs(**{"app/brand_new.py": [1, 2, 3, 4, 5]}))
        entry = report.files[0]
        self.assertEqual(Status.NO_DATA, entry.status)
        self.assertEqual(5, entry.relevant_lines)
        self.assertEqual(0, entry.covered_lines)
        self.assertEqual([1, 2, 3, 4, 5], entry.uncovered)
        self.assertEqual(0.0, entry.percentage)
        self.assertFalse(report.passed)

    def test_counted_once_not_twice(self) -> None:
        """The old version added the whole file to the total and the diff to the report."""
        report = self.analyse(diffs(**{"app/brand_new.py": [1, 2, 3]}))
        self.assertEqual(3, report.total_relevant)
        self.assertEqual(3, report.total_uncovered)
        self.assertEqual(0.0, report.percentage)

    def test_does_not_drag_down_files_that_are_covered(self) -> None:
        self.coverage.add("app/models.py", covered=list(range(1, 91)), uncovered=[])
        changed = {"app/models.py": list(range(1, 91)), "app/new.py": list(range(1, 11))}
        report = self.analyse(diffs(**changed))
        self.assertEqual(100, report.total_relevant)
        self.assertEqual(90.0, report.percentage)


class TestSkipping(AnalysisTestCase):
    def test_ignored_and_non_source_files(self) -> None:
        report = self.analyse(diffs(**{"README.md": [1], "app/migrations/0001.py": [1, 2]}))
        self.assertEqual([], report.files)
        self.assertEqual(2, len(report.skipped))

    def test_deleted_binary_and_empty_diffs(self) -> None:
        changed = {
            "gone.py": FileDiff(path="gone.py", is_deleted=True),
            "logo.png": FileDiff(path="logo.png", is_binary=True),
            "empty.py": FileDiff(path="empty.py"),
        }
        report = self.analyse(changed)
        self.assertEqual([], report.files)
        self.assertEqual({"deleted", "binary", "no-changes"}, {item.reason.kind for item in report.skipped})

    def test_nothing_to_grade_passes(self) -> None:
        report = self.analyse(diffs(**{"README.md": [1]}))
        self.assertTrue(report.passed)
        self.assertFalse(report.has_content)


class TestThreshold(AnalysisTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.coverage.add("app/models.py", covered=[1, 2, 3, 4, 5, 6, 7], uncovered=[8, 9, 10])

    def changed(self):
        return diffs(**{"app/models.py": list(range(1, 11))})

    def test_exactly_on_the_threshold_passes(self) -> None:
        self.assertTrue(self.analyse(self.changed(), threshold=70).passed)

    def test_below_the_threshold_fails(self) -> None:
        self.assertFalse(self.analyse(self.changed(), threshold=71).passed)

    def test_percentage(self) -> None:
        self.assertEqual(70.0, self.analyse(self.changed()).percentage)


class TestOrdering(AnalysisTestCase):
    def test_worst_files_come_first(self) -> None:
        self.coverage.add("good.py", covered=[1, 2], uncovered=[])
        self.coverage.add("bad.py", covered=[], uncovered=[1, 2])
        self.coverage.add("middling.py", covered=[1], uncovered=[2])
        report = self.analyse(diffs(**{"good.py": [1, 2], "bad.py": [1, 2], "middling.py": [1, 2]}))
        self.assertEqual(["bad.py", "middling.py", "good.py"], [entry.path for entry in report.sorted_files()])
