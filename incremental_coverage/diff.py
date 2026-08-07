"""A unified-diff parser that reports which lines a change adds or modifies.

The whole diff is parsed in one pass: hunk headers give the starting line in the new
file, and the parser tracks how many lines are left in each hunk so that added
content beginning with ``+++`` or ``--`` can never be mistaken for a header.
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from .paths import normalize

# Both counts are optional: git writes "@@ -1 +1 @@" for single-line hunks.
HUNK_HEADER = re.compile(r"^@@+ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
GIT_HEADER = re.compile(r"^diff --git ")
BINARY = re.compile(r"^(GIT binary patch|Binary files .* differ)$")


@dataclass
class FileDiff:
    """Lines added or modified in one file, numbered against the new revision."""

    path: str
    added_lines: Set[int] = field(default_factory=set)
    old_path: Optional[str] = None
    is_binary: bool = False
    is_deleted: bool = False

    @property
    def is_rename(self) -> bool:
        return bool(self.old_path) and self.old_path != self.path


def _strip_prefix(path: str, side: str) -> str:
    """Turn a ``--- a/foo`` / ``+++ b/foo`` operand into a plain path."""
    text = path.strip()
    if text.startswith('"') and text.endswith('"') and len(text) > 1:
        text = text[1:-1]
    if text == "/dev/null":
        return ""
    # git uses a/ and b/ prefixes unless --no-prefix was passed.
    for prefix in (side + "/", "a/", "b/"):
        if text.startswith(prefix):
            return normalize(text[len(prefix) :])
    return normalize(text)


def _split_lines(diff: str) -> List[str]:
    """Split a diff on newlines only.

    ``str.splitlines`` also breaks on form feed, vertical tab, NEL and the Unicode
    line separators. Source files do contain those - a form feed as a page break is
    an old but living Python convention - and git passes them through verbatim.
    Splitting on them would insert a marker-less fragment into the middle of a hunk,
    desynchronising the line counter and silently dropping the rest of the hunk from
    grading, which makes the gate report a pass on untested lines.
    """
    if not diff:
        return []
    return [line[:-1] if line.endswith("\r") else line for line in diff.split("\n")]


def parse_diff(diff: str) -> Dict[str, FileDiff]:
    """Parse a unified diff into ``{path: FileDiff}`` keyed by the new path.

    Deleted files and binary files are reported with no added lines rather than
    dropped, so callers can tell "nothing to check" from "not in the diff".
    """
    files: Dict[str, FileDiff] = {}
    current: Optional[FileDiff] = None
    pending_old: Optional[str] = None
    new_line = 0
    remaining_new = 0
    remaining_old = 0

    def commit(entry: Optional[FileDiff]) -> None:
        if entry is None or not entry.path:
            return
        existing = files.get(entry.path)
        if existing is None:
            files[entry.path] = entry
        else:
            existing.added_lines |= entry.added_lines
            existing.is_binary = existing.is_binary or entry.is_binary

    for raw in _split_lines(diff):
        in_hunk = remaining_new > 0 or remaining_old > 0

        if not in_hunk:
            if GIT_HEADER.match(raw):
                commit(current)
                current, pending_old = None, None
                continue
            if raw.startswith("--- "):
                pending_old = _strip_prefix(raw[4:], "a")
                continue
            if raw.startswith("+++ "):
                path = _strip_prefix(raw[4:], "b")
                commit(current)
                if path:
                    current = FileDiff(path=path, old_path=pending_old or None)
                else:
                    # +++ /dev/null - the file was deleted.
                    current = FileDiff(path=pending_old or "", is_deleted=True)
                pending_old = None
                continue
            if BINARY.match(raw.strip()):
                if current is not None:
                    current.is_binary = True
                continue

        header = HUNK_HEADER.match(raw)
        if header and not in_hunk:
            new_line = int(header.group(3))
            remaining_new = int(header.group(4)) if header.group(4) is not None else 1
            remaining_old = int(header.group(2)) if header.group(2) is not None else 1
            continue

        if not in_hunk:
            continue

        # Inside a hunk every line carries a marker; anything else ends it early.
        if raw.startswith("+"):
            if current is not None:
                current.added_lines.add(new_line)
            new_line += 1
            remaining_new -= 1
        elif raw.startswith("-"):
            remaining_old -= 1
        elif raw.startswith(" ") or raw == "":
            new_line += 1
            remaining_new -= 1
            remaining_old -= 1
        elif raw.startswith("\\"):
            # "\ No newline at end of file" - belongs to the previous line.
            continue
        else:
            remaining_new = remaining_old = 0

        remaining_new = max(remaining_new, 0)
        remaining_old = max(remaining_old, 0)

    commit(current)
    return files


def changed_lines(diff: str) -> Dict[str, List[int]]:
    """Convenience wrapper returning sorted added-line numbers per file."""
    return {path: sorted(entry.added_lines) for path, entry in parse_diff(diff).items()}
