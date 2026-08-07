"""Rendering the report for humans.

Three targets, one report object:

* :func:`render_terminal` - the workflow log, aligned columns and ANSI colour.
* :func:`render_markdown` - the job summary and the pull request comment, with
  every uncovered range linked to the exact lines on GitHub.
* :func:`render_annotations` - ``::warning`` workflow commands, which GitHub draws
  directly onto the changed lines in the Files tab.
"""

from __future__ import annotations

import os

from dataclasses import dataclass
from typing import List, Optional

from . import __version__, MARKER
from .analysis import group_skipped, Report, Status
from .lines import format_ranges, to_ranges

STATUS_TEXT = {
    Status.COVERED: "pass",
    Status.PARTIAL: "warn",
    Status.UNCOVERED: "fail",
    Status.NO_DATA: "fail",
}

STATUS_EMOJI = {
    Status.COVERED: "✅",
    Status.PARTIAL: "⚠️",
    Status.UNCOVERED: "❌",
    Status.NO_DATA: "🚫",
}

STATUS_SYMBOL = {
    Status.COVERED: "✓",
    Status.PARTIAL: "!",
    Status.UNCOVERED: "✗",
    Status.NO_DATA: "✗",
}

SKIP_LABELS = {
    "not-source": ("not a source file", "not source files"),
    "ignored": ("ignored by pattern", "ignored by pattern"),
    "no-changes": ("no added lines", "no added lines"),
    "deleted": ("deleted", "deleted"),
    "binary": ("binary", "binary"),
}

MAX_RANGES_PER_FILE = 8
MAX_ANNOTATIONS = 40


# --------------------------------------------------------------------------- #
# Small formatting helpers
# --------------------------------------------------------------------------- #


def format_percentage(value: float) -> str:
    """``73`` and ``73.4`` rather than a misleading ``73.0`` or rounded ``73``."""
    if abs(value - round(value)) < 0.05:
        return f"{value:.0f}%"
    return f"{value:.1f}%"


def truncate_left(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    return "…" + text[-(width - 1) :]


def progress_bar(percentage: float, width: int = 24, filled: str = "█", empty: str = "░") -> str:
    blocks = int(round(percentage / 100.0 * width))
    blocks = max(0, min(width, blocks))
    return filled * blocks + empty * (width - blocks)


class Palette:
    """ANSI colours, silently disabled when the output cannot show them."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def __call__(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def green(self, text: str) -> str:
        return self(text, "32")

    def yellow(self, text: str) -> str:
        return self(text, "33")

    def red(self, text: str) -> str:
        return self(text, "31")

    def dim(self, text: str) -> str:
        return self(text, "2")

    def bold(self, text: str) -> str:
        return self(text, "1")

    def status(self, status: Status, text: str) -> str:
        if status is Status.COVERED:
            return self.green(text)
        if status is Status.PARTIAL:
            return self.yellow(text)
        return self.red(text)


def colour_enabled(stream=None) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR") or os.environ.get("GITHUB_ACTIONS") == "true":
        return True
    return bool(stream is not None and getattr(stream, "isatty", lambda: False)())


@dataclass
class Linker:
    """Builds ``blob`` links into the repository at the revision under test."""

    server_url: str
    repository: str
    sha: str

    @classmethod
    def from_env(cls, sha: str) -> Optional[Linker]:
        repository = os.environ.get("GITHUB_REPOSITORY", "")
        if not repository or not sha:
            return None
        server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
        return cls(server, repository, sha)

    def file(self, path: str) -> str:
        return f"{self.server_url}/{self.repository}/blob/{self.sha}/{path}"

    def lines(self, path: str, start: int, end: int) -> str:
        anchor = f"#L{start}" if start == end else f"#L{start}-L{end}"
        return self.file(path) + anchor


def plural(count: int, singular: str, many: Optional[str] = None) -> str:
    """``1 file`` / ``3 files`` - small, but the report is read by people."""
    return f"{count} {singular if count == 1 else (many or singular + 's')}"


def _skip_summary(report: Report) -> str:
    counts = group_skipped(report.skipped)
    if not counts:
        return ""
    parts = []
    for kind, count in sorted(counts.items()):
        singular, many = SKIP_LABELS.get(kind, (kind, kind))
        parts.append(f"{count} {singular if count == 1 else many}")
    return f"{plural(len(report.skipped), 'file')} not graded ({', '.join(parts)})"


def _verdict_line(report: Report) -> str:
    if not report.has_content:
        return "no gradable lines changed"
    return (
        f"{report.total_covered} of {report.total_relevant} changed lines covered "
        f"({format_percentage(report.percentage)}), threshold {format_percentage(report.threshold)}"
    )


# --------------------------------------------------------------------------- #
# Terminal
# --------------------------------------------------------------------------- #


def render_terminal(report: Report, width: int = 0, colour: Optional[bool] = None) -> str:
    palette = Palette(colour_enabled() if colour is None else colour)
    width = width or _terminal_width()
    out: List[str] = []

    headline = "INCREMENTAL COVERAGE"
    score = format_percentage(report.percentage) if report.has_content else "n/a"
    out.append("")
    out.append(palette.bold(f"  {headline}  ·  {score}"))
    out.append(palette.dim("  " + "─" * (width - 4)))
    revisions = f"{report.base_label} ({report.base_sha[:7]}) → {report.head_label} ({report.head_sha[:7]})"
    out.append(palette.dim(f"  {revisions}"))
    if report.sources:
        out.append(palette.dim(f"  coverage: {', '.join(report.sources)}"))
    out.append("")

    if report.files:
        out.extend(_terminal_table(report, palette, width))
        out.append("")

    skipped = _skip_summary(report)
    if skipped:
        out.append(palette.dim(f"  {skipped}"))

    for warning in report.warnings:
        out.append(palette.yellow(f"  ! {warning}"))

    out.append("")
    if not report.has_content:
        out.append(palette.dim("  ✓ PASS   nothing to grade in this change"))
    elif report.passed:
        out.append(palette.green(f"  ✓ PASS   {_verdict_line(report)}"))
    else:
        out.append(palette.red(f"  ✗ FAIL   {_verdict_line(report)}"))
    out.append("")
    return "\n".join(out)


def _terminal_width() -> int:
    try:
        columns = int(os.environ.get("COLUMNS", "0"))
    except ValueError:
        columns = 0
    if not columns:
        try:
            columns = os.get_terminal_size().columns
        except OSError:
            columns = 100
    return max(80, min(columns, 140))


def _terminal_table(report: Report, palette: Palette, width: int) -> List[str]:
    rows = report.sorted_files()

    path_width = max([len(row.path) for row in rows] + [len("FILE")])
    path_width = min(path_width, max(24, width - 46))
    ratio_width = max([len(f"{row.covered_lines}/{row.relevant_lines}") for row in rows] + [5])

    header = f"  {'':2} {'FILE'.ljust(path_width)}  {'COV'.rjust(6)}  {'LINES'.rjust(ratio_width)}  UNCOVERED"
    rule = "  " + "─" * (width - 4)

    out = [palette.dim(header), palette.dim(rule)]
    for row in rows:
        symbol = palette.status(row.status, STATUS_SYMBOL[row.status])
        path = truncate_left(row.path, path_width).ljust(path_width)
        percentage = format_percentage(row.percentage).rjust(6)
        ratio = f"{row.covered_lines}/{row.relevant_lines}".rjust(ratio_width)
        detail = format_ranges(row.uncovered, empty="–", limit=MAX_RANGES_PER_FILE)
        if row.note:
            detail = f"{detail}  ({row.note})" if row.uncovered else f"({row.note})"
        line = f"  {symbol}  {path}  {palette.status(row.status, percentage)}  {ratio}  {detail}"
        out.append(line.rstrip())

    out.append(palette.dim(rule))
    total_ratio = f"{report.total_covered}/{report.total_relevant}".rjust(ratio_width)
    total = (
        f"  {'':2} {'TOTAL'.ljust(path_width)}  "
        f"{format_percentage(report.percentage).rjust(6)}  {total_ratio}  "
        f"{progress_bar(report.percentage)}"
    )
    out.append(palette.bold(total))
    return out


# --------------------------------------------------------------------------- #
# Markdown
# --------------------------------------------------------------------------- #


def render_markdown(report: Report, linker: Optional[Linker] = None, include_marker: bool = True) -> str:
    out: List[str] = []
    if include_marker:
        out.append(MARKER)

    if not report.has_content:
        icon, verdict = "✅", "Incremental coverage — nothing to grade"
    elif report.passed:
        icon, verdict = "✅", f"Incremental coverage — {format_percentage(report.percentage)}"
    else:
        icon, verdict = "❌", f"Incremental coverage — {format_percentage(report.percentage)}"

    out.append(f"## {icon} {verdict}")
    out.append("")

    if report.has_content:
        bar = progress_bar(report.percentage, width=20)
        out.append(
            f"`{bar}` **{report.total_covered} / {report.total_relevant}** changed lines covered · "
            f"threshold **{format_percentage(report.threshold)}** · "
            f"`{report.base_label}` → `{report.head_label}`"
        )
        if not report.passed:
            out.append("")
            out.append(
                f"> **{report.total_uncovered} changed "
                f"{'line is' if report.total_uncovered == 1 else 'lines are'} not covered by any test.** "
                f"Add tests for the lines listed below, or lower the `threshold` input."
            )
    else:
        out.append(f"No executable lines were changed between `{report.base_label}` and `{report.head_label}`.")
    out.append("")

    if report.files:
        out.extend(_markdown_table(report, linker))
        out.append("")

    out.extend(_markdown_skipped(report))
    out.extend(_markdown_warnings(report))
    out.extend(_markdown_footer(report))
    return "\n".join(out).rstrip() + "\n"


def inline_code(text: str) -> str:
    """A code span that survives a GFM table, whatever the path contains.

    Pipes must be backslash-escaped even inside code spans, and a path containing
    backticks needs a longer fence than the run it holds.
    """
    escaped = text.replace("|", "\\|")
    longest = run = 0
    for character in escaped:
        run = run + 1 if character == "`" else 0
        longest = max(longest, run)
    fence = "`" * (longest + 1)
    padding = " " if escaped.startswith("`") or escaped.endswith("`") else ""
    return f"{fence}{padding}{escaped}{padding}{fence}"


def _range_linker(linker: Optional[Linker], path: str):
    """A ``format_ranges`` renderer that turns each range into a link, or ``None``."""
    if linker is None:
        return None

    def render(start: int, end: int, text: str) -> str:
        return f"[{text}]({linker.lines(path, start, end)})"

    return render


def _markdown_table(report: Report, linker: Optional[Linker]) -> List[str]:
    out = [
        "| | File | Coverage | Covered | Uncovered lines |",
        "| :-: | :-- | --: | --: | :-- |",
    ]
    for row in report.sorted_files():
        emoji = STATUS_EMOJI[row.status]
        name = inline_code(row.path)
        if linker:
            name = f"[{name}]({linker.file(row.path)})"

        if row.uncovered:
            render = _range_linker(linker, row.path)
            detail = format_ranges(row.uncovered, dash="–", limit=MAX_RANGES_PER_FILE, render=render)
        else:
            detail = "—"
        if row.note:
            detail = f"{detail} _{row.note}_" if row.uncovered else f"_{row.note}_"

        out.append(
            f"| {emoji} | {name} | {format_percentage(row.percentage)} "
            f"| {row.covered_lines} / {row.relevant_lines} | {detail} |"
        )

    out.append(
        f"| | **Total** | **{format_percentage(report.percentage)}** "
        f"| **{report.total_covered} / {report.total_relevant}** | |"
    )
    return out


def _markdown_skipped(report: Report) -> List[str]:
    if not report.skipped:
        return []
    summary = _skip_summary(report)
    out = ["<details>", f"<summary>{summary}</summary>", ""]
    out.append("| File | Reason |")
    out.append("| :-- | :-- |")
    for item in sorted(report.skipped, key=lambda entry: entry.path):
        out.append(f"| {inline_code(item.path)} | {item.reason} |")
    out.extend(["", "</details>", ""])
    return out


def _markdown_warnings(report: Report) -> List[str]:
    if not report.warnings:
        return []
    out = [""]
    for warning in report.warnings:
        out.append("> [!WARNING]")
        out.append(f"> {warning}")
        out.append("")
    return out


def _markdown_footer(report: Report) -> List[str]:
    sources = " · ".join(report.sources) if report.sources else "no coverage reports"
    return ["", f"<sub>Coverage reports: {sources} — incremental-coverage-check v{__version__}</sub>"]


# --------------------------------------------------------------------------- #
# Workflow annotations
# --------------------------------------------------------------------------- #


def render_annotations(report: Report, limit: int = MAX_ANNOTATIONS) -> List[str]:
    """``::warning`` commands drawn onto the diff, worst files first."""
    commands: List[str] = []
    truncated = 0

    for row in report.sorted_files():
        if row.status is Status.COVERED:
            continue
        for start, end in to_ranges(row.uncovered):
            if len(commands) >= limit:
                truncated += 1
                continue
            title = "Uncovered by tests"
            message = f"{row.note or 'Not covered by any test'}: " f"{'line' if start == end else 'lines'} {start}" + (
                "" if start == end else f"–{end}"
            )
            commands.append(
                f"::warning file={_escape_property(row.path)},line={start},endLine={end},"
                f"title={_escape_property(title)}::{_escape_data(message)}"
            )

    if truncated:
        commands.append(
            f"::notice::{truncated} further uncovered ranges were not annotated "
            f"(limit {limit}); the full list is in the job summary."
        )
    return commands


def _escape_data(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(text: str) -> str:
    return _escape_data(text).replace(":", "%3A").replace(",", "%2C")
