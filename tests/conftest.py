"""Shared fixtures.

Everything here leans on the single seam that makes this project testable:
``Paths.from_env`` honours ``MCU_UPDATER_*`` env vars, so a fake root in a
tmp_path stands in for a whole printer host - no mocks, no monkeypatching of
``expanduser``, no hardware, and it all runs on Windows.
"""

from __future__ import annotations

import json
import os
import pathlib
import re

import pytest

from mcu_updater.paths import Paths
from mcu_updater.settings import Settings

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Representative fixture data for tests that need a populated registry.
TEST_MCUS_CFG = REPO_ROOT / "tests" / "fixtures" / "registry.cfg"

#: The documented example remains a separate contract target.
EXAMPLE_MCUS_CFG = REPO_ROOT / "mcu-updater.cfg"


@pytest.fixture(autouse=True)
def _instant_fake_builds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop the dry-run log pacing.

    In production the fake build log replays at a realistic speed so the
    streaming UI is genuinely exercised. In tests that just makes the suite slow.
    """
    monkeypatch.setattr("mcu_updater.build.FAKE_BUILD_DELAY", 0.0)


@pytest.fixture(autouse=True)
def _bootsel_boards_apply_instantly(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fake BOOTSEL volume never goes away, so a copy onto one would sit out
    the whole apply wait. Real boards reset as the image lands; here they reset
    at once. Tests of the wait itself restore the real check."""
    monkeypatch.setattr(
        "mcu_updater.flashers.bootsel._volume_still_mounted", lambda mount: False
    )


@pytest.fixture
def fake_root(tmp_path: pathlib.Path) -> pathlib.Path:
    """A pretend ~ laid out the way a printer host is."""
    (tmp_path / "bus").mkdir()
    (tmp_path / "klipper" / "src").mkdir(parents=True)
    (tmp_path / "katapult" / "src").mkdir(parents=True)
    (tmp_path / "printer_data" / "comms").mkdir(parents=True)
    # Hand-edited config, and build artifacts, deliberately in separate trees.
    (tmp_path / "printer_data" / "config" / "mcu-updater").mkdir(parents=True)
    (tmp_path / "printer_data" / "mcu-updater").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def paths(fake_root: pathlib.Path) -> Paths:
    return Paths.from_env(
        env={
            "MCU_UPDATER_HOME": str(fake_root),
            "MCU_UPDATER_FAKE_BUS": str(fake_root / "bus"),
        }
    )


@pytest.fixture
def settings() -> Settings:
    """Defaults, but never touching a real service."""
    return Settings(service_backend="null", clean_before_build=False)


@pytest.fixture
def cmake_type(paths):
    """A configured CMake type with a built sidecar, for the record paths."""
    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write(
            "\n[firmware roadrunner]\n"
            "source: ~/roadrunner\n"
            "builder: cmake\n"
            "flashers: bootsel\n"
            "helper: roadrunner\n"
            "\n[type roadrunner]\n"
            "firmware: roadrunner\n"
            "cmake_target: roadrunner_v1_i2c_rgb\n"
            "chipset: rp2040\n"
            "serials: RR-1\n"
        )
    os.makedirs(paths.artifact_dir("roadrunner"), exist_ok=True)
    with open(paths.sidecar_file("roadrunner", "roadrunner"), "w", encoding="utf-8") as fh:
        json.dump(
            {
                "provider": "cmake",
                "sha": "built-subtree-sha",
                "version": "v1.2.3-4-gabcdef0",
                "dirty": False,
                "cmake_target": "roadrunner_v1_i2c_rgb",
                "bin_sha256": "built-uf2-sha256",
                "bin_size": 15,
                "bin_mtime": 123.0,
            },
            fh,
        )
    return "roadrunner"


@pytest.fixture
def live_registry_text() -> str:
    return TEST_MCUS_CFG.read_text(encoding="utf-8")


@pytest.fixture
def example_registry_text() -> str:
    return EXAMPLE_MCUS_CFG.read_text(encoding="utf-8")


def cmd_tokens(cmd_line: str) -> list[str]:
    """Split an echoed command line into whole tokens.

    Never substring-match for a flag in one of these. The line contains absolute
    paths, and a temp directory can easily contain the characters you are looking
    for - GitHub's runners use ``/tmp/pytest-of-runner/...``, in which
    ``pytest-of-runner`` contains ``-r``. That made an
    ``assert not any("-r" in c)`` fail against the *directory name* while passing
    on Windows, where the path is ``pytest-of-Vi``.

    Whitespace splitting is enough: flags never contain spaces, so even a quoted
    path with a space in it cannot produce a false match.
    """
    return cmd_line.split()


def save_registry(reg, paths: Paths) -> None:
    """Write a fixture registry to the fake install, as it stands.

    Production writes only through `Registry.mutate`, which is why `_save` is
    private. A fixture building its starting state has no lock to contend for
    and nothing to re-read, so it is the one place outside config.py's own
    tests that writes directly - and only through here.
    """
    reg._save(paths)


def save_settings(paths: Paths, settings) -> None:
    """Write a whole fixture `Settings` object to the fake install.

    Production writes only through `settings.mutate`, which is why
    `_write_settings` is private. The same exception as `save_registry`: a
    fixture building its starting state has no lock to contend for.
    """
    from mcu_updater import settings as settings_mod

    settings_mod._write_settings(paths.settings_file, settings)


def write_main_config(paths: Paths, text: str) -> None:
    """Write `text` as the whole mcu-updater.cfg of the fake install."""
    os.makedirs(os.path.dirname(paths.main_config), exist_ok=True)
    with open(paths.main_config, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def read_main_config(paths: Paths) -> str:
    with open(paths.main_config, encoding="utf-8") as fh:
        return fh.read()


#: What install.sh seeds on a host whose trees are at the conventional paths.
BASE_FIRMWARES = (
    "[firmware klipper]\nsource: ~/klipper\nflashers: flashtool\n\n"
    "[firmware katapult]\nsource: ~/katapult\nflashers: dfu_util, bootsel\n\n"
)


def with_base_firmwares(text: str) -> str:
    """`text` with klipper and katapult declared ahead of its first family or type.

    Only for text that declares neither: adding a second copy of a section is a
    duplicate-section refusal.
    """
    match = re.search(r"^\[(firmware|type)\s", text, re.MULTILINE)
    if match is None:
        separator = "\n" if text and not text.endswith("\n") else ""
        return text + separator + BASE_FIRMWARES
    return text[: match.start()] + BASE_FIRMWARES + text[match.start() :]


def seed_base_firmwares(paths: Paths) -> None:
    """Declare klipper and katapult in the fake install, the way install.sh does."""
    from mcu_updater import seed

    seed.seed_firmware_sections(paths, {})


def write_settings(paths: Paths, **values: object) -> None:
    """Set keys in the ``[updater]`` section of the shared config file.

    Settings and the registry live in one file, so this has to be a
    load-modify-write: a plain ``open(..., "w")`` deletes every ``[mcu ...]``
    section the fixture just wrote, and prepending a second ``[updater]`` block
    is refused as a duplicate section.
    """
    import os

    from mcu_updater.cfgdoc import CfgDocument

    text = ""
    if os.path.exists(paths.main_config):
        with open(paths.main_config, encoding="utf-8") as fh:
            text = fh.read()
    doc = CfgDocument(text)
    for key, value in values.items():
        doc.set("updater", key, value)
    os.makedirs(paths.config_dir, exist_ok=True)
    with open(paths.main_config, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(doc.render())


def make_device(bus_dir: pathlib.Path, fw: str, chipset: str, serial: str) -> pathlib.Path:
    """Create a fake /dev/serial/by-id entry.

    Real ones are symlinks; a plain file is indistinguishable for our purposes
    since we only ever listdir and stat them.
    """
    p = bus_dir / f"usb-{fw}_{chipset}_{serial}"
    p.write_text("", encoding="utf-8")
    return p


def mounted_bootsel_volume(
    tmp_path: pathlib.Path, name: str = "bootsel_root"
) -> tuple[pathlib.Path, pathlib.Path]:
    """A fake mounted RPI-RP2 volume under `tmp_path`, for `paths.bootsel_root`.

    Returns `(root, volume)` - `root` is what `bootsel_root` should be set to,
    `volume` is where a `.uf2` would be copied.
    """
    root = tmp_path / name
    vol = root / "RPI-RP2"
    vol.mkdir(parents=True)
    (vol / "INFO_UF2.TXT").write_text("", encoding="utf-8")
    return root, vol


def bootsel_device_node(root: pathlib.Path, serial: str = "E0C9125B0D9B") -> str:
    """A fake `by-id` entry under `root`, the shape `bootsel_devices()` globs for.

    Present whether or not `root` also has a mounted volume in it - the two are
    independent, which is the whole point of `bootsel_devices` existing
    alongside `bootsel_scan`.

    The real device node has a `:` in it (e.g. `...-0:0-part1`), but NTFS reads
    `:` as an alternate-data-stream separator and silently truncates the
    filename there, so this drops it - the glob under test only cares about the
    `usb-RPI_RP2_<serial>-...-part1` shape either way.
    """
    by_id = root / "by-id"
    by_id.mkdir(parents=True, exist_ok=True)
    node = by_id / f"usb-RPI_RP2_{serial}-0-0-part1"
    node.write_text("", encoding="utf-8")
    return str(node)


def display_objects(sections: dict, objects: dict = None) -> dict:
    """Fold printer.cfg terms into what the klippy module actually reports.

    There is one source for displays now: the printer objects. `port` is the
    module's merged value - the configured ``serial:`` where there is one, the
    discovered path otherwise - and ``device_id`` is whatever printer.cfg
    named. Tests still describe a display in config terms because that is how a
    person thinks about one.

    Merged case-insensitively: `configfile.settings` lowercases section names
    while the printer object keeps the case printer.cfg used.
    """
    merged = {name: dict(values) for name, values in (objects or {}).items()}
    lowered = {name.lower(): name for name in merged}
    for section, values in sections.items():
        true = lowered.get(section.lower())
        if true is None:
            true = section
            merged[true] = {}
            lowered[section.lower()] = true
        obj = merged[true]
        if values.get("serial") and obj.get("port") is None:
            obj["port"] = values["serial"]
        if values.get("device_id") and obj.get("device_id") is None:
            obj["device_id"] = values["device_id"]
    return merged


def serve_klipper(
    objects: dict,
    *,
    reachable: bool = True,
    print_state: str = "standby",
    idle_state: str = "Ready",
):
    """A call channel for a Klipper with the display module loaded."""
    queries: list = []

    def call(method, params, timeout):
        if not reachable:
            return {}
        if method == "printer.objects.list":
            return {"objects": ["configfile", "toolhead", *objects]}
        if method == "printer.info":
            return {"state": "ready", "state_message": "klippy is ready"}
        if method == "machine.system_info":
            return {"system_info": {"service_state": {"klipper": {"active_state": "active"}}}}
        if method == "printer.objects.query":
            queries.append(params)
            requested = (params or {}).get("objects") or {}
            status: dict = {n: v for n, v in objects.items() if n in requested}
            if "print_stats" in requested:
                status["print_stats"] = {"state": print_state}
            if "idle_timeout" in requested:
                status["idle_timeout"] = {"state": idle_state}
            return {"status": status}
        return {}

    call.queries = queries  # type: ignore[attr-defined]
    return call


def stage_uf2_only(paths: Paths, mcu_type: str, fw: str = "klipper", content: bytes = b"uf2 firmware") -> str:
    """Leave what an offset-less RP2040 Klipper build leaves: a `.uf2`, no
    `.bin`, and a sidecar listing only the `.uf2`."""
    import hashlib

    from mcu_updater.artifacts import KIND_UF2, sidecar_field

    os.makedirs(paths.artifact_dir(mcu_type), exist_ok=True)
    path = paths.uf2_file(mcu_type, fw)
    with open(path, "wb") as fh:
        fh.write(content)
    with open(paths.sidecar_file(mcu_type, fw), "w", encoding="utf-8") as fh:
        json.dump(
            {
                "bin_sha256": None,
                "artifacts": sidecar_field({KIND_UF2: hashlib.sha256(content).hexdigest()}),
            },
            fh,
        )
    return path
