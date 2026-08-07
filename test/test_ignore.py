from unittest import TestCase

from incremental_coverage.ignore import FileFilter, split_patterns


class TestFileFilter(TestCase):
    def setUp(self) -> None:
        self.filter = FileFilter()

    def assertGraded(self, path: str) -> None:
        reason = self.filter.skip_reason(path)
        self.assertIsNone(reason, f"{path} should be graded, but was skipped: {reason}")

    def assertSkipped(self, path: str, kind: str = None) -> None:
        reason = self.filter.skip_reason(path)
        self.assertIsNotNone(reason, f"{path} should have been skipped")
        if kind:
            self.assertEqual(kind, reason.kind)

    def test_source_files_are_graded(self) -> None:
        for path in (
            "app/models.py",
            "vue/src/components/table.ts",
            "vue/src/components/table.vue",
            "scripts/build.mjs",
            "src/index.tsx",
        ):
            self.assertGraded(path)

    def test_non_source_files_are_skipped(self) -> None:
        for path in ("README.md", "package.json", "poetry.lock", "docs/guide.rst", "logo.png"):
            self.assertSkipped(path, "not-source")

    def test_substring_extensions_do_not_match(self) -> None:
        """The old suffix black-list skipped `gemini` because it ends in `ini`."""
        self.assertGraded("app/gemini.py")
        self.assertGraded("app/myjson.py")
        self.assertGraded("vue/src/latest.ts")

    def test_tests_are_skipped(self) -> None:
        for path in (
            "test_main.py",
            "app/tests.py",
            "app/models_test.py",
            "test/test_diff.py",
            "app/conftest.py",
            "vue/src/table.spec.ts",
            "vue/src/table.test.ts",
            "vue/__tests__/table.ts",
        ):
            self.assertSkipped(path, "ignored")

    def test_migrations_are_skipped_at_any_depth(self) -> None:
        for path in (
            "migrations/0001_initial.py",
            "app/migrations/0001_initial.py",
            "backend/apps/orders/migrations/0042_add_field.py",
        ):
            self.assertSkipped(path, "ignored")

    def test_framework_files_are_skipped(self) -> None:
        for path in ("manage.py", "setup.py", "project/settings.py", "project/settings/prod.py", "app/wsgi.py"):
            self.assertSkipped(path, "ignored")

    def test_dotfiles_and_workflows_are_skipped(self) -> None:
        self.assertSkipped(".eslintrc.js", "ignored")
        self.assertSkipped(".github/scripts/release.js", "ignored")

    def test_generated_typescript_is_skipped(self) -> None:
        self.assertSkipped("vue/src/shims.d.ts", "ignored")
        self.assertSkipped("static/bundle.min.js", "ignored")
        self.assertSkipped("vite.config.ts", "ignored")

    def test_custom_patterns_replace_the_defaults(self) -> None:
        custom = FileFilter(ignore_patterns=["vendor/*"])
        self.assertIsNone(custom.skip_reason("app/tests.py"))
        self.assertIsNotNone(custom.skip_reason("vendor/lib.py"))

    def test_custom_extensions_replace_the_defaults(self) -> None:
        custom = FileFilter(source_extensions=["py"])
        self.assertIsNone(custom.skip_reason("app/models.py"))
        self.assertEqual("not-source", custom.skip_reason("vue/src/table.ts").kind)

    def test_reason_names_the_pattern(self) -> None:
        self.assertIn("migrations", str(self.filter.skip_reason("app/migrations/0001.py")))


class TestSplitPatterns(TestCase):
    def test_splits_on_commas_and_newlines(self) -> None:
        self.assertEqual(["a/*", "b/*", "c/*"], split_patterns("a/*, b/*\nc/*"))

    def test_empty_input(self) -> None:
        self.assertIsNone(split_patterns(""))
        self.assertIsNone(split_patterns(None))
