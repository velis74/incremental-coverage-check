"""The GitHub Actions integration, driven against a fake HTTP layer."""

import io
import json
import os
import tempfile
import urllib.error

from unittest import TestCase
from unittest.mock import patch

from incremental_coverage import MARKER
from incremental_coverage.gha import (
    CommentTarget,
    GitHubApiError,
    set_output,
    set_outputs,
    upsert_comment,
    write_job_summary,
)


class FakeResponse:
    def __init__(self, payload, status=200):
        self._body = json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class FakeHttp:
    """Replaces urlopen; records every request and replays queued responses."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout=None):
        body = json.loads(request.data.decode("utf-8")) if request.data else None
        self.requests.append((request.method, request.full_url, body))
        response = self.responses.pop(0) if self.responses else []
        if isinstance(response, Exception):
            raise response
        return FakeResponse(response)

    @property
    def calls(self):
        return [(method, url.split("api.github.com")[-1]) for method, url, _ in self.requests]

    def body(self, index):
        return self.requests[index][2]


def http_error(code, message="nope"):
    return urllib.error.HTTPError(
        "https://api.github.com/x",
        code,
        message,
        {},
        io.BytesIO(json.dumps({"message": message}).encode("utf-8")),
    )


class GhaTestCase(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def env_file(self, variable):
        path = os.path.join(self.directory.name, variable.lower())
        os.environ[variable] = path
        self.addCleanup(os.environ.pop, variable, None)
        return path

    def contents(self, path):
        with open(path, encoding="utf-8") as handle:
            return handle.read()


class TestOutputs(GhaTestCase):
    def test_single_line_output(self) -> None:
        path = self.env_file("GITHUB_OUTPUT")
        set_output("coverage", "84.50")
        self.assertEqual("coverage=84.50\n", self.contents(path))

    def test_multi_line_output_uses_a_heredoc(self) -> None:
        path = self.env_file("GITHUB_OUTPUT")
        set_output("markdown", "line one\nline two")
        self.assertEqual("markdown<<ICC_EOF\nline one\nline two\nICC_EOF\n", self.contents(path))

    def test_a_value_containing_the_delimiter_gets_a_longer_one(self) -> None:
        """Otherwise the value could close its own heredoc and forge further outputs."""
        path = self.env_file("GITHUB_OUTPUT")
        set_output("markdown", "before\nICC_EOF\npassed=true\nafter")
        written = self.contents(path)
        self.assertTrue(written.startswith("markdown<<ICC_EOF_\n"))
        self.assertTrue(written.rstrip().endswith("\nICC_EOF_"))

    def test_several_outputs(self) -> None:
        path = self.env_file("GITHUB_OUTPUT")
        set_outputs({"passed": "true", "files-checked": 3})
        self.assertEqual("passed=true\nfiles-checked=3\n", self.contents(path))

    def test_outputs_outside_actions_are_silently_skipped(self) -> None:
        os.environ.pop("GITHUB_OUTPUT", None)
        set_output("coverage", "84.50")  # must not raise

    def test_job_summary(self) -> None:
        path = self.env_file("GITHUB_STEP_SUMMARY")
        self.assertTrue(write_job_summary("## Report"))
        self.assertEqual("## Report\n", self.contents(path))

    def test_job_summary_outside_actions(self) -> None:
        os.environ.pop("GITHUB_STEP_SUMMARY", None)
        self.assertFalse(write_job_summary("## Report"))


class TestCommentTarget(TestCase):
    def test_pull_request_endpoints(self) -> None:
        target = CommentTarget.pull_request("velis74/demo", 42)
        self.assertEqual("/repos/velis74/demo/issues/42/comments", target.listing)
        self.assertEqual("/repos/velis74/demo/issues/comments/{id}", target.update)

    def test_commit_endpoints(self) -> None:
        target = CommentTarget.commit("velis74/demo", "abc1234def")
        self.assertEqual("/repos/velis74/demo/commits/abc1234def/comments", target.listing)
        self.assertEqual("/repos/velis74/demo/comments/{id}", target.update)


class TestUpsertComment(TestCase):
    def setUp(self):
        self.target = CommentTarget.pull_request("velis74/demo", 42)

    def run_upsert(self, http, body="new report"):
        with patch("urllib.request.urlopen", http):
            return upsert_comment("t0ken", self.target, body)

    def test_creates_a_comment_when_there_is_none(self) -> None:
        http = FakeHttp([{"id": 1, "body": "unrelated"}], {"id": 7})
        outcome = self.run_upsert(http)
        self.assertEqual("created comment 7", outcome)
        self.assertEqual(("POST", "/repos/velis74/demo/issues/42/comments"), http.calls[-1])
        self.assertEqual({"body": "new report"}, http.body(-1))

    def test_updates_its_own_previous_comment(self) -> None:
        http = FakeHttp([{"id": 5, "body": f"{MARKER}\nold report"}], {"id": 5})
        outcome = self.run_upsert(http)
        self.assertEqual("updated comment 5", outcome)
        self.assertEqual(("PATCH", "/repos/velis74/demo/issues/comments/5"), http.calls[-1])

    def test_other_peoples_comments_are_left_alone(self) -> None:
        http = FakeHttp([{"id": 1, "body": "looks good to me"}], {"id": 9})
        self.assertEqual("created comment 9", self.run_upsert(http))
        self.assertEqual("POST", http.calls[-1][0])

    def test_a_commit_comment_uses_the_commit_endpoints(self) -> None:
        self.target = CommentTarget.commit("velis74/demo", "abc1234def")
        http = FakeHttp([{"id": 3, "body": f"{MARKER}\nold"}], {"id": 3})
        self.assertEqual("updated comment 3", self.run_upsert(http))
        self.assertEqual(
            [
                ("GET", "/repos/velis74/demo/commits/abc1234def/comments?per_page=100&page=1"),
                ("PATCH", "/repos/velis74/demo/comments/3"),
            ],
            http.calls,
        )

    def test_pagination(self) -> None:
        first_page = [{"id": index, "body": "chatter"} for index in range(100)]
        second_page = [{"id": 100, "body": f"{MARKER} old"}]
        http = FakeHttp(first_page, second_page, {"id": 100})
        self.assertEqual("updated comment 100", self.run_upsert(http))
        self.assertIn("page=2", http.calls[1][1])

    def test_a_read_only_token_is_a_warning_not_a_failure(self) -> None:
        """Fork pull requests cannot comment; that must not fail a passing check."""
        for status in (401, 403, 404):
            http = FakeHttp(http_error(status, "Resource not accessible by integration"))
            self.assertEqual("skipped (insufficient permissions)", self.run_upsert(http))

    def test_other_api_failures_are_raised(self) -> None:
        http = FakeHttp(http_error(500, "Server Error"))
        with self.assertRaises(GitHubApiError) as caught:
            self.run_upsert(http)
        self.assertEqual(500, caught.exception.status)
        self.assertIn("Server Error", str(caught.exception))

    def test_the_request_is_authenticated_and_versioned(self) -> None:
        http = FakeHttp([], {"id": 1})
        with patch("urllib.request.urlopen", http):
            upsert_comment("t0ken", self.target, "body")
        request = http.requests[0]
        self.assertEqual("GET", request[0])
        self.assertTrue(request[1].startswith("https://api.github.com/"))
