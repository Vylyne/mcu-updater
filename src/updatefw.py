#!/usr/bin/env python3
"""Back-compat entry point.

The implementation moved into the ``mcu_updater`` package next to this file.
This shim stays so that muscle memory, cron entries, and anything invoking
``~/mcu-updater/src/updatefw.py`` keep working unchanged.

``realpath`` rather than ``abspath`` so a symlink onto PATH also works. Nothing
ever installs an ``updatefw`` command - the project runs from source and is
never pip-installed, so the ``[project.scripts]`` entry point in pyproject.toml
never materialises - which makes a symlink the obvious thing to reach for, and
``abspath`` would have looked for the package beside the *link*.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from mcu_updater.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
