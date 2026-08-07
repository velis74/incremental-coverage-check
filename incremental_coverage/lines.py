"""Helpers for turning sets of line numbers into human readable ranges."""

from __future__ import annotations

from typing import Callable, Iterable, List, Optional, Tuple


def to_ranges(lines: Iterable[int]) -> List[Tuple[int, int]]:
    """Collapse line numbers into sorted, inclusive ``(start, end)`` ranges."""
    ranges: List[Tuple[int, int]] = []
    for line in sorted(set(lines)):
        if ranges and line == ranges[-1][1] + 1:
            ranges[-1] = (ranges[-1][0], line)
        else:
            ranges.append((line, line))
    return ranges


def format_ranges(
    lines: Iterable[int],
    dash: str = "-",
    separator: str = ", ",
    empty: str = "",
    limit: Optional[int] = None,
    render: Optional[Callable[[int, int, str], str]] = None,
) -> str:
    """Render line numbers as ``1-3, 5, 12-17``.

    ``limit`` caps the number of ranges shown and appends a ``(+n more)`` hint so a
    file with a hundred uncovered fragments cannot flood the report.  ``render``
    receives ``(start, end, text)`` and may wrap each range, e.g. in a link.
    """
    ranges = to_ranges(lines)
    if not ranges:
        return empty

    shown = ranges if limit is None else ranges[:limit]
    parts = []
    for start, end in shown:
        text = str(start) if start == end else f"{start}{dash}{end}"
        parts.append(render(start, end, text) if render else text)

    rendered = separator.join(parts)
    hidden = len(ranges) - len(shown)
    return f"{rendered} (+{hidden} more)" if hidden else rendered


def count(lines: Iterable[int]) -> int:
    return len(set(lines))
