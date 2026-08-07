"""Thin, explicit wrapper around the git commands the check needs.

Every call goes through :func:`run_git` so failures surface with the actual stderr
instead of being swallowed - diagnosing a broken checkout on a runner is otherwise
guesswork.
"""

from __future__ import annotations

import logging
import os
import subprocess

from typing import List, Optional, Sequence

logger = logging.getLogger(__name__)


class GitError(RuntimeError):
    """A git command failed; the message carries git's own stderr."""


def run_git(repo: str, *args: str, check: bool = True) -> str:
    command = ["git", "-C", repo, "-c", "core.quotepath=false", *args]
    logger.debug("running: %s", " ".join(command))
    process = subprocess.run(command, capture_output=True, text=True)
    if process.returncode != 0:
        message = (process.stderr or process.stdout or "").strip()
        if check:
            raise GitError(f"`{' '.join(args)}` failed: {message}")
        logger.debug("git command failed (ignored): %s", message)
        return ""
    return process.stdout


def is_repository(repo: str) -> bool:
    return run_git(repo, "rev-parse", "--git-dir", check=False).strip() != ""


def is_shallow(repo: str) -> bool:
    return run_git(repo, "rev-parse", "--is-shallow-repository", check=False).strip() == "true"


def rev_parse(repo: str, ref: str) -> Optional[str]:
    """Resolve *ref* to a commit sha, or ``None`` when it is unknown locally."""
    sha = run_git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False).strip()
    return sha or None


def short_sha(repo: str, ref: str) -> str:
    return run_git(repo, "rev-parse", "--short", ref, check=False).strip() or ref


def describe_ref(repo: str, ref: str) -> str:
    """A human label for a ref: the branch name when we have one, else a short sha."""
    name = run_git(repo, "rev-parse", "--abbrev-ref", ref, check=False).strip()
    if name and name != "HEAD":
        return name
    return short_sha(repo, ref)


def current_branch(repo: str) -> Optional[str]:
    name = run_git(repo, "rev-parse", "--abbrev-ref", "HEAD", check=False).strip()
    return name if name and name != "HEAD" else None


CANDIDATE_PREFIXES = ("", "origin/", "refs/remotes/origin/", "refs/heads/", "refs/tags/")


def resolve_ref(repo: str, ref: str, fetch: bool = True) -> str:
    """Resolve a user-supplied ref to a sha, fetching it if the clone lacks it.

    Shallow checkouts (``fetch-depth: 1``, the actions/checkout default) rarely have
    the base branch, so a targeted fetch here saves the caller from having to get
    their checkout configuration exactly right.
    """
    for prefix in CANDIDATE_PREFIXES:
        sha = rev_parse(repo, prefix + ref)
        if sha:
            return sha

    if fetch:
        logger.info("ref %r is not in the local clone, fetching it from origin", ref)
        run_git(repo, "fetch", "--no-tags", "--depth=2147483647", "origin", ref, check=False)
        for candidate in (f"refs/remotes/origin/{ref}", "FETCH_HEAD", ref):
            sha = rev_parse(repo, candidate)
            if sha:
                return sha

    raise GitError(f"cannot resolve base revision {ref!r}. Fetch it first, or check out with " f"`fetch-depth: 0`.")


NULL_SHA = "0" * 40
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def is_null_sha(ref: str) -> bool:
    """True for git's all-zero sha, which is what a push to a new branch reports."""
    candidate = (ref or "").strip()
    return len(candidate) >= 7 and set(candidate) == {"0"}


def default_branch(repo: str) -> Optional[str]:
    """The remote's default branch, when the clone knows about it."""
    head_ref = run_git(repo, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD", check=False).strip()
    if head_ref:
        return head_ref.rsplit("/", 1)[-1]
    for candidate in ("main", "master"):
        if rev_parse(repo, f"origin/{candidate}"):
            return candidate
    return None


def merge_base(repo: str, base: str, head: str) -> Optional[str]:
    sha = run_git(repo, "merge-base", base, head, check=False).strip()
    return sha or None


def diff_range(repo: str, base: str, head: str) -> str:
    """Diff *head* against the point it branched off *base*.

    Using the merge base (``base...head``) rather than the two tips means commits
    that landed on the base branch after this one forked are not reported as
    changes belonging to this pull request.
    """
    fork_point = merge_base(repo, base, head)
    if fork_point is None and is_shallow(repo):
        logger.info("no merge base in the shallow clone, deepening history")
        run_git(repo, "fetch", "--no-tags", "--unshallow", "origin", check=False)
        fork_point = merge_base(repo, base, head)
    if fork_point is None:
        logger.warning(
            "no common ancestor between %s and %s; comparing the two revisions directly",
            short_sha(repo, base),
            short_sha(repo, head),
        )
        fork_point = base
    return fork_point


def diff(repo: str, base: str, head: str, context: int = 0) -> str:
    """Unified diff between the merge base of *base*/*head* and *head*."""
    return run_git(
        repo,
        "diff",
        f"--unified={context}",
        "--no-color",
        "--no-ext-diff",
        "--find-renames",
        "--no-textconv",
        base,
        head,
    )


def repo_root(repo: str) -> str:
    root = run_git(repo, "rev-parse", "--show-toplevel", check=False).strip()
    return root or os.path.abspath(repo)


def head_sha(repo: str, ref: str = "HEAD") -> str:
    return rev_parse(repo, ref) or ref


def list_files(repo: str, refs: Sequence[str]) -> List[str]:  # pragma: no cover - helper
    output = run_git(repo, "diff", "--name-only", *refs, check=False)
    return [line for line in output.splitlines() if line.strip()]
