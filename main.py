#!/usr/bin/env python3
"""Entry point used by ``action.yml`` and by anyone running the check locally.

Adding the repository root to ``sys.path`` keeps ``python3 /path/to/main.py`` working
no matter which directory it is invoked from, which is what the composite action does.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from incremental_coverage.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
