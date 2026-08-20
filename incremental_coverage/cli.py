"""Command line entry point and the glue between all the pieces."""

from __future__ import annotations

import argparse
import glob
import logging
import os
import sys

from typing import List, Optional, Sequence, Tuple

from . import __version__
from .analysis import analyse
from .coverage import CoverageParseError, load_reports
from .diff import parse_diff
from .gha import (
    CommentTarget,
    endgroup,
    error,
    GitHubApiError,
    group,
    in_actions,
    set_outputs,
    upsert_comment,
    warning,
    write_job_summary,
)
from .gitutil import (
    current_branch,
    default_branch,
    diff,
    diff_range,
    EMPTY_TREE,
    GitError,
    is_null_sha,
    is_repository,
    repo_root,
    resolve_ref,
    rev_parse,
)
from .ignore import DEFAULT_IGNORE_PATTERNS, FileFilter
from .paths import normalize
from .render import Linker, plural, render_annotations, render_markdown, render_terminal

logger = logging.getLogger("incremental_coverage")

EXIT_OK = 0
EXIT_COVERAGE_TOO_LOW = 1
EXIT_ERROR = 2

TRUE_VALUES = {"1", "true", "yes", "on"}
FALSE_VALUES = {"0", "false", "no", "off", ""}


class ConfigurationError(Exception):
    """The check was asked to do something impossible; the message explains what."""


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #


def env_default(name: str, fallback: Optional[str] = None) -> Optional[str]:
    """Read an ``ICC_*`` environment default, treating empty and ``none`` as unset.

    ``none`` is honoured because the previous action version used it as the literal
    default for unset inputs, and old workflows still pass it.
    """
    value = os.environ.get(name)
    if value is None:
        return fallback
    value = value.strip()
    if value == "" or value.lower() == "none":
        return fallback
    return value


def parse_bool(value: Optional[str], fallback: bool = False) -> bool:
    if value is None:
        return fallback
    text = value.strip().lower()
    if text in TRUE_VALUES:
        return True
    if text in FALSE_VALUES:
        return False
    raise ConfigurationError(f"expected a boolean, got {value!r}")


def split_list(*values: Optional[str]) -> List[str]:
    """Flatten newline- and comma-separated inputs into a clean list."""
    items: List[str] = []
    for value in values:
        if not value:
            continue
        for chunk in value.splitlines():
            for part in chunk.split(","):
                part = part.strip()
                if part and part.lower() != "none":
                    items.append(part)
    # Preserve order, drop duplicates.
    return list(dict.fromkeys(items))


def _parse_threshold(value: Optional[str], fallback: float = 70.0) -> float:
    if value is None:
        return fallback
    try:
        return float(value.strip().rstrip("%"))
    except ValueError:
        raise ConfigurationError(f"threshold must be a number, got {value!r}") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="incremental-coverage-check",
        description=(
            "Check that the lines a change adds or modifies are covered by tests. "
            "Reads coverage.py JSON, Cobertura XML, istanbul JSON, Clover XML and LCOV."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    reports = parser.add_argument_group("coverage reports")
    reports.add_argument(
        "-c",
        "--coverage",
        action="append",
        default=None,
        metavar="PATH",
        help="Coverage report to read. Repeatable; accepts comma/newline separated\n"
        "lists and glob patterns. The format is detected from the content.",
    )

    revisions = parser.add_argument_group("revisions")
    revisions.add_argument("-b", "--base", metavar="REF", help="Base revision to compare against.")
    revisions.add_argument("-H", "--head", metavar="REF", default=None, help="Revision under test (default HEAD).")
    revisions.add_argument("-w", "--repo-dir", metavar="DIR", default=None, help="Repository working directory.")
    revisions.add_argument(
        "--files",
        nargs="+",
        default=None,
        metavar="PATH",
        help="Grade only these files instead of everything in the diff.",
    )

    grading = parser.add_argument_group("grading")
    grading.add_argument("-t", "--threshold", type=float, default=None, help="Required percentage (default 70).")
    grading.add_argument(
        "--ignore",
        action="append",
        default=None,
        metavar="GLOB",
        help="Replace the default ignore patterns.",
    )
    grading.add_argument(
        "--extra-ignore",
        action="append",
        default=None,
        metavar="GLOB",
        help="Add to the default ignore patterns.",
    )
    grading.add_argument(
        "--source-extensions",
        action="append",
        default=None,
        metavar="EXT",
        help="Replace the list of extensions treated as source code.",
    )

    reporting = parser.add_argument_group("reporting")
    reporting.add_argument(
        "--comment",
        choices=("auto", "always", "never"),
        default=None,
        help="Post the report as a comment: auto, always or never. On a pull\n"
        "request the comment goes on the pull request; on a push it goes on the\n"
        "pushed commit. auto always comments on a pull request, and comments on\n"
        "a commit only when something is wrong.",
    )
    reporting.add_argument(
        "--commit-comments",
        default=None,
        metavar="BOOL",
        help="Comment on the pushed commit when there is no pull request.",
    )
    reporting.add_argument("--annotations", default=None, metavar="BOOL")
    reporting.add_argument("--job-summary", default=None, metavar="BOOL")
    reporting.add_argument("--markdown-out", default=None, metavar="PATH", help="Also write the report here.")
    reporting.add_argument("--github-token", default=None)
    reporting.add_argument("--repository", default=None, metavar="OWNER/REPO")
    reporting.add_argument("--pr-number", default=None, metavar="N")
    reporting.add_argument("-l", "--log-level", default=None, metavar="LEVEL")

    return parser


class Settings:
    """Resolved configuration: CLI arguments first, ``ICC_*`` environment second."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.repo_dir = os.path.abspath(
            args.repo_dir or env_default("ICC_WORKING_DIRECTORY") or env_default("GITHUB_WORKSPACE") or os.getcwd()
        )
        self.coverage_patterns = split_list(
            *(args.coverage or []),
            env_default("ICC_COVERAGE_FILES"),
            env_default("ICC_PY_COVERAGE_JSON"),
            env_default("ICC_CLOVER_COVERAGE_JSON"),
        )
        self.base = args.base or env_default("ICC_BASE_REF")
        self.head = args.head or env_default("ICC_HEAD_REF") or "HEAD"
        self.files = args.files
        self.threshold = (
            args.threshold if args.threshold is not None else _parse_threshold(env_default("ICC_THRESHOLD"))
        )
        self.ignore = split_list(*(args.ignore or []), env_default("ICC_IGNORE_PATTERNS")) or None
        self.extra_ignore = split_list(*(args.extra_ignore or []), env_default("ICC_EXTRA_IGNORE_PATTERNS"))
        self.source_extensions = (
            split_list(*(args.source_extensions or []), env_default("ICC_SOURCE_EXTENSIONS")) or None
        )
        self.threshold_fails_build = parse_bool(env_default("ICC_THRESHOLD_FAILS_BUILD"), True)
        self.commit_comments = parse_bool(args.commit_comments or env_default("ICC_COMMIT_COMMENTS"), True)
        self.comment = args.comment or env_default("ICC_COMMENT") or "auto"
        self.annotations = parse_bool(args.annotations or env_default("ICC_ANNOTATIONS"), True)
        self.job_summary = parse_bool(args.job_summary or env_default("ICC_JOB_SUMMARY"), True)
        self.markdown_out = args.markdown_out or env_default("ICC_MARKDOWN_OUT")
        self.github_token = args.github_token or env_default("ICC_GITHUB_TOKEN") or env_default("GITHUB_TOKEN")
        self.repository = args.repository or env_default("ICC_REPOSITORY") or env_default("GITHUB_REPOSITORY")
        self.pr_number = _optional_int(args.pr_number or env_default("ICC_PR_NUMBER"))
        self.log_level = (args.log_level or env_default("ICC_LOG_LEVEL") or "INFO").upper()

        if self.ignore is None:
            self.ignore = list(DEFAULT_IGNORE_PATTERNS)
        self.ignore = list(self.ignore) + list(self.extra_ignore)

    def validate(self) -> None:
        if not os.path.isdir(self.repo_dir):
            raise ConfigurationError(f"working directory does not exist: {self.repo_dir}")
        if not is_repository(self.repo_dir):
            raise ConfigurationError(f"{self.repo_dir} is not a git repository")
        if not self.base:
            raise ConfigurationError(
                "no base revision given. Pass --base, or set the `base-ref` input "
                "(for a pull request that is `github.base_ref`)."
            )
        if not self.coverage_patterns:
            raise ConfigurationError(
                "no coverage reports configured. Pass --coverage, or set the "
                "`coverage-files` input to the reports your test run produced."
            )
        if not 0 <= self.threshold <= 100:
            raise ConfigurationError(f"threshold must be between 0 and 100, got {self.threshold}")


def _optional_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Coverage report discovery
# --------------------------------------------------------------------------- #


def _is_glob(pattern: str) -> bool:
    return any(character in pattern for character in "*?[")


def resolve_coverage_files(patterns: Sequence[str], repo_dir: str) -> List[Tuple[str, Optional[str]]]:
    """Expand the configured patterns into concrete, existing report paths."""
    resolved: List[str] = []
    for pattern in patterns:
        candidate = pattern if os.path.isabs(pattern) else os.path.join(repo_dir, pattern)
        # Test the pattern the user wrote, not the joined path: a checkout living in a
        # directory called `build[1]` would otherwise turn every plain filename into a
        # glob that matches nothing.
        if _is_glob(pattern):
            matches = sorted(path for path in glob.glob(candidate, recursive=True) if os.path.isfile(path))
            if not matches:
                raise ConfigurationError(f"coverage pattern matched no files: {pattern}")
            resolved.extend(matches)
        elif os.path.isfile(candidate):
            resolved.append(candidate)
        else:
            raise ConfigurationError(
                f"coverage report not found: {pattern} (looked in {os.path.dirname(candidate) or repo_dir})"
            )
    return [(path, None) for path in dict.fromkeys(resolved)]


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )


def run(settings: Settings) -> int:
    settings.validate()

    root = repo_root(settings.repo_dir)
    head_sha = resolve_ref(root, settings.head, fetch=False)

    base_ref = _usable_base(root, settings.base)
    if base_ref == EMPTY_TREE:
        base_sha = fork_point = EMPTY_TREE
    else:
        base_sha = resolve_ref(root, base_ref)
        fork_point = diff_range(root, base_sha, head_sha)

    logger.info("comparing %s..%s", fork_point[:12], head_sha[:12])
    diffs = parse_diff(diff(root, fork_point, head_sha))
    logger.info("%s changed", plural(len(diffs), "file"))

    if settings.files:
        wanted = {normalize(path) for path in settings.files}
        diffs = {path: entry for path, entry in diffs.items() if normalize(path) in wanted}
        for missing in sorted(wanted - {normalize(path) for path in diffs}):
            logger.warning("--files named %s, which is not in the diff", missing)

    reports = resolve_coverage_files(settings.coverage_patterns, root)
    coverage = load_reports(reports, root)
    logger.info(
        "loaded coverage for %s from %s",
        plural(len(coverage), "file"),
        plural(len(coverage.sources), "report"),
    )

    file_filter = FileFilter(settings.source_extensions, settings.ignore)
    report = analyse(diffs, coverage, file_filter, settings.threshold)

    report.base_label = _ref_label(base_ref, base_sha)
    report.head_label = _head_label(root, settings.head, head_sha)
    report.base_sha = fork_point
    report.head_sha = head_sha
    report.sources = [
        f"{os.path.relpath(source.path, root)} ({source.format}, {plural(source.file_count, 'file')})"
        for source in coverage.sources
    ]

    _emit(report, settings, head_sha)

    if report.passed:
        return EXIT_OK
    if not settings.threshold_fails_build:
        logger.warning("threshold not met, but threshold-fails-build is false")
        return EXIT_OK
    return EXIT_COVERAGE_TOO_LOW


def _usable_base(root: str, base: str) -> str:
    """Replace git's all-zero "before" sha with something we can actually diff.

    A push that creates a branch reports ``000...0`` as the previous revision.  The
    useful comparison there is the default branch; failing that, the parent commit;
    failing that, the empty tree, which grades the whole branch as new code.
    """
    if not is_null_sha(base):
        return base

    fallback = default_branch(root)
    if fallback and rev_parse(root, f"origin/{fallback}"):
        logger.info("base is the null sha (new branch); comparing against origin/%s", fallback)
        return f"origin/{fallback}"
    if rev_parse(root, "HEAD~1"):
        logger.info("base is the null sha (new branch); comparing against HEAD~1")
        return "HEAD~1"
    logger.info("base is the null sha and there is no parent commit; grading the whole tree")
    return EMPTY_TREE


def _head_label(root: str, given: str, sha: str) -> str:
    """``HEAD`` is technically correct and tells the reader nothing; name the branch."""
    if given.strip().upper() == "HEAD":
        return os.environ.get("GITHUB_HEAD_REF") or current_branch(root) or sha[:7]
    return _ref_label(given, sha)


def _ref_label(given: str, sha: str) -> str:
    """Show branch names as given, but shorten raw shas so headings stay readable."""
    candidate = (given or "").strip()
    if candidate and sha.startswith(candidate.lower()) and len(candidate) >= 7:
        return candidate[:7]
    return candidate or sha[:7]


def _emit(report, settings: Settings, head_sha: str) -> None:
    print(render_terminal(report))

    linker = Linker.from_env(head_sha)
    markdown = render_markdown(report, linker)

    if settings.markdown_out:
        with open(settings.markdown_out, "w", encoding="utf-8") as handle:
            handle.write(markdown)
        logger.info("markdown report written to %s", settings.markdown_out)

    if settings.job_summary and write_job_summary(markdown):
        logger.info("job summary written")

    if settings.annotations and in_actions():
        commands = render_annotations(report)
        if commands:
            group(f"Uncovered lines ({plural(len(commands), 'annotation')})")
            for command in commands:
                print(command, flush=True)
            endgroup()

    _maybe_comment(report, settings, markdown, head_sha)

    set_outputs(
        {
            "coverage": f"{report.percentage:.2f}",
            "covered-lines": report.total_covered,
            "changed-lines": report.total_relevant,
            "uncovered-lines": report.total_uncovered,
            "files-checked": len(report.files),
            "files-skipped": len(report.skipped),
            "passed": "true" if report.passed else "false",
            "markdown": markdown,
        }
    )


def _comment_target(settings: Settings, head_sha: str) -> Optional[CommentTarget]:
    """Where the report should be posted, or ``None`` when it cannot be posted.

    A pull request is the obvious home for the report.  A push has no pull request,
    so the report goes on the pushed commit instead - the whole push is graded as one
    range, and GitHub shows a commit comment on the commit's own page.
    """
    if not (settings.github_token and settings.repository):
        return None
    if settings.pr_number:
        return CommentTarget.pull_request(settings.repository, settings.pr_number)
    if settings.commit_comments and head_sha:
        return CommentTarget.commit(settings.repository, head_sha)
    return None


def _maybe_comment(report, settings: Settings, markdown: str, head_sha: str) -> None:
    if settings.comment == "never":
        return

    target = _comment_target(settings, head_sha)
    if target is None:
        if settings.comment == "always":
            logger.warning("cannot comment: github-token and repository are required")
        return

    # `auto` weighs the report against what posting it costs the reader.  On a pull
    # request one comment serves the whole branch and is edited in place, so a passing
    # report is free to post and is the only place the numbers are visible without
    # opening the run.  A commit comment notifies on every push, so a passing report
    # there is noise and is withheld.
    if settings.comment == "auto" and target.renotifies and report.passed and not report.problem_files:
        logger.debug("nothing worth commenting about")
        return

    try:
        outcome = upsert_comment(settings.github_token, target, markdown)
    except GitHubApiError as api_error:
        # GitHub being unavailable is not a coverage problem; the verdict stands and
        # the report is still in the job summary.
        warning(f"could not comment on {target.description}: {api_error}")
        return
    logger.info("%s: %s", target.description, outcome)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        settings = Settings(args)
    except ConfigurationError as configuration_error:
        configure_logging("INFO")
        error(str(configuration_error))
        return EXIT_ERROR

    configure_logging(settings.log_level)

    try:
        return run(settings)
    except ConfigurationError as configuration_error:
        error(str(configuration_error))
        return EXIT_ERROR
    except CoverageParseError as parse_error:
        error(str(parse_error))
        return EXIT_ERROR
    except GitError as git_error:
        error(str(git_error))
        return EXIT_ERROR
    except KeyboardInterrupt:  # pragma: no cover
        return EXIT_ERROR
    except Exception as unexpected:  # pragma: no cover - a bug in this tool
        logger.exception("unexpected failure")
        error(f"incremental-coverage-check crashed: {unexpected}")
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
