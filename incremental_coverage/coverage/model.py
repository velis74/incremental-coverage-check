"""The format-independent coverage model every parser produces."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set, Tuple

from ..paths import normalize, PathResolver, SuffixIndex


class CoverageParseError(Exception):
    """Raised when a coverage report cannot be read or understood."""


@dataclass
class FileCoverage:
    """Per-file line coverage.

    Only lines the coverage tool considers executable appear in either set; blank
    lines, comments and (for Python) excluded lines are in neither.  That
    distinction is what keeps comment-only changes out of the score.
    """

    path: str
    covered_lines: Set[int] = field(default_factory=set)
    uncovered_lines: Set[int] = field(default_factory=set)

    @property
    def executable_lines(self) -> Set[int]:
        return self.covered_lines | self.uncovered_lines

    def merge(self, other: FileCoverage) -> None:
        """Fold *other* into this one; a hit in any report wins over a miss."""
        self.covered_lines |= other.covered_lines
        self.uncovered_lines |= other.uncovered_lines
        self.uncovered_lines -= self.covered_lines


@dataclass
class CoverageSource:
    """One coverage report file that was read."""

    path: str
    format: str
    file_count: int


class CoverageData:
    """All coverage reports, merged and queryable by repo-relative path."""

    def __init__(self) -> None:
        self.files: Dict[str, FileCoverage] = {}
        self.sources: List[CoverageSource] = []
        self.hints: List[str] = []
        self.unresolved: List[Tuple[str, List[str]]] = []
        self._index: Optional[SuffixIndex] = None

    def __len__(self) -> int:
        return len(self.files)

    def __bool__(self) -> bool:
        return bool(self.files)

    def add(self, path: str, covered: Iterable[int], uncovered: Iterable[int]) -> None:
        key = normalize(path)
        entry = FileCoverage(key, set(covered), set(uncovered))
        entry.uncovered_lines -= entry.covered_lines
        if key in self.files:
            self.files[key].merge(entry)
        else:
            self.files[key] = entry
        self._index = None

    def merge(self, other: CoverageData) -> None:
        for entry in other.files.values():
            self.add(entry.path, entry.covered_lines, entry.uncovered_lines)
        self.sources.extend(other.sources)
        self.unresolved.extend(other.unresolved)

    def resolved(self, resolver: PathResolver, report_dir: str = "") -> CoverageData:
        """A copy whose keys are paths in the checkout rather than in the report.

        Entries the resolver cannot pin down to a single file are dropped and listed
        in :attr:`unresolved`. Dropping is the safe direction: the file then has no
        coverage data and counts as uncovered, instead of silently inheriting the
        coverage of a same-named file from elsewhere in the tree.
        """
        out = CoverageData()
        for entry in self.files.values():
            path, alternatives = resolver.resolve(entry.path, report_dir, self.hints)
            if alternatives:
                out.unresolved.append((entry.path, [path, *alternatives]))
                continue
            out.add(path, entry.covered_lines, entry.uncovered_lines)
        out.sources = [
            CoverageSource(path=source.path, format=source.format, file_count=len(out.files)) for source in self.sources
        ]
        return out

    def lookup(self, path: str) -> Optional[FileCoverage]:
        if self._index is None:
            self._index = SuffixIndex()
            for entry in self.files.values():
                self._index.add(entry.path, entry)
        return self._index.get(path)

    @property
    def ambiguous_lookups(self) -> List:
        return self._index.ambiguous if self._index else []
