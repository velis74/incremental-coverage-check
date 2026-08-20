"""GitHub Actions integration, using the REST API through the standard library.

Deliberately dependency-free: the action then needs no ``pip install`` step at all,
which removes both the slowest part of the run and a supply-chain surface that a
CI gate has no business carrying.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

from typing import Dict, Iterable, List, Optional, Tuple

from . import __version__, MARKER

logger = logging.getLogger(__name__)

API_VERSION = "2022-11-28"
USER_AGENT = f"incremental-coverage-check/{__version__}"


def in_actions() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


# --------------------------------------------------------------------------- #
# Workflow files: outputs, job summary, log grouping
# --------------------------------------------------------------------------- #


def _append_to(env_var: str, text: str) -> bool:
    path = os.environ.get(env_var)
    if not path:
        return False
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text)
            if not text.endswith("\n"):
                handle.write("\n")
        return True
    except OSError as error:  # pragma: no cover - runner filesystem problem
        logger.warning("could not write to %s (%s): %s", env_var, path, error)
        return False


def set_output(name: str, value: str) -> None:
    """Write a step output, using the heredoc form so multi-line values survive."""
    text = str(value)
    if "\n" in text:
        # The delimiter must not occur in the value, or the value could close its own
        # heredoc and everything after it would be read as further outputs.
        delimiter = "ICC_EOF"
        while delimiter in text:
            delimiter += "_"
        payload = f"{name}<<{delimiter}\n{text}\n{delimiter}"
    else:
        payload = f"{name}={text}"
    if not _append_to("GITHUB_OUTPUT", payload):
        logger.debug("output %s=%s (not running in GitHub Actions)", name, text)


def set_outputs(values: Dict[str, object]) -> None:
    for name, value in values.items():
        set_output(name, str(value))


def write_job_summary(markdown: str) -> bool:
    return _append_to("GITHUB_STEP_SUMMARY", markdown)


def emit(commands: Iterable[str]) -> None:
    for command in commands:
        print(command, flush=True)


def group(title: str) -> None:
    if in_actions():
        print(f"::group::{title}", flush=True)


def endgroup() -> None:
    if in_actions():
        print("::endgroup::", flush=True)


def notice(message: str) -> None:
    print(f"::notice::{message}" if in_actions() else f"NOTICE: {message}", flush=True)


def warning(message: str) -> None:
    print(f"::warning::{message}" if in_actions() else f"WARNING: {message}", flush=True)


def error(message: str) -> None:
    print(f"::error::{message}" if in_actions() else f"ERROR: {message}", flush=True)


# --------------------------------------------------------------------------- #
# REST API
# --------------------------------------------------------------------------- #


class GitHubApiError(RuntimeError):
    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


class GitHubApi:
    def __init__(self, token: str, api_url: Optional[str] = None) -> None:
        self.token = token
        self.api_url = (api_url or os.environ.get("GITHUB_API_URL") or "https://api.github.com").rstrip("/")

    def request(self, method: str, path: str, payload: Optional[dict] = None) -> Tuple[int, object]:
        url = path if path.startswith("http") else f"{self.api_url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", API_VERSION)
        request.add_header("User-Agent", USER_AGENT)
        if data is not None:
            request.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read().decode("utf-8") or "null"
                return response.status, json.loads(body)
        except urllib.error.HTTPError as http_error:
            detail = ""
            try:
                detail = json.loads(http_error.read().decode("utf-8")).get("message", "")
            except Exception:  # pragma: no cover - error body is best effort
                detail = http_error.reason or ""
            raise GitHubApiError(
                f"{method} {url} returned {http_error.code}: {detail}", http_error.code
            ) from http_error
        except urllib.error.URLError as url_error:  # pragma: no cover - network
            raise GitHubApiError(f"{method} {url} failed: {url_error.reason}") from url_error

    def paginate(self, path: str, limit: int = 10) -> List[dict]:
        separator = "&" if "?" in path else "?"
        items: List[dict] = []
        for page in range(1, limit + 1):
            _, body = self.request("GET", f"{path}{separator}per_page=100&page={page}")
            if not isinstance(body, list) or not body:
                break
            items.extend(body)
            if len(body) < 100:
                break
        return items

    def post(self, path: str, payload: dict) -> dict:
        _, result = self.request("POST", path, payload)
        return result if isinstance(result, dict) else {}

    def patch(self, path: str, payload: dict) -> dict:
        _, result = self.request("PATCH", path, payload)
        return result if isinstance(result, dict) else {}


class CommentTarget:
    """Where a report comment goes, and the three endpoints needed to keep it there.

    Pull request comments and commit comments are different resources with different
    URLs but identical behaviour from here: find our previous comment by its hidden
    marker, and edit it rather than piling up a new one on every run.
    """

    def __init__(
        self,
        description: str,
        listing: str,
        create: str,
        update: str,
        permission: str,
        renotifies: bool,
    ) -> None:
        self.description = description
        self.listing = listing
        self.create = create
        self.update = update
        self.permission = permission
        # Whether posting here reaches the author again on every run.  A pull request
        # keeps one comment across the whole branch, so the second run edits what the
        # first one wrote and GitHub stays quiet.  A commit comment cannot: each push
        # is a different sha, so there is no earlier comment to edit and the author is
        # notified anew.  `comment: auto` uses this to stay silent where silence costs
        # nothing and speak up where it does not.
        self.renotifies = renotifies

    @classmethod
    def pull_request(cls, repository: str, number: int) -> CommentTarget:
        return cls(
            description=f"pull request #{number}",
            listing=f"/repos/{repository}/issues/{number}/comments",
            create=f"/repos/{repository}/issues/{number}/comments",
            update=f"/repos/{repository}/issues/comments/{{id}}",
            permission="pull-requests: write",
            renotifies=False,
        )

    @classmethod
    def commit(cls, repository: str, sha: str) -> CommentTarget:
        return cls(
            description=f"commit {sha[:7]}",
            listing=f"/repos/{repository}/commits/{sha}/comments",
            create=f"/repos/{repository}/commits/{sha}/comments",
            update=f"/repos/{repository}/comments/{{id}}",
            permission="contents: write",
            renotifies=True,
        )


def upsert_comment(token: str, target: CommentTarget, body: str, marker: str = MARKER) -> str:
    """Post the report, replacing this action's previous comment if there is one.

    Returns a short description of what happened.  Never raises for permission
    problems: pull requests from forks get a read-only token, and a comment that
    cannot be posted must not turn a passing check into a failing one.
    """
    api = GitHubApi(token)
    try:
        existing = next(
            (comment for comment in api.paginate(target.listing) if marker in (comment.get("body") or "")),
            None,
        )
        if existing:
            api.patch(target.update.format(id=existing["id"]), {"body": body})
            return f"updated comment {existing['id']}"
        created = api.post(target.create, {"body": body})
        return f"created comment {created.get('id', '')}".strip()
    except GitHubApiError as api_error:
        if api_error.status in (401, 403, 404):
            warning(
                f"could not comment on {target.description} ({api_error}). "
                f"Grant `{target.permission}` to the job; note that pull requests from "
                f"forks get a read-only token and cannot be commented on at all."
            )
            return "skipped (insufficient permissions)"
        raise
