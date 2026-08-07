import os
import tempfile

from unittest import TestCase

from incremental_coverage.coverage import CoverageParseError, load_reports, parse_report

from .helpers import resource_path, write

COVERAGE_PY = """
{
  "meta": {"version": "7.4.0"},
  "files": {
    "app/models.py": {
      "executed_lines": [1, 2, 5],
      "missing_lines": [7, 8],
      "excluded_lines": []
    }
  }
}
"""

ISTANBUL = """
{
  "/home/runner/work/repo/repo/vue/src/table.ts": {
    "path": "/home/runner/work/repo/repo/vue/src/table.ts",
    "statementMap": {
      "0": {"start": {"line": 3, "column": 0}, "end": {"line": 3, "column": 10}},
      "1": {"start": {"line": 7, "column": 0}, "end": {"line": 7, "column": 10}},
      "2": {"start": {"line": 7, "column": 20}, "end": {"line": 7, "column": 30}}
    },
    "s": {"0": 2, "1": 0, "2": 5},
    "fnMap": {"0": {"decl": {"start": {"line": 12, "column": 1}}}},
    "f": {"0": 0}
  }
}
"""

CLOVER = """<?xml version="1.0" encoding="UTF-8"?>
<coverage generated="1700000000">
  <project timestamp="1700000000">
    <file name="/build/vue/src/form.ts" path="/build/vue/src/form.ts">
      <line num="2" count="1" type="stmt"/>
      <line num="4" count="0" type="stmt"/>
      <line num="9" count="0" type="cond"/>
    </file>
  </project>
</coverage>
"""

COBERTURA = """<?xml version="1.0" ?>
<coverage version="7.4.0">
  <sources><source>/home/runner/work/repo/repo</source></sources>
  <packages>
    <package name="app">
      <classes>
        <class filename="app/views.py" name="views.py">
          <lines>
            <line number="1" hits="1"/>
            <line number="4" hits="0"/>
          </lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>
"""

LCOV = """TN:
SF:vue/src/store.ts
DA:1,3
DA:5,0
DA:6,0
LF:3
LH:1
end_of_record
SF:vue/src/util.ts
DA:2,1
end_of_record
"""


class ParserTestCase(TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def parse(self, name: str, content: str):
        return parse_report(write(os.path.join(self.directory.name, name), content))


class TestCoveragePy(ParserTestCase):
    def test_parses_executed_and_missing(self) -> None:
        coverage = self.parse("coverage.json", COVERAGE_PY)
        entry = coverage.lookup("app/models.py")
        self.assertEqual({1, 2, 5}, entry.covered_lines)
        self.assertEqual({7, 8}, entry.uncovered_lines)
        self.assertEqual("coverage.py", coverage.sources[0].format)

    def test_real_world_fixture(self) -> None:
        coverage = parse_report(resource_path("py-coverage.json"))
        self.assertEqual("coverage.py", coverage.sources[0].format)
        self.assertTrue(coverage.lookup("diff_parser.py"))


class TestIstanbul(ParserTestCase):
    def test_uses_the_statement_map_not_the_statement_id(self) -> None:
        """Statement ids are not line numbers; only statementMap knows the lines."""
        coverage = self.parse("coverage-final.json", ISTANBUL)
        entry = coverage.lookup("/home/runner/work/repo/repo/vue/src/table.ts")
        self.assertEqual({3, 7}, entry.covered_lines)
        # Line 7 holds a hit and a miss - a hit anywhere on the line wins.
        self.assertEqual({12}, entry.uncovered_lines)

    def test_real_world_fixture(self) -> None:
        coverage = parse_report(resource_path("coverage.json"))
        self.assertEqual("istanbul", coverage.sources[0].format)
        self.assertGreater(len(coverage), 50)


class TestClover(ParserTestCase):
    def test_parses_line_counts(self) -> None:
        coverage = self.parse("clover.xml", CLOVER)
        entry = coverage.lookup("/build/vue/src/form.ts")
        self.assertEqual({2}, entry.covered_lines)
        self.assertEqual({4, 9}, entry.uncovered_lines)
        self.assertEqual("clover", coverage.sources[0].format)


class TestCobertura(ParserTestCase):
    def test_parses_hits(self) -> None:
        coverage = self.parse("coverage.xml", COBERTURA)
        entry = coverage.lookup("app/views.py")
        self.assertEqual({1}, entry.covered_lines)
        self.assertEqual({4}, entry.uncovered_lines)
        self.assertEqual("cobertura", coverage.sources[0].format)


class TestLcov(ParserTestCase):
    def test_parses_records(self) -> None:
        coverage = self.parse("lcov.info", LCOV)
        store = coverage.lookup("vue/src/store.ts")
        self.assertEqual({1}, store.covered_lines)
        self.assertEqual({5, 6}, store.uncovered_lines)
        self.assertEqual({2}, coverage.lookup("vue/src/util.ts").covered_lines)
        self.assertEqual("lcov", coverage.sources[0].format)


class TestDispatch(ParserTestCase):
    def test_unknown_format_is_an_explicit_error(self) -> None:
        with self.assertRaises(CoverageParseError) as caught:
            self.parse("mystery.txt", "this is not a coverage report\n")
        self.assertIn("cannot determine the format", str(caught.exception))

    def test_missing_file_is_an_explicit_error(self) -> None:
        with self.assertRaises(CoverageParseError):
            parse_report(os.path.join(self.directory.name, "absent.json"))

    def test_empty_file_is_an_explicit_error(self) -> None:
        with self.assertRaises(CoverageParseError):
            self.parse("empty.json", "   \n")

    def test_broken_json_names_the_file(self) -> None:
        with self.assertRaises(CoverageParseError) as caught:
            self.parse("broken.json", '{"files": ')
        self.assertIn("broken.json", str(caught.exception))


class TestMerging(ParserTestCase):
    def test_python_and_typescript_reports_merge(self) -> None:
        root = self.directory.name
        paths = [
            (write(os.path.join(root, "coverage.json"), COVERAGE_PY), None),
            (write(os.path.join(root, "vue/coverage/lcov.info"), LCOV), None),
        ]
        coverage = load_reports(paths, root)
        self.assertEqual(2, len(coverage.sources))
        self.assertTrue(coverage.lookup("app/models.py"))
        self.assertTrue(coverage.lookup("vue/src/store.ts"))

    def test_absolute_paths_are_rebased_onto_the_repository(self) -> None:
        root = self.directory.name
        istanbul = ISTANBUL.replace("/home/runner/work/repo/repo", root)
        paths = [(write(os.path.join(root, "coverage-final.json"), istanbul), None)]
        coverage = load_reports(paths, root)
        self.assertIn("vue/src/table.ts", coverage.files)

    def test_a_hit_in_one_report_beats_a_miss_in_another(self) -> None:
        root = self.directory.name
        other = COVERAGE_PY.replace('"executed_lines": [1, 2, 5]', '"executed_lines": [7]')
        paths = [
            (write(os.path.join(root, "a.json"), COVERAGE_PY), None),
            (write(os.path.join(root, "b.json"), other), None),
        ]
        coverage = load_reports(paths, root)
        entry = coverage.lookup("app/models.py")
        self.assertIn(7, entry.covered_lines)
        self.assertNotIn(7, entry.uncovered_lines)


COBERTURA_TWO_SOURCES = """<?xml version="1.0" ?>
<coverage version="7.4.0">
  <sources><source>{root}/api</source><source>{root}/worker</source></sources>
  <packages>
    <package name="api">
      <classes>
        <class filename="handlers.py" name="handlers.py">
          <lines><line number="1" hits="1"/><line number="2" hits="1"/></lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>
"""


class TestPathResolution(ParserTestCase):
    """Where a report sits decides what its relative paths mean."""

    def test_a_report_in_a_subdirectory_anchors_its_own_paths(self) -> None:
        root = self.directory.name
        write(os.path.join(root, "vue/src/table.ts"), "export const a = 1;\n")
        report = write(os.path.join(root, "vue/coverage/lcov.info"), "SF:src/table.ts\nDA:1,1\nend_of_record\n")
        coverage = load_reports([(report, None)], root)
        self.assertIn("vue/src/table.ts", coverage.files)

    def test_two_packages_reporting_the_same_relative_path_stay_apart(self) -> None:
        """Both packages call it src/index.ts; crediting one to the other fails open."""
        root = self.directory.name
        write(os.path.join(root, "packages/a/src/index.ts"), "export const a = 1;\n")
        write(os.path.join(root, "packages/b/src/index.ts"), "export const b = 1;\n")
        reports = [
            (
                write(os.path.join(root, "packages/a/coverage/lcov.info"), "SF:src/index.ts\nDA:1,5\nend_of_record\n"),
                None,
            ),
            (
                write(os.path.join(root, "packages/b/coverage/lcov.info"), "SF:src/index.ts\nDA:1,0\nend_of_record\n"),
                None,
            ),
        ]
        coverage = load_reports(reports, root)
        self.assertEqual({1}, coverage.lookup("packages/a/src/index.ts").covered_lines)
        self.assertEqual({1}, coverage.lookup("packages/b/src/index.ts").uncovered_lines)

    def test_cobertura_does_not_fabricate_coverage_under_every_source_root(self) -> None:
        """<class filename> is relative to one <source>; the report does not say which."""
        root = self.directory.name
        write(os.path.join(root, "api/handlers.py"), "def a():\n    return 1\n")
        write(os.path.join(root, "worker/handlers.py"), "def b():\n    return 2\n")
        report = write(os.path.join(root, "coverage.xml"), COBERTURA_TWO_SOURCES.format(root=root))
        coverage = load_reports([(report, None)], root)

        self.assertEqual({}, coverage.files)
        self.assertEqual(1, len(coverage.unresolved))
        reported, candidates = coverage.unresolved[0]
        self.assertEqual("handlers.py", reported)
        self.assertEqual({"api/handlers.py", "worker/handlers.py"}, set(candidates))

    def test_cobertura_with_a_single_source_root_resolves_cleanly(self) -> None:
        root = self.directory.name
        write(os.path.join(root, "api/handlers.py"), "def a():\n    return 1\n")
        xml = COBERTURA_TWO_SOURCES.format(root=root).replace(f"<source>{root}/worker</source>", "")
        report = write(os.path.join(root, "coverage.xml"), xml)
        coverage = load_reports([(report, None)], root)
        self.assertEqual({1, 2}, coverage.lookup("api/handlers.py").covered_lines)

    def test_a_report_from_a_foreign_absolute_path_still_matches(self) -> None:
        root = self.directory.name
        write(os.path.join(root, "vue/src/table.ts"), "export const a = 1;\n")
        report = write(
            os.path.join(root, "coverage-final.json"),
            ISTANBUL.replace("/home/runner/work/repo/repo/vue/src/table.ts", "/build/app/vue/src/table.ts"),
        )
        coverage = load_reports([(report, None)], root)
        self.assertIn("vue/src/table.ts", coverage.files)

    def test_files_missing_from_the_checkout_are_kept_for_suffix_matching(self) -> None:
        root = self.directory.name
        report = write(os.path.join(root, "coverage.json"), COVERAGE_PY)
        coverage = load_reports([(report, None)], root)
        self.assertIn("app/models.py", coverage.files)
