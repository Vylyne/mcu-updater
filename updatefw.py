#!/usr/bin/env python3
"""Root-level convenience entry point.

``src/updatefw.py`` is the documented one - cron entries and PATH symlinks
target it and it must not move. This is a second, purely additive shim so the
CLI can also be run as ``./updatefw.py`` from a repo checkout without a `cd`.

``realpath`` rather than ``abspath`` so a symlink onto PATH also works.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "src"))

from mcu_updater.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
