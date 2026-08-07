"""The actual check: which changed lines are covered, and does that clear the bar.

Scoring rules
-------------
* Only lines the diff **adds or modifies** are considered - deletions and context
  are irrelevant to "is the new code tested".
* Of those, only lines the coverage tool considers **executable** are graded, so
  comments, blank lines and import-only changes neither help nor hurt.
* A changed file with **no coverage data at all** counts as fully uncovered.  That
  is deliberate: a brand new module nobody wrote a test for is exactly the case
  this check exists to catch, and silently skipping it would be the easiest way to
  sneak untested code past the gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Sequence, Set

from .coverage import CoverageData
from .diff import FileDiff
from .ignore import FileFilter, SkipReason


class Status(str, Enum):
    COVERED = "covered"
    PARTIAL = "partial"
    UNCOVERED = "uncovered"
    NO_DATA = "no-data"

    @property
    def is_problem(self) -> bool:
        return self is not Status.COVERED


@dataclass
class FileResult:
    """The verdict for one graded file."""

    path: str
    status: Status
    relevant_lines: int
    covered_lines: int
    uncovered: List[int] = field(default_factory=list)
    note: Optional[str] = None

    @property
    def percentage(self) -> float:
        if not self.relevant_lines:
            return 100.0
        return 100.0 * self.covered_lines / self.relevant_lines

    @property
    def uncovered_count(self) -> int:
        return len(self.uncovered)

    def sort_key(self):
        """Worst first: lowest coverage, then most uncovered lines, then by path."""
        return (self.percentage, -self.uncovered_count, self.path)


@dataclass
class SkippedFile:
    path: str
    reason: SkipReason


@dataclass
class Report:
    """Everything the renderers need; no formatting decisions live in here."""

    files: List[FileResult] = field(default_factory=list)
    skipped: List[SkippedFile] = field(default_factory=list)
    threshold: float = 70.0
    base_label: str = ""
    head_label: str = ""
    base_sha: str = ""
    head_sha: str = ""
    sources: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def total_relevant(self) -> int:
        return sum(entry.relevant_lines for entry in self.files)

    @property
    def total_covered(self) -> int:
        return sum(entry.covered_lines for entry in self.files)

    @property
    def total_uncovered(self) -> int:
        return self.total_relevant - self.total_covered

    @property
    def percentage(self) -> float:
        if not self.total_relevant:
            return 100.0
        return 100.0 * self.total_covered / self.total_relevant

    @property
    def has_content(self) -> bool:
        """False when the change set contains nothing worth grading."""
        return self.total_relevant > 0

    @property
    def passed(self) -> bool:
        return not self.has_content or self.percentage + 1e-9 >= self.threshold

    @property
    def problem_files(self) -> List[FileResult]:
        return [entry for entry in self.files if entry.status.is_problem]

    def sorted_files(self) -> List[FileResult]:
        return sorted(self.files, key=lambda entry: entry.sort_key())


def analyse(
    diffs: Dict[str, FileDiff],
    coverage: CoverageData,
    file_filter: FileFilter,
    threshold: float = 70.0,
) -> Report:
    """Grade every changed file and produce a :class:`Report`."""
    report = Report(threshold=threshold)

    for path in sorted(diffs):
        entry = diffs[path]

        if entry.is_deleted:
            report.skipped.append(SkippedFile(path, SkipReason("deleted", "deleted")))
            continue
        if entry.is_binary:
            report.skipped.append(SkippedFile(path, SkipReason("binary", "binary file")))
            continue
        if not entry.added_lines:
            report.skipped.append(SkippedFile(path, SkipReason("no-changes", "no added lines")))
            continue

        skip = file_filter.skip_reason(path)
        if skip is not None:
            report.skipped.append(SkippedFile(path, skip))
            continue

        report.files.append(_grade(path, entry.added_lines, coverage))

    graded = {entry.path for entry in report.files}

    for changed_path, candidates in coverage.ambiguous_lookups:
        if changed_path in graded:
            report.warnings.append(
                f"`{changed_path}` matches several coverage entries "
                f"({', '.join('`' + candidate + '`' for candidate in candidates)}), so none was used. "
                f"It is counted as uncovered."
            )

    for reported_path, candidates in coverage.unresolved:
        if any(candidate in graded for candidate in candidates):
            report.warnings.append(
                f"`{reported_path}` in the coverage report could be any of "
                f"{', '.join('`' + candidate + '`' for candidate in candidates)}, so it was ignored. "
                f"Point the coverage tool at a single source root, or run it from the repository root."
            )

    return report


def _grade(path: str, added_lines: Set[int], coverage: CoverageData) -> FileResult:
    file_coverage = coverage.lookup(path)

    if file_coverage is None:
        # No coverage data: the whole change is treated as untested.
        uncovered = sorted(added_lines)
        return FileResult(
            path=path,
            status=Status.NO_DATA,
            relevant_lines=len(uncovered),
            covered_lines=0,
            uncovered=uncovered,
            note="no coverage data",
        )

    executable = file_coverage.executable_lines
    relevant = added_lines & executable
    if not relevant:
        # Every changed line is a comment, a blank line or otherwise not runnable.
        return FileResult(
            path=path,
            status=Status.COVERED,
            relevant_lines=0,
            covered_lines=0,
            uncovered=[],
            note="no executable lines changed",
        )

    uncovered = sorted(relevant & file_coverage.uncovered_lines)
    covered_count = len(relevant) - len(uncovered)

    if not uncovered:
        status = Status.COVERED
    elif covered_count:
        status = Status.PARTIAL
    else:
        status = Status.UNCOVERED

    return FileResult(
        path=path,
        status=status,
        relevant_lines=len(relevant),
        covered_lines=covered_count,
        uncovered=uncovered,
    )


def group_skipped(skipped: Sequence[SkippedFile]) -> Dict[str, int]:
    """Count skipped files by reason kind, for the one-line summary."""
    counts: Dict[str, int] = {}
    for item in skipped:
        counts[item.reason.kind] = counts.get(item.reason.kind, 0) + 1
    return counts
