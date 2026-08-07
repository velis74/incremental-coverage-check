"""Deciding which changed files the coverage requirement applies to.

Two independent gates, in order:

1. **Source extensions** - an allow-list.  Anything that is not code the coverage
   tools could ever instrument (Markdown, JSON, lock files, images) is skipped.
   An allow-list is used deliberately: the previous suffix black-list matched on
   bare substrings, so a file called ``gemini`` was skipped because it ends in
   ``ini``.
2. **Ignore patterns** - a glob deny-list for code that is real source but should
   not be graded: tests, migrations, settings, generated files.

Patterns are matched against the repo-relative POSIX path and against the bare file
name, so ``migrations/*`` and ``*.spec.ts`` both do what they look like they do.
"""

from __future__ import annotations

import fnmatch

from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

from .paths import normalize

DEFAULT_SOURCE_EXTENSIONS: Tuple[str, ...] = (
    ".py",
    ".pyi",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".mts",
    ".cts",
    ".vue",
    ".svelte",
)

DEFAULT_IGNORE_PATTERNS: Tuple[str, ...] = (
    # Tests never need to be covered by tests.
    "test_*.py",
    "*_test.py",
    "tests.py",
    "conftest.py",
    "test/*",
    "tests/*",
    "__tests__/*",
    "__mocks__/*",
    "*.spec.*",
    "*.test.*",
    # Generated or framework-owned code.
    "migrations/*",
    "settings.py",
    "settings/*",
    "manage.py",
    "setup.py",
    "asgi.py",
    "wsgi.py",
    "*.d.ts",
    "*.min.js",
    # Tooling and configuration living next to the code.
    ".*",
    ".github/*",
    "*.config.js",
    "*.config.ts",
    "vite.config.*",
    "vitest.config.*",
)


@dataclass(frozen=True)
class SkipReason:
    """Why a changed file is not graded."""

    kind: str
    detail: str

    def __str__(self) -> str:
        return self.detail


class FileFilter:
    """Decides whether a changed file must be covered."""

    def __init__(
        self,
        source_extensions: Optional[Sequence[str]] = None,
        ignore_patterns: Optional[Sequence[str]] = None,
    ) -> None:
        extensions = DEFAULT_SOURCE_EXTENSIONS if source_extensions is None else source_extensions
        self.source_extensions = tuple(
            ext.lower() if ext.startswith(".") else "." + ext.lower() for ext in extensions if ext.strip()
        )
        patterns = DEFAULT_IGNORE_PATTERNS if ignore_patterns is None else ignore_patterns
        self.ignore_patterns = tuple(pattern.strip() for pattern in patterns if pattern.strip())

    def matching_pattern(self, path: str) -> Optional[str]:
        """The first ignore pattern that matches *path*, if any."""
        candidate = normalize(path)
        basename = candidate.rsplit("/", 1)[-1]
        for pattern in self.ignore_patterns:
            if _matches(candidate, basename, pattern):
                return pattern
        return None

    def is_source(self, path: str) -> bool:
        candidate = normalize(path).lower()
        return any(candidate.endswith(ext) for ext in self.source_extensions)

    def skip_reason(self, path: str) -> Optional[SkipReason]:
        """``None`` when the file must be covered, otherwise why it is skipped."""
        if not self.is_source(path):
            return SkipReason("not-source", "not a source file")
        pattern = self.matching_pattern(path)
        if pattern is not None:
            return SkipReason("ignored", f"ignored by `{pattern}`")
        return None


def _matches(path: str, basename: str, pattern: str) -> bool:
    """Match a gitignore-flavoured glob against a repo-relative path.

    A pattern with no slash matches the file name anywhere in the tree; a pattern
    with a slash matches the path, either anchored at the repo root or at any
    directory level.
    """
    normalized = pattern.replace("**/", "*/").rstrip("/")
    if "/" not in normalized:
        return fnmatch.fnmatch(basename, normalized)
    if fnmatch.fnmatch(path, normalized):
        return True
    return fnmatch.fnmatch(path, "*/" + normalized)


def split_patterns(raw: Optional[str]) -> Optional[List[str]]:
    """Split a newline- or comma-separated pattern list from CLI/env input."""
    if raw is None:
        return None
    items = [part.strip() for chunk in raw.splitlines() for part in chunk.split(",")]
    cleaned = [item for item in items if item]
    return cleaned or None


def iter_skipped(filter_: FileFilter, paths: Iterable[str]) -> List[Tuple[str, SkipReason]]:  # pragma: no cover
    result = []
    for path in paths:
        reason = filter_.skip_reason(path)
        if reason is not None:
            result.append((path, reason))
    return result
