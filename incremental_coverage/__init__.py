"""Incremental coverage check.

Fails a pull request when the lines it changes are not covered by tests.
"""

__version__ = "2.0.0"

MARKER = "<!-- incremental-coverage-check -->"
