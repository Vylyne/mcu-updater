"""Where katapult's flashtool.py is looked for.

`flashtool_path` if set, else the declared katapult tree's scripts/. Never the
hardcoded ~/katapult, which is how a fork's flashtool went unused.
"""

from __future__ import annotations

import dataclasses
import os

import pytest

from mcu_updater.errors import ConfigCorruptError
from mcu_updater.flashers.flash import find_flashtool

from .conftest import BASE_FIRMWARES, write_main_config


def test_a_configured_flashtool_path_wins(paths, settings):
    write_main_config(paths, BASE_FIRMWARES)
    configured = dataclasses.replace(settings, flashtool_path="~/tools/flashtool.py")
    assert find_flashtool(paths, configured) == os.path.join(paths.home, "tools/flashtool.py")


def test_a_configured_path_needs_no_katapult_section(paths, settings):
    configured = dataclasses.replace(settings, flashtool_path="/opt/flashtool.py")
    assert find_flashtool(paths, configured) == "/opt/flashtool.py"


def test_the_declared_katapult_source_is_used(paths, settings):
    write_main_config(paths, BASE_FIRMWARES.replace("source: ~/katapult", "source: ~/katapult-fork"))
    assert find_flashtool(paths, settings) == os.path.join(
        paths.home, "katapult-fork", "scripts", "flashtool.py"
    )


def test_an_undeclared_katapult_is_refused_naming_the_section(paths, settings):
    write_main_config(paths, "[firmware klipper]\nsource: ~/klipper\nflashers: flashtool\n")
    with pytest.raises(ConfigCorruptError, match=r"\[firmware katapult\]"):
        find_flashtool(paths, settings)
