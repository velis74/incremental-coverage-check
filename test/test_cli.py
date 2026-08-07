"""End-to-end tests: a real git repository, real coverage files, the real CLI."""

import json
import os

from unittest import TestCase

from incremental_coverage.cli import (
    _comment_target,
    build_parser,
    EXIT_COVERAGE_TOO_LOW,
    EXIT_ERROR,
    EXIT_OK,
    main,
    Settings,
)

from .helpers import TemporaryRepository

BASE_SOURCE = """def add(a, b):
    return a + b
"""

HEAD_SOURCE = """def add(a, b):
    return a + b


def subtract(a, b):
    return a - b


def multiply(a, b):
    return a * b
"""


def coverage_json(covered, missing, filename="calc.py"):
    return json.dumps(
        {
            "meta": {"version": "7.4.0"},
            "files": {filename: {"executed_lines": covered, "missing_lines": missing}},
        }
    )


class CliTestCase(TestCase):
    def setUp(self) -> None:
        self.repo = TemporaryRepository()
        self.addCleanup(self.repo.cleanup)
        self.repo.commit("base", {"calc.py": BASE_SOURCE})
        self.repo.git("checkout", "-b", "feature")
        self.repo.commit("feature", {"calc.py": HEAD_SOURCE})

    def write_coverage(self, covered, missing) -> str:
        return self.repo.write("coverage.json", coverage_json(covered, missing))

    def run_check(self, *extra: str) -> int:
        return main(
            [
                "--repo-dir",
                self.repo.path,
                "--base",
                "main",
                "--coverage",
                "coverage.json",
                "--comment",
                "never",
                "--log-level",
                "ERROR",
                *extra,
            ]
        )


class TestHappyPath(CliTestCase):
    def test_fully_covered_change_passes(self) -> None:
        self.write_coverage(covered=[1, 2, 5, 6, 9, 10], missing=[])
        self.assertEqual(EXIT_OK, self.run_check())

    def test_uncovered_change_fails(self) -> None:
        self.write_coverage(covered=[1, 2], missing=[5, 6, 9, 10])
        self.assertEqual(EXIT_COVERAGE_TOO_LOW, self.run_check())

    def test_threshold_is_configurable(self) -> None:
        self.write_coverage(covered=[5, 6], missing=[9, 10])
        self.assertEqual(EXIT_COVERAGE_TOO_LOW, self.run_check("--threshold", "70"))
        self.assertEqual(EXIT_OK, self.run_check("--threshold", "50"))

    def test_soft_mode_reports_without_failing(self) -> None:
        self.write_coverage(covered=[], missing=[5, 6, 9, 10])
        os.environ["ICC_THRESHOLD_FAILS_BUILD"] = "false"
        self.addCleanup(os.environ.pop, "ICC_THRESHOLD_FAILS_BUILD", None)
        self.assertEqual(EXIT_OK, self.run_check())

    def test_markdown_can_be_written_to_a_file(self) -> None:
        self.write_coverage(covered=[5, 6, 9, 10], missing=[])
        target = os.path.join(self.repo.path, "report.md")
        self.assertEqual(EXIT_OK, self.run_check("--markdown-out", target))
        with open(target, encoding="utf-8") as handle:
            self.assertIn("Incremental coverage", handle.read())


class TestBaseRevision(CliTestCase):
    def test_commits_added_to_the_base_after_forking_are_not_blamed_on_this_change(self) -> None:
        """The merge base is what matters, not the tip of the base branch."""
        self.repo.git("checkout", "main")
        self.repo.commit("unrelated work on main", {"other.py": "def other():\n    return 1\n"})
        self.repo.git("checkout", "feature")

        self.write_coverage(covered=[5, 6, 9, 10], missing=[])
        self.assertEqual(EXIT_OK, self.run_check())

    def test_unknown_base_is_an_explicit_error(self) -> None:
        self.write_coverage(covered=[], missing=[])
        exit_code = main(
            [
                "--repo-dir",
                self.repo.path,
                "--base",
                "no-such-branch",
                "--coverage",
                "coverage.json",
                "--comment",
                "never",
                "--log-level",
                "CRITICAL",
            ]
        )
        self.assertEqual(EXIT_ERROR, exit_code)


class TestConfigurationErrors(CliTestCase):
    def test_missing_coverage_file(self) -> None:
        self.assertEqual(EXIT_ERROR, self.run_check())

    def test_no_coverage_input_at_all(self) -> None:
        exit_code = main(["--repo-dir", self.repo.path, "--base", "main", "--log-level", "CRITICAL"])
        self.assertEqual(EXIT_ERROR, exit_code)

    def test_no_base_revision(self) -> None:
        self.write_coverage(covered=[], missing=[])
        exit_code = main(["--repo-dir", self.repo.path, "--coverage", "coverage.json", "--log-level", "CRITICAL"])
        self.assertEqual(EXIT_ERROR, exit_code)

    def test_not_a_repository(self) -> None:
        exit_code = main(["--repo-dir", "/", "--base", "main", "--coverage", "x.json", "--log-level", "CRITICAL"])
        self.assertEqual(EXIT_ERROR, exit_code)

    def test_unreadable_coverage_report(self) -> None:
        self.repo.write("coverage.json", "definitely not a coverage report")
        self.assertEqual(EXIT_ERROR, self.run_check())


class TestDualEnvironment(TestCase):
    """A Python backend and a TypeScript frontend graded in one run."""

    def setUp(self) -> None:
        self.repo = TemporaryRepository()
        self.addCleanup(self.repo.cleanup)
        self.repo.commit("base", {"backend/calc.py": BASE_SOURCE, "vue/src/table.ts": "export const a = 1;\n"})
        self.repo.git("checkout", "-b", "feature")
        self.repo.commit(
            "feature",
            {
                "backend/calc.py": HEAD_SOURCE,
                "vue/src/table.ts": "export const a = 1;\nexport const b = 2;\nexport const c = 3;\n",
            },
        )
        self.repo.write("backend/coverage.json", coverage_json([1, 2, 5, 6, 9, 10], [], "backend/calc.py"))
        self.repo.write(
            "vue/coverage/lcov.info",
            "SF:src/table.ts\nDA:1,1\nDA:2,1\nDA:3,0\nend_of_record\n",
        )

    def run_check(self, *extra: str) -> int:
        return main(
            [
                "--repo-dir",
                self.repo.path,
                "--base",
                "main",
                "--coverage",
                "backend/coverage.json,vue/coverage/lcov.info",
                "--comment",
                "never",
                "--log-level",
                "ERROR",
                *extra,
            ]
        )

    def test_both_toolchains_are_graded_together(self) -> None:
        # 4 of 4 changed python lines covered, 1 of 2 changed typescript lines -> 5/6 = 83.3%
        self.assertEqual(EXIT_OK, self.run_check("--threshold", "83"))
        self.assertEqual(EXIT_COVERAGE_TOO_LOW, self.run_check("--threshold", "90"))

    def test_glob_patterns_find_the_reports(self) -> None:
        self.assertEqual(
            EXIT_OK,
            main(
                [
                    "--repo-dir",
                    self.repo.path,
                    "--base",
                    "main",
                    "--coverage",
                    "**/coverage.json",
                    "--coverage",
                    "**/lcov.info",
                    "--comment",
                    "never",
                    "--threshold",
                    "83",
                    "--log-level",
                    "ERROR",
                ]
            ),
        )


class TestEnvironmentDefaults(CliTestCase):
    """The action passes every input through ICC_* rather than the command line."""

    def set_env(self, **values) -> None:
        for name, value in values.items():
            os.environ[name] = value
            self.addCleanup(os.environ.pop, name, None)

    def test_inputs_can_come_entirely_from_the_environment(self) -> None:
        self.write_coverage(covered=[5, 6, 9, 10], missing=[])
        self.set_env(
            ICC_WORKING_DIRECTORY=self.repo.path,
            ICC_BASE_REF="main",
            ICC_COVERAGE_FILES="coverage.json",
            ICC_THRESHOLD="80",
            ICC_COMMENT="never",
            ICC_LOG_LEVEL="ERROR",
        )
        self.assertEqual(EXIT_OK, main([]))

    def test_command_line_wins_over_the_environment(self) -> None:
        self.write_coverage(covered=[5], missing=[6, 9, 10])
        self.set_env(ICC_THRESHOLD="10")
        self.assertEqual(EXIT_COVERAGE_TOO_LOW, self.run_check("--threshold", "90"))

    def test_literal_none_is_treated_as_unset(self) -> None:
        """v1 of the action passed the string "none" for every unset input."""
        self.write_coverage(covered=[5, 6, 9, 10], missing=[])
        self.set_env(ICC_PY_COVERAGE_JSON="none", ICC_CLOVER_COVERAGE_JSON="none")
        self.assertEqual(EXIT_OK, self.run_check())

    def test_deprecated_input_names_still_work(self) -> None:
        self.write_coverage(covered=[5, 6, 9, 10], missing=[])
        self.set_env(
            ICC_WORKING_DIRECTORY=self.repo.path,
            ICC_BASE_REF="main",
            ICC_PY_COVERAGE_JSON="coverage.json",
            ICC_COMMENT="never",
            ICC_LOG_LEVEL="ERROR",
        )
        self.assertEqual(EXIT_OK, main([]))

    def test_a_nonsense_threshold_is_an_explicit_error(self) -> None:
        self.write_coverage(covered=[], missing=[])
        self.set_env(ICC_THRESHOLD="eighty")
        self.assertEqual(EXIT_ERROR, self.run_check())

    def test_a_threshold_out_of_range_is_an_explicit_error(self) -> None:
        self.write_coverage(covered=[], missing=[])
        self.assertEqual(EXIT_ERROR, self.run_check("--threshold", "150"))


class TestMonorepoFailsClosed(TestCase):
    """A same-named file in a sibling package must not lend it its coverage."""

    def setUp(self) -> None:
        self.repo = TemporaryRepository()
        self.addCleanup(self.repo.cleanup)
        self.repo.commit(
            "base",
            {
                "packages/a/src/index.ts": "export const a = 1;\n",
                "packages/b/src/index.ts": "export const b = 1;\n",
            },
        )
        self.repo.git("checkout", "-b", "feature")
        self.repo.commit(
            "feature",
            {"packages/b/src/index.ts": "export const b = 1;\nexport function untested() {\n  return 2;\n}\n"},
        )
        # Only package a ran its tests; its report names the file src/index.ts too.
        self.repo.write("packages/a/coverage/lcov.info", "SF:src/index.ts\nDA:1,4\nend_of_record\n")

    def test_untested_new_code_is_not_credited_with_a_sibling_report(self) -> None:
        exit_code = main(
            [
                "--repo-dir",
                self.repo.path,
                "--base",
                "main",
                "--coverage",
                "packages/*/coverage/lcov.info",
                "--comment",
                "never",
                "--log-level",
                "ERROR",
            ]
        )
        self.assertEqual(EXIT_COVERAGE_TOO_LOW, exit_code)


class TestCommentTargets(TestCase):
    """A pull request gets a pull request comment; a push gets a commit comment."""

    def make_settings(self, **overrides):
        parser = build_parser()
        args = parser.parse_args([])
        settings = Settings(args)
        settings.github_token = overrides.get("token", "t0ken")
        settings.repository = overrides.get("repository", "velis74/demo")
        settings.pr_number = overrides.get("pr_number")
        settings.commit_comments = overrides.get("commit_comments", True)
        return settings

    def test_pull_request_wins_when_there_is_one(self) -> None:
        target = _comment_target(self.make_settings(pr_number=42), "abc1234def")
        self.assertEqual("pull request #42", target.description)
        self.assertEqual("/repos/velis74/demo/issues/42/comments", target.create)
        self.assertEqual("pull-requests: write", target.permission)

    def test_a_push_comments_on_the_pushed_commit(self) -> None:
        target = _comment_target(self.make_settings(), "abc1234def")
        self.assertEqual("commit abc1234", target.description)
        self.assertEqual("/repos/velis74/demo/commits/abc1234def/comments", target.create)
        self.assertEqual("/repos/velis74/demo/comments/{id}", target.update)
        self.assertEqual("contents: write", target.permission)

    def test_commit_comments_can_be_switched_off(self) -> None:
        self.assertIsNone(_comment_target(self.make_settings(commit_comments=False), "abc1234def"))

    def test_no_token_means_no_comment(self) -> None:
        self.assertIsNone(_comment_target(self.make_settings(token=""), "abc1234def"))
