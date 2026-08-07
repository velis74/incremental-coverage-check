"""Parsers for the coverage report formats the supported toolchains emit.

Python side
    coverage.py JSON  ``coverage json``            (pytest-cov, pytest, Django)
    Cobertura XML     ``coverage xml``             (pytest --cov-report=xml)

JavaScript / TypeScript side
    istanbul JSON     ``coverage-final.json``      (vitest/jest ``json`` reporter)
    Clover XML        ``clover.xml``               (vitest default reporter, jest)
    LCOV              ``lcov.info``                (vitest/jest ``lcov`` reporter)

Every parser turns its input into the same :class:`CoverageData`, so the analysis
never learns which toolchain a file came from.
"""

from __future__ import annotations

import json
import os
import re

from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from xml.etree import ElementTree

from ..paths import make_relative, PathResolver
from .model import CoverageData, CoverageParseError, CoverageSource


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8-sig", errors="replace") as handle:
            return handle.read()
    except OSError as error:
        raise CoverageParseError(f"cannot read coverage report {path}: {error}") from error


def _load_json(path: str, text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError as error:
        raise CoverageParseError(f"{path} is not valid JSON: {error}") from error


def _load_xml(path: str, text: str) -> ElementTree.Element:
    try:
        return ElementTree.fromstring(text)
    except ElementTree.ParseError as error:
        raise CoverageParseError(f"{path} is not valid XML: {error}") from error


def _int(value: Optional[str], default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# coverage.py JSON
# --------------------------------------------------------------------------- #


def _detect_coverage_py(text: str, data: Any) -> bool:
    if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
        return False
    for entry in data["files"].values():
        return isinstance(entry, dict) and ("executed_lines" in entry or "missing_lines" in entry)
    # An empty run is still a valid coverage.py report.
    return "meta" in data or "totals" in data


def _parse_coverage_py(path: str, data: Any) -> CoverageData:
    coverage = CoverageData()
    for filename, entry in data["files"].items():
        if not isinstance(entry, dict):
            continue
        covered = {int(line) for line in entry.get("executed_lines", [])}
        missing = {int(line) for line in entry.get("missing_lines", [])}
        coverage.add(filename, covered, missing)
    return coverage


# --------------------------------------------------------------------------- #
# istanbul JSON (coverage-final.json)
# --------------------------------------------------------------------------- #


def _detect_istanbul(text: str, data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    for entry in data.values():
        return isinstance(entry, dict) and "statementMap" in entry
    return False


def _parse_istanbul(path: str, data: Any) -> CoverageData:
    coverage = CoverageData()
    for filename, entry in data.items():
        if not isinstance(entry, dict):
            continue
        covered: Set[int] = set()
        missed: Set[int] = set()

        # A line may hold several statements; one hit anywhere on it counts.
        _collect_istanbul(entry.get("statementMap"), entry.get("s"), covered, missed, key="start")
        _collect_istanbul(entry.get("fnMap"), entry.get("f"), covered, missed, key="decl")

        missed -= covered
        coverage.add(entry.get("path") or filename, covered, missed)
    return coverage


def _collect_istanbul(
    location_map: Any,
    hit_map: Any,
    covered: Set[int],
    missed: Set[int],
    key: str,
) -> None:
    if not isinstance(location_map, dict) or not isinstance(hit_map, dict):
        return
    for entry_id, location in location_map.items():
        if not isinstance(location, dict):
            continue
        start = location.get(key) or location.get("loc", {}).get("start") or location.get("start")
        if isinstance(start, dict) and "start" in start:
            start = start["start"]
        if not isinstance(start, dict):
            continue
        line = start.get("line")
        if not isinstance(line, int) or line < 1:
            continue
        if _int(hit_map.get(entry_id), 0) > 0:
            covered.add(line)
        else:
            missed.add(line)


# --------------------------------------------------------------------------- #
# Clover XML
# --------------------------------------------------------------------------- #


def _detect_clover(root: ElementTree.Element) -> bool:
    return root.tag == "coverage" and root.find("project") is not None


def _parse_clover(path: str, root: ElementTree.Element) -> CoverageData:
    coverage = CoverageData()
    for file_node in root.iter("file"):
        filename = file_node.get("path") or file_node.get("name")
        if not filename:
            continue
        covered: Set[int] = set()
        missed: Set[int] = set()
        for line_node in file_node.iter("line"):
            line = _int(line_node.get("num"), 0)
            if line < 1:
                continue
            if _int(line_node.get("count"), 0) > 0:
                covered.add(line)
            else:
                missed.add(line)
        missed -= covered
        coverage.add(filename, covered, missed)
    return coverage


# --------------------------------------------------------------------------- #
# Cobertura XML
# --------------------------------------------------------------------------- #


def _detect_cobertura(root: ElementTree.Element) -> bool:
    return root.tag == "coverage" and root.find("packages") is not None


def _parse_cobertura(path: str, root: ElementTree.Element) -> CoverageData:
    coverage = CoverageData()

    # <class filename=...> is relative to whichever <source> root the file was found
    # under, and the report does not say which one. Record them all as hints and let
    # the resolver decide against the checkout; registering the file under every root
    # here would fabricate coverage for files that were never measured.
    coverage.hints = [
        (node.text or "").strip()
        for node in root.iter("source")
        if node.text and (node.text or "").strip() not in (".", "")
    ]

    for class_node in root.iter("class"):
        filename = class_node.get("filename")
        if not filename:
            continue
        covered: Set[int] = set()
        missed: Set[int] = set()
        for line_node in class_node.iter("line"):
            line = _int(line_node.get("number"), 0)
            if line < 1:
                continue
            if _int(line_node.get("hits"), 0) > 0:
                covered.add(line)
            else:
                missed.add(line)
        missed -= covered
        coverage.add(filename, covered, missed)
    return coverage


# --------------------------------------------------------------------------- #
# LCOV
# --------------------------------------------------------------------------- #

_LCOV_RECORD = re.compile(r"^(SF|DA|end_of_record)\b[:]?(.*)$")


def _detect_lcov(text: str) -> bool:
    has_source = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("SF:"):
            has_source = True
        elif stripped == "end_of_record" and has_source:
            return True
    return False


def _parse_lcov(path: str, text: str) -> CoverageData:
    coverage = CoverageData()
    filename: Optional[str] = None
    covered: Set[int] = set()
    missed: Set[int] = set()

    def flush() -> None:
        nonlocal filename, covered, missed
        if filename:
            coverage.add(filename, covered, missed - covered)
        filename, covered, missed = None, set(), set()

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("SF:"):
            flush()
            filename = line[3:].strip()
        elif line.startswith("DA:"):
            parts = line[3:].split(",")
            if len(parts) < 2:
                continue
            number = _int(parts[0], 0)
            if number < 1:
                continue
            if _int(parts[1], 0) > 0:
                covered.add(number)
            else:
                missed.add(number)
        elif line == "end_of_record":
            flush()
    flush()
    return coverage


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #

FORMATS = ("coverage.py", "istanbul", "clover", "cobertura", "lcov")


def parse_report(path: str, fmt: Optional[str] = None) -> CoverageData:
    """Read one coverage report, auto-detecting its format unless *fmt* is given."""
    if not os.path.isfile(path):
        raise CoverageParseError(f"coverage report not found: {path}")

    text = _read_text(path)
    if not text.strip():
        raise CoverageParseError(f"coverage report is empty: {path}")

    if fmt is not None and fmt not in FORMATS:
        raise CoverageParseError(f"unknown coverage format {fmt!r}; expected one of {', '.join(FORMATS)}")

    stripped = text.lstrip()
    detected: Optional[str] = fmt
    data: Any = None
    root: Optional[ElementTree.Element] = None

    if stripped.startswith("{"):
        data = _load_json(path, text)
        if detected is None:
            if _detect_coverage_py(text, data):
                detected = "coverage.py"
            elif _detect_istanbul(text, data):
                detected = "istanbul"
    elif stripped.startswith("<"):
        root = _load_xml(path, text)
        if detected is None:
            if _detect_clover(root):
                detected = "clover"
            elif _detect_cobertura(root):
                detected = "cobertura"
    elif detected is None and _detect_lcov(text):
        detected = "lcov"

    if detected is None:
        raise CoverageParseError(
            f"cannot determine the format of {path}. Supported formats: "
            f"coverage.py JSON, Cobertura XML, istanbul JSON, Clover XML, LCOV."
        )

    if detected in ("coverage.py", "istanbul") and data is None:
        data = _load_json(path, text)
    if detected in ("clover", "cobertura") and root is None:
        root = _load_xml(path, text)

    parsers: Dict[str, Callable[[], CoverageData]] = {
        "coverage.py": lambda: _parse_coverage_py(path, data),
        "istanbul": lambda: _parse_istanbul(path, data),
        "clover": lambda: _parse_clover(path, root),  # type: ignore[arg-type]
        "cobertura": lambda: _parse_cobertura(path, root),  # type: ignore[arg-type]
        "lcov": lambda: _parse_lcov(path, text),
    }

    try:
        coverage = parsers[detected]()
    except CoverageParseError:
        raise
    except Exception as error:  # pragma: no cover - defensive, format-specific
        raise CoverageParseError(f"failed to parse {path} as {detected}: {error}") from error

    coverage.sources = [CoverageSource(path=path, format=detected, file_count=len(coverage.files))]
    return coverage


def load_reports(paths: List[Tuple[str, Optional[str]]], root: str) -> CoverageData:
    """Read several coverage reports, resolve their paths against *root*, and merge.

    Each report is resolved separately, because where a report sits is the strongest
    clue about what its relative paths mean: ``src/index.ts`` in
    ``packages/a/coverage/lcov.info`` is ``packages/a/src/index.ts``, and the
    identically named entry in package b is a different file entirely.
    """
    resolver = PathResolver(root)
    combined = CoverageData()
    for path, fmt in paths:
        report = parse_report(path, fmt)
        report_dir = make_relative(os.path.dirname(os.path.abspath(path)), root)
        combined.merge(report.resolved(resolver, report_dir))
    return combined
