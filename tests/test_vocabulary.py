""" "display" and "screen" name a device only in a display firmware's own code.

The core, the generic seams, the wire and the UI say "device" and "PlatformIO
type". A KNOMI is a screen; that fact lives in `helpers/knomi_serial.py` and
`discovery/knomi_serial/`, which are the one place allowed to know it. The last
time it leaked, the wire grew `displays`, `screens` and `display_flash`
alongside `targets[]`, and every consumer had two vocabularies to keep in step.

Words, not substrings: identifiers are split on `_` and on camelCase, so
`screen_id` and `roadrunnerDisplaySerial` are caught, and `displayed` and
`flipMenuIfOffscreen` are not.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCANNED = (ROOT / "src", ROOT / "ui" / "src")
SUFFIXES = {".py", ".ts", ".vue"}
EXEMPT = (
    "src/mcu_updater/helpers/knomi_serial.py",
    "src/mcu_updater/discovery/knomi_serial/",
)
BANNED = {"display", "displays", "screen", "screens"}
_CSS = re.compile(
    r"display:\s*(none|block|flex|grid|inline[-a-z]*|contents|table[-a-z]*)|@media\s+screen",
    re.IGNORECASE,
)
_WORD = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])")

#: (path, the line stripped, why the word is not a device there).
ALLOWED = {
    (
        "src/mcu_updater/providers/kconfig.py",
        "the choice, not the option), so the screen was three padlocked toggles and",
        "menuconfig screen",
    ),
    (
        "src/mcu_updater/providers/kconfig.py",
        "is enabled reads as indented under it, not as a separate screen.",
        "menuconfig screen",
    ),
    (
        "src/mcu_updater/providers/kconfig.py",
        "# menuconfig is its own screen, reached by `enterable`.",
        "menuconfig screen",
    ),
    (
        "src/mcu_updater/providers/kconfig.py",
        "# are their own screens; a choice is represented by its",
        "menuconfig screen",
    ),
    (
        "src/mcu_updater/providers/kconfig.py",
        '"""The current screen: where we are, and what is on it."""',
        "menuconfig screen",
    ),
    (
        "src/mcu_updater/providers/kconfig.py",
        "beats rendering an empty screen with no way out of it.",
        "menuconfig screen",
    ),
    (
        "src/mcu_updater/states.py",
        "#: understood. A chip, an icon and a screen reader all need the `label`; the",
        "screen reader",
    ),
    (
        "src/mcu_updater/__init__.py",
        "# 2: fields were *removed*. `screens[].mac`/`flashed_at`/`moved_from`/`moved_at`",
        "API history",
    ),
    (
        "src/mcu_updater/__init__.py",
        "# 3: `fw.display.list` and `fw.display.build` are gone (use `fw.device.list`",
        "API history",
    ),
    (
        "src/mcu_updater/sections.py",
        "``[mcu carto_v4]`` and ``[display knomi_toolchanger]`` were two spellings of one",
        "config history",
    ),
    (
        "src/mcu_updater/agent/events.py",
        "tab, a phone, KlipperScreen - which is enough to make the UI stutter on a Pi.",
        "product name",
    ),
    ("ui/src/api/kconfig.ts", "* current screen. */", "menuconfig screen"),
    (
        "ui/src/store/agent.ts",
        "// but Vi wants the finished job (and its log) to stay on screen rather",
        "on screen",
    ),
    (
        "ui/src/store/agent.ts",
        "// A menu-changing reply (open/enter/up/set/reset) replaces the screen",
        "menuconfig screen",
    ),
    (
        "ui/src/store/agent.ts",
        "* assignment can rewrite the screen - picking a different architecture",
        "menuconfig screen",
    ),
}


def _scanned_lines():
    for base in SCANNED:
        for path in sorted(base.rglob("*")):
            if path.suffix not in SUFFIXES:
                continue
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith(EXEMPT):
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                yield rel, number, line


def _names_a_device(line: str) -> bool:
    words = _WORD.findall(_CSS.sub("", line))
    return any(word.lower() in BANNED for word in words)


def test_the_core_and_the_ui_say_device():
    allowed = {(path, text) for path, text, _why in ALLOWED}
    found = [
        f"{rel}:{number}: {line.strip()}"
        for rel, number, line in _scanned_lines()
        if _names_a_device(line) and (rel, line.strip()) not in allowed
    ]
    assert found == [], (
        "say 'device' or 'PlatformIO type' - only a display firmware's own code "
        "may call its device a screen:\n" + "\n".join(found)
    )


def test_every_allowance_is_still_needed():
    """An allowance for a line that has gone would quietly permit its return."""
    present = {(rel, line.strip()) for rel, _n, line in _scanned_lines()}
    stale = [f"{path}: {text}" for path, text, _why in ALLOWED if (path, text) not in present]
    assert stale == []


@pytest.mark.parametrize(
    ("line", "names_one"),
    [
        ("for screen in payload:", True),
        ("screen_id = x", True),
        ("roadrunnerDisplaySerial(serial)", True),
        ("PlatformIO build failed for display 'knomi'", True),
        ("displayed = True", False),
        ("flipMenuIfOffscreen()", False),
        ("  display: none;", False),
        ("@media screen and (max-width: 600px)", False),
    ],
)
def test_the_guard_reads_words_not_substrings(line, names_one):
    assert _names_a_device(line) is names_one
