"""Path normalisation and fuzzy matching between coverage reports and the repo.

Coverage tools disagree wildly about how they spell a file path: coverage.py emits
paths relative to wherever it ran, istanbul emits absolute paths from the machine
that produced the report, Cobertura splits the path between ``<source>`` and
``filename``.  Everything here exists to map those back onto repo-relative paths.
"""

from __future__ import annotations

import os

from collections import defaultdict
from typing import Dict, Generic, List, Optional, Sequence, Tuple, TypeVar

T = TypeVar("T")


def normalize(path: str) -> str:
    """Normalise a path to POSIX separators without leading ``./`` or quotes."""
    text = path.strip()
    if len(text) > 1 and text.startswith('"') and text.endswith('"'):
        text = text[1:-1]
    text = text.replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.rstrip("/")


def is_absolute(path: str) -> bool:
    return path.startswith("/") or (len(path) > 2 and path[1] == ":")


def make_relative(path: str, root: str) -> str:
    """Express *path* relative to *root* when it lives underneath it.

    Purely textual - the file need not exist.  Paths that are not under *root* are
    returned normalised but otherwise untouched, so suffix matching can have a go.
    """
    candidate = normalize(path)
    for base in (os.path.abspath(root), os.path.realpath(root)):
        normalized = normalize(base)
        if candidate == normalized:
            return ""
        if candidate.startswith(normalized + "/"):
            return candidate[len(normalized) + 1 :]
    return candidate


def shared_suffix_segments(a: str, b: str) -> int:
    """Number of trailing path segments *a* and *b* have in common."""
    segments_a = a.split("/")
    segments_b = b.split("/")
    matched = 0
    for left, right in zip(reversed(segments_a), reversed(segments_b)):
        if left != right:
            break
        matched += 1
    return matched


def is_path_suffix(long: str, short: str) -> bool:
    """True when *short* is a whole-segment tail of *long* (or they are equal)."""
    return long == short or long.endswith("/" + short)


class PathResolver:
    """Maps a path out of a coverage report onto a file in the checkout.

    Coverage tools spell paths relative to wherever they ran, which in a monorepo is
    ambiguous on its own: ``src/index.ts`` from ``packages/a/coverage/lcov.info`` and
    ``src/index.ts`` from ``packages/b/coverage/lcov.info`` are different files with
    the same name. Guessing between them can attribute one package's coverage to the
    other's untested code, so resolution is decided against the working tree instead:
    a candidate only counts if that file actually exists in the checkout.

    Candidates are tried in two tiers. The first holds paths derived from something
    the report told us - the workspace prefix, a Cobertura ``<source>`` root, or the
    directory the report itself sits in. Only if none of those exist does the second
    tier fall back to trimming leading segments off the path. When the first tier
    that produces a hit produces more than one, the answer is genuinely ambiguous and
    is reported as such rather than picked at random.
    """

    def __init__(self, root: str) -> None:
        self.root = os.path.abspath(root)
        self._exists_cache: Dict[str, bool] = {}

    def exists(self, relative: str) -> bool:
        """Whether *relative* names a file inside the checkout.

        Absolute candidates and ones that climb out with ``..`` are rejected outright:
        ``os.path.join`` would quietly discard the root and start testing paths
        elsewhere on the runner.
        """
        if not relative or is_absolute(relative) or ".." in relative.split("/"):
            return False
        if relative not in self._exists_cache:
            self._exists_cache[relative] = os.path.isfile(os.path.join(self.root, relative))
        return self._exists_cache[relative]

    def resolve(
        self,
        raw: str,
        report_dir: str = "",
        hints: Sequence[str] = (),
    ) -> Tuple[str, List[str]]:
        """Return ``(path, alternatives)``.

        ``alternatives`` is empty when the answer is certain. When it is not, it
        lists every equally plausible candidate and the caller should refuse to use
        the coverage rather than guess.
        """
        path = normalize(raw)
        if not path:
            return path, []

        for tier in (self._anchored(path, report_dir, hints), self._trimmed(path)):
            found = [candidate for candidate in tier if self.exists(candidate)]
            unique = list(dict.fromkeys(found))
            if unique:
                return unique[0], unique[1:] if len(unique) > 1 else []

        # Not in the checkout at all: a deleted file, a build artefact, or a report
        # from a tree that shares nothing with this one. Keep it for suffix matching.
        return make_relative(path, self.root), []

    def _anchored(self, path: str, report_dir: str, hints: Sequence[str]) -> List[str]:
        candidates: List[str] = []

        if is_absolute(path):
            relative = make_relative(path, self.root)
            if relative != path:
                candidates.append(relative)
            return candidates

        for hint in hints:
            candidates.append(self._under_root(hint, path))

        # Walk from the report's own directory up to the repository root: a report in
        # packages/a/coverage naming src/index.ts almost certainly means
        # packages/a/src/index.ts.
        directory = make_relative(report_dir, self.root) if report_dir else ""
        if is_absolute(directory):
            directory = ""
        while True:
            candidates.append(f"{directory}/{path}" if directory else path)
            if not directory:
                break
            directory = directory.rsplit("/", 1)[0] if "/" in directory else ""

        return [candidate for candidate in candidates if candidate]

    def _under_root(self, hint: str, path: str) -> str:
        joined = normalize(hint) + "/" + path if hint else path
        return make_relative(joined, self.root) if is_absolute(joined) else joined

    def _trimmed(self, path: str) -> List[str]:
        """Drop leading segments, longest match first."""
        segments = normalize(path).lstrip("/").split("/")
        return ["/".join(segments[index:]) for index in range(len(segments))]


class SuffixIndex(Generic[T]):
    """Look values up by path, tolerating differing path prefixes.

    A lookup succeeds when one path is a whole-segment suffix of the other, so
    ``vue/src/table.ts`` finds a report entry stored as ``src/table.ts`` (vitest ran
    inside ``vue/``) or ``/home/runner/work/x/x/vue/src/table.ts`` (istanbul).
    """

    def __init__(self) -> None:
        self._exact: Dict[str, T] = {}
        self._by_basename: Dict[str, List[str]] = defaultdict(list)
        self.ambiguous: List[Tuple[str, List[str]]] = []

    def __len__(self) -> int:
        return len(self._exact)

    def __contains__(self, path: str) -> bool:
        return self.get(path) is not None

    def add(self, path: str, value: T) -> None:
        key = normalize(path)
        self._exact[key] = value
        basename = key.rsplit("/", 1)[-1]
        if key not in self._by_basename[basename]:
            self._by_basename[basename].append(key)

    def get(self, path: str) -> Optional[T]:
        """The value for *path*, or ``None`` when there is no unambiguous match.

        A tie is deliberately not broken. Picking one of several candidates would
        let a different file's coverage be credited to this one, which is how a gate
        ends up reporting a pass on untested code; returning nothing instead makes
        the file count as uncovered, and the tie is recorded so it can be reported.
        """
        key = normalize(path)
        if key in self._exact:
            return self._exact[key]

        basename = key.rsplit("/", 1)[-1]
        best: List[str] = []
        best_score = 0
        for candidate in self._by_basename.get(basename, ()):
            if not (is_path_suffix(candidate, key) or is_path_suffix(key, candidate)):
                continue
            score = shared_suffix_segments(candidate, key)
            if score > best_score:
                best, best_score = [candidate], score
            elif score == best_score and score:
                best.append(candidate)

        if not best:
            return None
        if len(best) > 1:
            best.sort()
            self.ambiguous.append((key, list(best)))
            return None
        return self._exact[best[0]]

    def keys(self) -> List[str]:
        return list(self._exact)

    def values(self) -> List[T]:
        return list(self._exact.values())
