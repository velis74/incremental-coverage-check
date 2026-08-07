"""The two ways the check gets started.

``action.yml`` runs ``python3 $GITHUB_ACTION_PATH/main.py``. If that shim breaks, the
action fails for everyone regardless of how well the rest of the code is tested, so it
is worth an actual test rather than a coverage exemption.
"""

import os
import runpy
import subprocess
import sys

from unittest import TestCase

from incremental_coverage import __version__

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestEntryPoints(TestCase):
    def run_entry_point(self, *args):
        return subprocess.run(
            [sys.executable, *args, "--version"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )

    def test_main_py_is_importable_and_exposes_main(self) -> None:
        module = runpy.run_path(os.path.join(ROOT, "main.py"), run_name="not_main")
        self.assertTrue(callable(module["main"]))

    def test_package_is_runnable_as_a_module(self) -> None:
        module = runpy.run_module("incremental_coverage.__main__", run_name="not_main")
        self.assertTrue(callable(module["main"]))

    def test_the_script_the_action_runs_starts_up(self) -> None:
        result = self.run_entry_point("main.py")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(__version__, result.stdout)

    def test_python_m_incremental_coverage_starts_up(self) -> None:
        result = self.run_entry_point("-m", "incremental_coverage")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(__version__, result.stdout)

    def test_main_py_works_from_another_directory(self) -> None:
        """The action invokes it by absolute path from the workspace, not from here."""
        result = subprocess.run(
            [sys.executable, os.path.join(ROOT, "main.py"), "--version"],
            cwd=os.path.dirname(ROOT),
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)
