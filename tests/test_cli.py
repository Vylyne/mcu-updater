"""The CLI, walking the same seams the agent does.

`cli.py` referenced neither seam. `build_fw_cmd` called `build()` directly and
`flash_fw_cmd`/`update_all` called `flash_katapult` directly, so
`mcu-updater update-all` meant "every tracked MCU" and left every PlatformIO
device on whatever it happened to be running - with nothing anywhere saying so.
That is the bug `build_all` had before the Provider seam, one layer down, and
exactly what the seam docstring names: every caller knowing only one
implementation quietly served only one.

These tests are about *what the CLI hands the seams*, not about writing
firmware. The batch and the flashers have their own tests; what was missing was
anything at all covering the layer that chooses.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import types

import pytest

from mcu_updater import cli, flashers, helpers, tracking, typelist
from mcu_updater.config import Registry
from mcu_updater.discovery import canbus
from mcu_updater.errors import (
    BusyError,
    NoFlasherError,
    SerialTrackedElsewhereError,
    UnprovisionedSerialError,
    UpdaterError,
)
from mcu_updater.lock import ExclusiveLock
from mcu_updater.settings import Settings

from .conftest import make_device, save_registry, seed_base_firmwares

ENV = "knomi_toolchanger"


@pytest.fixture
def c(paths, fake_root, monkeypatch):
    """A CLI context pinned to the test tree, with no real services."""
    seed_base_firmwares(paths)
    context = cli.Context(
        paths=paths, settings=Settings(service_backend="null", clean_before_build=False)
    )
    monkeypatch.setattr(cli, "_ctx", context)

    reg = Registry.load(paths)
    reg.add_type("board", "stm32f072xb")
    reg.add_serial("board", "AAAA-if00")
    save_registry(reg, paths)

    # Saved menuconfig answers, or `select()` correctly skips it as unbuildable
    # and the type never reaches the build half at all.
    import os

    os.makedirs(paths.type_dir("board"), exist_ok=True)
    with open(paths.config_file("board", "klipper"), "w", encoding="utf-8") as fh:
        fh.write("CONFIG_MACH_STM32=y\n")
    return context


@pytest.fixture
def pio_type(c, fake_root):
    """A PlatformIO type with a source tree, declared the new way."""
    tree = fake_root / "knomi-serial"
    (tree / ".pio" / "build" / ENV).mkdir(parents=True)
    (tree / "platformio.ini").write_text(f"[env:{ENV}]\n", encoding="utf-8")
    with open(c.paths.main_config, "a", encoding="utf-8") as fh:
        fh.write(
            f"\n[firmware knomi_serial]\nsource: {tree}\nbuilder: platformio\n"
            "helper: knomi_serial\nflashers: esptool\n\n"
            f"[type {ENV}]\nfirmware: knomi_serial\nplatformio_env: {ENV}\nservice:\n"
        )
    return tree


@pytest.fixture
def captured(monkeypatch):
    """Every batch the CLI submits, without writing anything."""
    calls: list[list] = []

    def fake(bench, targets, ctx, **kwargs):
        calls.append(list(targets))
        return {"flashed": [t.to_json() for t in targets], "failures": []}

    monkeypatch.setattr(flashers, "write_all", fake)
    return calls


def test_status_can_flag_prints_interfaces_sightings_and_partial_failures(c, capsys, monkeypatch):
    result = canbus.CanScanResult(
        interfaces=[canbus.CanInterface("can0", None), canbus.CanInterface("can1", None)],
        sightings=[canbus.CanSighting("abc123", "Klipper", "klipper", "can1")],
        failures=[canbus.CanQueryFailure("can0", "completion sentinel missing", 0)],
    )
    monkeypatch.setattr(canbus, "scan_all_result", lambda *args, **kwargs: result)

    args = cli.build_parser().parse_args(["status", "--can"])
    cli.status_cmd(args)
    output = capsys.readouterr().out
    assert "CAN scan" in output
    assert "can0" in output and "can1" in output
    assert "abc123" in output and "Klipper" in output
    assert "completion sentinel missing" in output


def test_status_without_can_does_not_scan_can(c, monkeypatch):
    monkeypatch.setattr(canbus, "scan_all_result", lambda *args, **kwargs: pytest.fail("CAN scan was not requested"))
    args = cli.build_parser().parse_args(["status"])
    cli.status_cmd(args)


def test_status_leaves_the_on_demand_katapult_target_out(c, capsys):
    """`board` carries katapult on demand - built only by name, never a sweep.

    Listing it as "not built" on every kconfig type would be noise about
    firmware nobody asked for, so the block for a plain klipper+katapult type
    names only its application.
    """
    cli.status_cmd(cli.build_parser().parse_args(["status"]))
    out = capsys.readouterr().out
    board_block = out.split("\nboard  (chipset=", 1)[1]
    board_block = board_block.split("\n\n", 1)[0]
    assert board_block.splitlines()[1:] == [
        "  klipper: not built",
        "  - AAAA-if00: offline",
    ]


def _device_map(paths, tree, **devices) -> None:
    """What the watcher writes while it is running - the CLI's only offline
    source for which PlatformIO devices exist and where."""
    import json

    path = pathlib.Path(paths.printer_data) / "knomi" / "devices.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    for port in devices.values():
        pathlib.Path(port).write_text("", encoding="utf-8")
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "devices": {
                    ident: {"port": port} for ident, port in devices.items()
                },
            }
        ),
        encoding="utf-8",
    )


# --------------------------------------------------------------------------
# update-all: the command whose meaning was wrong
# --------------------------------------------------------------------------


def test_update_all_builds_every_provider_not_just_the_registry(
    c, pio_type, captured, capsys, fake_root, monkeypatch
):
    """The bug in one assertion. This walked `[mcu ...]` because that was the
    only list it had, so a PlatformIO type was never built and never mentioned."""
    _device_map(c.paths, pio_type, aaa111=str(fake_root / "ttyUSB0"))
    built: list[str] = []
    monkeypatch.setattr(
        "mcu_updater.providers.platformio.PlatformIO.build",
        lambda self, install, target, **kw: built.append(target.name),
    )
    monkeypatch.setattr(
        "mcu_updater.providers.kconfig_make.KconfigMake.build",
        lambda self, install, target, **kw: built.append(target.name),
    )

    cli.update_all(argparse.Namespace(yes=True, jobs=None))

    # Both providers, not just the registry's.
    assert sorted(built) == sorted(["board", ENV]), capsys.readouterr().out


def test_update_all_names_what_it_skipped_rather_than_dropping_it(
    c, captured, capsys, monkeypatch
):
    """A type silently passed over is the failure the Provider seam was written
    for: the fleet reports success and a board sits a month behind."""
    with open(c.paths.main_config, "a", encoding="utf-8") as fh:
        fh.write(
            "\n[firmware no_tree_fw]\nbuilder: platformio\n"
            "helper: knomi_serial\nflashers: esptool\n\n"
            "[type no_tree]\nfirmware: no_tree_fw\nplatformio_env: no_tree\n"
        )

    with pytest.raises(SystemExit) as exc:
        cli.update_all(argparse.Namespace(yes=True, jobs=None))

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "SKIP no_tree" in err
    assert "not found" in err
    assert "no_tree_fw" in err


def test_update_all_confirmation_no_longer_claims_only_mcus(c, monkeypatch, capsys):
    """It used to promise "every tracked MCU", which stopped being the truth the
    moment the build half started covering more."""
    asked: list[str] = []
    monkeypatch.setattr(cli, "_confirm", lambda prompt: asked.append(prompt) or False)

    cli.update_all(argparse.Namespace(yes=False, jobs=None))

    assert asked and "every tracked MCU" not in asked[0]


# --------------------------------------------------------------------------
# flash: one batch, whichever flasher owns the device
# --------------------------------------------------------------------------


def test_flashing_a_type_hands_its_boards_to_the_batch(c, captured, monkeypatch):
    monkeypatch.setattr(cli, "_confirm", lambda prompt: True)

    with pytest.raises(SystemExit):
        cli.flash_fw_cmd(argparse.Namespace(type="board", serial=None, yes=True))

    assert len(captured) == 1
    assert [t.id for t in captured[0]] == ["AAAA-if00"]
    assert {t.flasher for t in captured[0]} == {"flashtool"}


def test_a_whole_type_never_carries_force_even_if_one_board_would(c, captured, monkeypatch):
    """A blanket override across a fleet is exactly what the offset check
    exists to prevent - --force only ever reaches a single-device flash."""
    monkeypatch.setattr(cli, "_confirm", lambda prompt: True)

    with pytest.raises(SystemExit):
        cli.flash_fw_cmd(argparse.Namespace(type="board", serial=None, yes=True))

    assert len(captured) == 1
    assert all(t.detail.get("force") is False for t in captured[0])


def test_a_single_device_flash_can_be_forced(c, captured, monkeypatch):
    monkeypatch.setattr(cli, "_confirm", lambda prompt: True)

    with pytest.raises(SystemExit):
        cli.flash_fw_cmd(
            argparse.Namespace(type=None, serial="AAAA-if00", yes=True, force=True)
        )

    assert len(captured) == 1
    assert captured[0][0].detail["force"] is True


def test_a_single_device_flash_defaults_to_not_forced(c, captured, monkeypatch):
    monkeypatch.setattr(cli, "_confirm", lambda prompt: True)

    with pytest.raises(SystemExit):
        cli.flash_fw_cmd(
            argparse.Namespace(type=None, serial="AAAA-if00", yes=True, force=False)
        )

    assert len(captured) == 1
    assert captured[0][0].detail["force"] is False


def test_a_board_its_family_cannot_write_is_reported_not_written(c, monkeypatch):
    """The CLI selects through the family's `flashers:` too. A board that
    nothing in the list can write reaches the batch as a refusal, and the
    command exits non-zero."""
    with open(c.paths.main_config, encoding="utf-8") as fh:
        text = fh.read()
    assert text.count("flashers: flashtool") == 1
    with open(c.paths.main_config, "w", encoding="utf-8") as fh:
        fh.write(text.replace("flashers: flashtool", "flashers: esptool"))
    written: list = []
    refusals: list = []

    def fake(bench, targets, ctx, *, refused=(), **kwargs):
        written.extend(targets)
        refusals.extend(refused)
        return {"flashed": [], "failures": list(refused)}

    monkeypatch.setattr(flashers, "write_all", fake)

    with pytest.raises(SystemExit) as exc:
        cli.flash_fw_cmd(argparse.Namespace(type="board", serial=None, yes=True))

    assert exc.value.code == 1
    assert written == []
    assert [r["id"] for r in refusals] == ["AAAA-if00"]
    assert "[firmware klipper]" in refusals[0]["error"]


def test_flashing_a_platformio_type_uses_the_watcher_map(
    c, pio_type, captured, fake_root, monkeypatch
):
    """The CLI has no Moonraker, so it cannot ask Klipper which devices exist.
    The watcher's map is the source written for exactly this moment."""
    _device_map(c.paths, pio_type, aaa111=str(fake_root / "ttyUSB0"))
    monkeypatch.setattr(cli, "_confirm", lambda prompt: True)

    with pytest.raises(SystemExit):
        cli.flash_fw_cmd(argparse.Namespace(type=ENV, serial=None, yes=True))

    assert len(captured) == 1
    assert {t.flasher for t in captured[0]} == {"esptool"}


def test_a_platformio_type_with_no_watcher_map_says_so(c, pio_type, monkeypatch):
    """Flashing nothing and reporting success is the failure this area exists to
    prevent, so an absent map is "cannot tell" rather than "no devices"."""
    monkeypatch.setattr(cli, "_confirm", lambda prompt: True)

    with pytest.raises(UpdaterError) as exc:
        cli.flash_fw_cmd(argparse.Namespace(type=ENV, serial=None, yes=True))

    assert "watcher" in str(exc.value)


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------


def test_building_a_platformio_type_needs_no_firmware_family(
    c, pio_type, monkeypatch
):
    """Its env already names the board, the partitions and the flags, so `-f` is
    not merely optional there - it is meaningless, and unused regardless of
    which family the type declares."""
    built: list[str] = []
    monkeypatch.setattr(
        "mcu_updater.providers.platformio.PlatformIO.build",
        lambda self, install, target, **kw: built.append(target.name),
    )

    cli.build_fw_cmd(argparse.Namespace(type=ENV, fw=None, jobs=None, no_reseed=False))

    assert built == [ENV]


def test_building_a_platformio_type_with_no_tree_refuses_before_the_lock(
    c, monkeypatch, capsys
):
    """`source:` lives on the `[firmware ...]` section now. Naming no `source:`
    at all falls back to the `~/<family name>` convention, same as klipper and
    katapult - so an unconfigured tree reads as "not found" at that path,
    rather than "not configured" the way a genuinely empty key once did."""
    with open(c.paths.main_config, "a", encoding="utf-8") as fh:
        fh.write(
            "\n[firmware no_tree_fw]\nbuilder: platformio\n"
            "helper: knomi_serial\nflashers: esptool\n\n"
            "[type no_tree]\nfirmware: no_tree_fw\nplatformio_env: no_tree\n"
        )

    with pytest.raises(SystemExit) as exc:
        cli.build_fw_cmd(
            argparse.Namespace(type="no_tree", fw=None, jobs=None, no_reseed=False)
        )

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "not found" in err
    assert "no_tree_fw" in err


@pytest.fixture
def cmake_type(c, fake_root):
    """A cmake type with a source tree and no build directory.

    No `build/` on purpose: `blocked()` asks a *configured* tree for its target
    list by running real cmake, and a fixture that invited that would make this
    test depend on the toolchain being installed.
    """
    tree = fake_root / "roadrunner" / "rp2040"
    tree.mkdir(parents=True)
    (tree / "CMakeLists.txt").write_text("project(roadrunner)\n", encoding="utf-8")
    with open(c.paths.main_config, "a", encoding="utf-8") as fh:
        fh.write(
            f"\n[firmware roadrunner]\nsource: {tree}\nbuilder: cmake\nflashers: bootsel\n\n"
            f"[type roadrunner]\nchipset: rp2040\nfirmware: roadrunner\n"
            f"cmake_target: roadrunner_v1_i2c_rgb\n"
        )
    return tree


def test_building_a_cmake_type_stages_exactly_the_named_image(
    c, cmake_type, monkeypatch
):
    """`build -t roadrunner` was "MCU type 'roadrunner' does not exist".

    A cmake type is in neither the registry nor the display map, so the only
    route that reached it was a whole-fleet sweep - which is not what somebody
    who just edited one board's firmware wants to run. One make produces every
    image the tree declares; the point of `cmake_target:` is that exactly one
    of them is staged.
    """
    from mcu_updater.providers import cmake as cmake_mod

    def fake_run(cmd, *, cwd, reporter, cancel=None, dry_run=False, **kw):
        build = cmake_type / "build"
        build.mkdir(exist_ok=True)
        for name in ("roadrunner_v1_i2c_rgb", "roadrunner_v1_uart_grb"):
            (build / f"{name}.uf2").write_bytes(name.encode())
        return 0

    monkeypatch.setattr(cmake_mod.build_mod, "run_streamed", fake_run)

    cli.build_fw_cmd(
        argparse.Namespace(type="roadrunner", fw=None, jobs=None, no_reseed=False)
    )

    staged = pathlib.Path(c.paths.uf2_file("roadrunner", "roadrunner"))
    assert staged.read_bytes() == b"roadrunner_v1_i2c_rgb"


def test_the_build_parser_accepts_a_cmake_family_as_the_fw():
    """The bench command, at the layer the other cmake tests skip.

    `--fw` is `required` with `choices`, and the choices are the *declared*
    families (`main()` passes `firmware.names(paths)`), so `build -t roadrunner
    -f roadrunner` has to parse before `build_fw_cmd` ever sees it. The branch
    itself ignores `fw` - a cmake family names the tree and `cmake_target:`
    names the image - but the parser still insists on one, exactly as it does
    for a PlatformIO type.
    """
    parser = cli.build_parser(["klipper", "katapult", "roadrunner"])
    args = parser.parse_args(["build", "-t", "roadrunner", "-f", "roadrunner"])

    assert (args.type, args.fw) == ("roadrunner", "roadrunner")
    assert args.func is cli.build_fw_cmd


def test_cleaning_a_cmake_type_removes_its_build_dir(c, cmake_type, capsys):
    """The recovery path. A build directory whose CMakeCache pins a toolchain
    that has since moved keeps failing through any number of rebuilds, because
    the cache correctly names the right source tree - so nothing reconfigures."""
    build = cmake_type / "build"
    build.mkdir()
    (build / "CMakeCache.txt").write_text("stale\n", encoding="utf-8")

    cli.clean_fw_cmd(argparse.Namespace(type="roadrunner"))

    assert not build.exists()
    assert cmake_type.exists(), "only build/ goes, never the source tree"
    assert str(build) in capsys.readouterr().out


def test_cleaning_a_kconfig_type_says_there_is_nothing_to_clean(c, capsys):
    """Dispatched through the provider, so a build system with no build
    directory of its own answers None and this reports the non-event. Not an
    error: the user asked a reasonable question and got a real answer."""
    cli.clean_fw_cmd(argparse.Namespace(type="board"))

    out = capsys.readouterr().out
    assert "Nothing to clean" in out


def test_cleaning_an_unknown_type_refuses(c, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.clean_fw_cmd(argparse.Namespace(type="nosuchtype"))

    assert exc.value.code == 1
    assert "does not exist" in capsys.readouterr().err


def test_building_a_cmake_type_with_no_tree_refuses_before_the_lock(
    c, capsys, fake_root
):
    """The same shape as the PlatformIO refusal above, and for the same reason:
    a missing tree is setup that has to happen outside this tool, so it is said
    plainly rather than discovered as a cmake traceback."""
    with open(c.paths.main_config, "a", encoding="utf-8") as fh:
        fh.write(
            f"\n[firmware rr_no_tree]\nsource: {fake_root / 'nowhere'}\n"
            f"builder: cmake\nflashers: bootsel\n\n[type rr_no_tree]\nchipset: rp2040\n"
            f"firmware: rr_no_tree\ncmake_target: t\n"
        )

    with pytest.raises(SystemExit) as exc:
        cli.build_fw_cmd(
            argparse.Namespace(type="rr_no_tree", fw=None, jobs=None, no_reseed=False)
        )

    assert exc.value.code == 1
    assert "not found" in capsys.readouterr().err


def test_an_empty_device_map_falls_back_to_asking_the_devices(
    c, pio_type, captured, fake_root, monkeypatch
):
    """The map is a remembered path; discovery is the authority. knomi_serial's
    own docs put identity at flash time for exactly this reason, and the ports
    are free by the time this runs - which is the only moment it is possible.
    """
    from mcu_updater.providers import pio

    port = fake_root / "ttyUSB7"
    port.write_text("", encoding="utf-8")
    asked: list[str] = []

    def fake_discover(paths, settings, display, **kwargs):
        asked.append(display.name)
        return {
            "aaa111": pio.WatcherDevice(
                device_id="aaa111", port=str(port), present=True
            )
        }

    monkeypatch.setattr(pio, "discover", fake_discover)
    monkeypatch.setattr(cli, "_confirm", lambda prompt: True)

    with pytest.raises(SystemExit):
        cli.flash_fw_cmd(argparse.Namespace(type=ENV, serial=None, yes=True))

    assert asked == [ENV]
    assert [t.id for t in captured[0]] == [str(port)]


def test_discovery_failing_still_names_both_sources(c, pio_type, monkeypatch):
    """A host with no pyserial must not surface a tool error from the fallback -
    the useful message is the one naming what it tried."""
    from mcu_updater.errors import ToolMissingError
    from mcu_updater.providers import pio

    def boom(*a, **k):
        raise ToolMissingError("no python3 here", tool="python3")

    monkeypatch.setattr(pio, "discover", boom)
    monkeypatch.setattr(cli, "_confirm", lambda prompt: True)

    with pytest.raises(UpdaterError) as exc:
        cli.flash_fw_cmd(argparse.Namespace(type=ENV, serial=None, yes=True))

    assert "device map" in str(exc.value)
    assert "asking the devices directly" in str(exc.value)


# --------------------------------------------------------------------------
# flash: the third provider
#
# `flash -t roadrunner -s ...` died with `KeyError: 'roadrunner'` inside
# `_ports_free` on a real printer, because "not in the kconfig registry" meant
# "PlatformIO" for exactly as long as there were two providers. These tests are
# about which provider the CLI decides a name belongs to, and nothing else.
# --------------------------------------------------------------------------


RR_SERIAL = "5K3DNTFCR1B3C9D0RZMYA3Y720"


def _cmake_flashable(c, fake_root, *, helper: bool = True, staged: bool = True):
    """A CMake type a flash can actually reach: a helper and a built UF2.

    Separate from `cmake_type` above, which deliberately declares neither - the
    build tests want the bare declaration, and a flash wants the two things that
    make a declaration writable.
    """
    import os

    tree = fake_root / "roadrunner" / "rp2040"
    tree.mkdir(parents=True, exist_ok=True)
    (tree / "CMakeLists.txt").write_bytes(b"project(roadrunner)\n")
    helper_line = "helper: roadrunner\n" if helper else ""
    with open(c.paths.main_config, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(
            f"\n[firmware roadrunner]\nsource: {tree}\nbuilder: cmake\n{helper_line}"
            "flashers: bootsel\n"
            f"\n[type roadrunner]\nchipset: rp2040\nfirmware: roadrunner\n"
            f"cmake_target: roadrunner_v1_i2c_rgb\nserials: {RR_SERIAL}\n"
        )
    if staged:
        os.makedirs(c.paths.artifact_dir("roadrunner"), exist_ok=True)
        pathlib.Path(c.paths.uf2_file("roadrunner", "roadrunner")).write_bytes(b"UF2\n")
    return tree


@pytest.fixture
def cmake_flashable(c, fake_root):
    return _cmake_flashable(c, fake_root)


def test_flashing_a_cmake_serial_alone_routes_to_the_helper(
    c, cmake_flashable, captured, monkeypatch
):
    """`resolve_serial` only ever saw the kconfig registry, so a serial tracked
    under a `[type roadrunner]` section was reported as tracked nowhere."""
    monkeypatch.setattr(cli, "_confirm", lambda prompt: True)

    with pytest.raises(SystemExit) as exc:
        cli.flash_fw_cmd(
            argparse.Namespace(type=None, serial=RR_SERIAL, yes=True, force=False)
        )

    assert exc.value.code == 0
    assert len(captured) == 1
    assert [t.flasher for t in captured[0]] == ["bootsel"]
    assert [t.id for t in captured[0]] == [RR_SERIAL]
    assert captured[0][0].type == "roadrunner"


def test_flashing_a_cmake_serial_with_its_type_routes_the_same_way(
    c, cmake_flashable, captured, monkeypatch
):
    """The printer's failing command, verbatim. `-t` named the type correctly;
    the CLI took "not in the registry" to mean the PlatformIO branch."""
    monkeypatch.setattr(cli, "_confirm", lambda prompt: True)

    with pytest.raises(SystemExit) as exc:
        cli.flash_fw_cmd(
            argparse.Namespace(
                type="roadrunner", serial=RR_SERIAL, yes=True, force=False
            )
        )

    assert exc.value.code == 0
    assert [t.flasher for t in captured[0]] == ["bootsel"]


def test_the_cmake_target_carries_the_staged_uf2_and_its_stop_services(
    c, cmake_flashable, captured, monkeypatch
):
    """`for_cmake`, not `for_platformio` - the resolver that indexes the PlatformIO
    map is the one that raised KeyError."""
    monkeypatch.setattr(cli, "_confirm", lambda prompt: True)

    with pytest.raises(SystemExit):
        cli.flash_fw_cmd(
            argparse.Namespace(type=None, serial=RR_SERIAL, yes=True, force=False)
        )

    target = captured[0][0]
    assert target.detail["uf2_file"] == c.paths.uf2_file("roadrunner", "roadrunner")
    assert target.detail["chipset"] == "rp2040"
    assert target.detail["helper"].name == "roadrunner"
    assert "klipper" in target.stop_services


def test_flashing_a_cmake_type_by_name_alone_is_refused(
    c, cmake_flashable, captured, capsys, monkeypatch
):
    """Deferred work with its own spec, not a silent no-op. `bulk.py` refuses
    the same thing for the same reason and points at the per-device call."""
    monkeypatch.setattr(cli, "_confirm", lambda prompt: True)

    with pytest.raises(SystemExit) as exc:
        cli.flash_fw_cmd(argparse.Namespace(type="roadrunner", serial=None, yes=True))

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "CMake" in err and "roadrunner" in err
    assert "-s <serial>" in err
    assert captured == []


def test_a_cmake_type_with_no_helper_names_its_flashers(c, fake_root, captured, monkeypatch):
    """A family whose helper cannot request BOOTSEL leaves `bootsel` nothing to
    write a running board with, and the refusal names the list to fix."""
    _cmake_flashable(c, fake_root, helper=False)
    monkeypatch.setattr(cli, "_confirm", lambda prompt: True)

    with pytest.raises(NoFlasherError) as exc:
        cli.flash_fw_cmd(
            argparse.Namespace(type=None, serial=RR_SERIAL, yes=True, force=False)
        )

    assert "[firmware roadrunner]" in str(exc.value)
    assert "flashers: bootsel" in str(exc.value)
    assert captured == []


def test_a_cmake_type_with_nothing_built_says_to_build_it(
    c, fake_root, captured, monkeypatch
):
    monkeypatch.setattr(cli, "_confirm", lambda prompt: True)
    _cmake_flashable(c, fake_root, staged=False)

    with pytest.raises(UpdaterError) as exc:
        cli.flash_fw_cmd(
            argparse.Namespace(type=None, serial=RR_SERIAL, yes=True, force=False)
        )

    assert "Build it first" in str(exc.value)
    assert captured == []


def test_an_untracked_serial_can_be_added_to_a_cmake_type(
    c, fake_root, captured, monkeypatch
):
    """The add-prompt writes through the declared-section writer, so a CMake
    type gains an identity without the kconfig registry claiming its build."""
    _cmake_flashable(c, fake_root)
    monkeypatch.setattr(cli, "_confirm", lambda prompt: True)

    with pytest.raises(SystemExit) as exc:
        cli.flash_fw_cmd(
            argparse.Namespace(type="roadrunner", serial="RR-NEW", yes=True, force=False)
        )

    assert exc.value.code == 0
    assert [t.id for t in captured[0]] == ["RR-NEW"]
    reloaded = Registry.load(c.paths)
    assert reloaded.resolve_declared_serial("RR-NEW") == "roadrunner"
    assert "roadrunner" not in reloaded.names(), "still not a kconfig-built type"


@pytest.mark.parametrize(
    "type_name,serial",
    [
        ("roadrunner", RR_SERIAL),
        ("roadrunner", None),
        (None, RR_SERIAL),
        ("roadrunner", "RR-NEW"),
    ],
)
def test_no_cmake_argument_combination_raises_keyerror(
    c, cmake_flashable, captured, monkeypatch, type_name, serial
):
    """The printer traceback, as a regression test: `KeyError: 'roadrunner'`
    from `_ports_free` indexing the PlatformIO map with a CMake name."""
    monkeypatch.setattr(cli, "_confirm", lambda prompt: True)

    with pytest.raises((SystemExit, UpdaterError)):
        cli.flash_fw_cmd(
            argparse.Namespace(type=type_name, serial=serial, yes=True, force=False)
        )


def test_an_unknown_type_is_named_rather_than_indexed(c, monkeypatch):
    """Before the seam this reached `pio.load(paths)[name]`. A typo is a typo,
    whichever provider the user meant."""
    from mcu_updater.errors import UnknownTypeError

    monkeypatch.setattr(cli, "_confirm", lambda prompt: True)

    with pytest.raises(UnknownTypeError) as exc:
        cli.flash_fw_cmd(
            argparse.Namespace(type="nosuchtype", serial=None, yes=True, force=False)
        )

    assert exc.value.data["type"] == "nosuchtype"


def test_a_serial_tracked_under_another_provider_is_refused_with_its_name(
    c, cmake_flashable, captured, monkeypatch
):
    """The kconfig-only search behind the old `resolve_serial` could not see a
    CMake type, so `-t board -s <roadrunner serial>` looked like a brand new
    device and offered to add it - tracking one board under two types. The
    declared search sees it and says where it actually lives."""
    from mcu_updater.errors import SerialTrackedElsewhereError

    monkeypatch.setattr(cli, "_confirm", lambda prompt: True)

    with pytest.raises(SerialTrackedElsewhereError) as exc:
        cli.flash_fw_cmd(
            argparse.Namespace(type="board", serial=RR_SERIAL, yes=True, force=False)
        )

    assert "Did you mean -t roadrunner?" in str(exc.value)
    assert exc.value.data["tracked_under"] == ["roadrunner"]
    assert captured == []


def test_an_ambiguous_serial_still_asks_for_a_type(c, cmake_flashable, monkeypatch):
    """One serial under two types, one of them CMake: the same disambiguation
    prompt, now reachable across providers rather than within one."""
    from mcu_updater.errors import AmbiguousSerialError

    reg = Registry.load(c.paths)
    reg.add_serial("board", RR_SERIAL)
    save_registry(reg, c.paths)
    monkeypatch.setattr(cli, "_confirm", lambda prompt: True)

    with pytest.raises(AmbiguousSerialError) as exc:
        cli.flash_fw_cmd(
            argparse.Namespace(type=None, serial=RR_SERIAL, yes=True, force=False)
        )

    assert sorted(exc.value.data["tracked_under"]) == ["board", "roadrunner"]


def _declare_roadrunner(paths, serial: str, *, helper: bool = False) -> None:
    helper_line = "helper: roadrunner\n" if helper else ""
    with open(paths.main_config, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(
            "\n[firmware roadrunner]\nsource: ~/roadrunner/rp2040\nbuilder: cmake\n"
            f"{helper_line}flashers: bootsel\n\n"
            "[type roadrunner]\nchipset: rp2040\nfirmware: roadrunner\n"
            f"cmake_target: roadrunner_v1_usbserial\nserials:\n    {serial}\n"
        )


class _TrackableOnly:
    name = "roadrunner"

    def is_trackable(self, serial: str) -> helpers.TrackVerdict:
        return helpers.TrackVerdict(
            ok=False,
            reason="trackable-only helper says provision it first",
            remedy="provision",
        )


def test_status_lists_a_cmake_type_and_its_board(c, fake_root, capsys):
    """The open bug: tracked in the UI, invisible to the CLI."""
    _declare_roadrunner(c.paths, "RR-ONE")
    cli.status_cmd(cli.build_parser().parse_args(["status"]))
    out = capsys.readouterr().out
    assert "\nroadrunner  (chipset=rp2040)" in out
    assert "  roadrunner: " in out
    assert "  - RR-ONE: offline" in out

    make_device(fake_root / "bus", "Vylyne", "Roadrunner", "RR-ONE")
    cli.status_cmd(cli.build_parser().parse_args(["status"]))
    assert "  - RR-ONE: online" in capsys.readouterr().out


def test_status_lists_a_platformio_type(c, pio_type, capsys):
    cli.status_cmd(cli.build_parser().parse_args(["status"]))
    assert f"\n{ENV}  (chipset=?)" in capsys.readouterr().out


def _status_block(out: str, name: str) -> list[str]:
    block = out.split(f"\n{name}  (chipset=", 1)[1]
    return block.split("\n\n", 1)[0].splitlines()[1:]


def test_status_says_a_type_with_no_devices_tracks_none(c, pio_type, capsys):
    """One wording for every type, whatever builds it."""
    reg = Registry.load(c.paths)
    reg.add_type("empty", "stm32f072xb")
    save_registry(reg, c.paths)
    cli.status_cmd(cli.build_parser().parse_args(["status"]))
    out = capsys.readouterr().out
    assert cli.NO_TRACKED_DEVICES == "  (no tracked devices)"
    assert cli.NO_TRACKED_DEVICES in _status_block(out, "empty")
    assert cli.NO_TRACKED_DEVICES in _status_block(out, ENV)
    assert "no tracked serials" not in out


def test_status_lists_a_can_only_types_uuid(c, capsys):
    """A CAN board is tracked by uuid, not by a USB serial - so it is listed,
    and never called untracked. Its state is not guessed: the inventory never
    infers a CAN node's state and status does not query tracked nodes."""
    reg = Registry.load(c.paths)
    reg.add_type("canboard", "stm32f072xb")
    save_registry(reg, c.paths)
    with open(c.paths.main_config, encoding="utf-8") as fh:
        text = fh.read()
    with open(c.paths.main_config, "w", encoding="utf-8") as fh:
        fh.write(text.replace("[type canboard]", "[type canboard]\ncanbus_uuids: 0123456789ab", 1))
    assert typelist.load(c.paths)[-1].canbus_uuids == ("0123456789ab",)
    cli.status_cmd(cli.build_parser().parse_args(["status"]))
    out = capsys.readouterr().out
    block = _status_block(out, "canboard")
    assert "no tracked" not in "\n".join(block)
    assert "  - 0123456789ab (CAN): state unknown (CAN nodes are not queried)" in block
    assert "online" not in "\n".join(block)


def test_add_serial_tracks_a_board_under_a_cmake_type(c):
    _declare_roadrunner(c.paths, "RR-ONE")
    cli.add_serial(argparse.Namespace(type="roadrunner", serial="RR-NEW"))
    assert Registry.load(c.paths).declared_serials("roadrunner") == ["RR-ONE", "RR-NEW"]


def test_add_serial_refuses_a_board_tracked_under_another_type(c):
    _declare_roadrunner(c.paths, "RR-ONE")
    with pytest.raises(SerialTrackedElsewhereError):
        cli.add_serial(argparse.Namespace(type="roadrunner", serial="AAAA-if00"))


def test_add_serial_provisions_an_unprovisioned_roadrunner_then_tracks_it(
    c, monkeypatch, capsys
):
    """What used to be a refusal. The CLI reaches cmake types, and the family's
    helper can provision, so the operator is not sent to the web UI and back."""
    _declare_roadrunner(c.paths, "RR-ONE")
    monkeypatch.setattr(
        tracking,
        "add_serial",
        lambda paths, name, serial: tracking.Tracked(
            added=True, chipset="rp2040", serial="RR-NEW", provisioned_from=serial
        ),
    )

    cli.add_serial(
        argparse.Namespace(type="roadrunner", serial="RR-UNPROVISIONED-50543165187A4D1C")
    )

    out = capsys.readouterr().out
    assert "Provisioned RR-UNPROVISIONED-50543165187A4D1C as RR-NEW" in out
    assert "Added serial RR-NEW to roadrunner" in out


def test_add_serial_refuses_when_the_trackable_helper_has_no_provisioner(
    c, monkeypatch
):
    """A helper's durability refusal stands even when it cannot fix the serial."""
    from mcu_updater.helpers import registry as helpers_registry

    monkeypatch.setitem(helpers_registry._BY_NAME, "roadrunner", _TrackableOnly())
    _declare_roadrunner(c.paths, "RR-ONE", helper=True)

    with pytest.raises(UnprovisionedSerialError):
        cli.add_serial(
            argparse.Namespace(type="roadrunner", serial="RR-UNPROVISIONED-50543165187A4D1C")
        )

    assert Registry.load(c.paths).declared_serials("roadrunner") == ["RR-ONE"]


def test_remove_serial_untracks_a_board_under_a_cmake_type(c):
    _declare_roadrunner(c.paths, "RR-ONE")
    cli.remove_serial(argparse.Namespace(type="roadrunner", serial="RR-ONE"))
    assert Registry.load(c.paths).declared_serials("roadrunner") == []


def test_remove_type_removes_a_cmake_type(c):
    _declare_roadrunner(c.paths, "RR-ONE")
    cli.remove_mcu_type(argparse.Namespace(type="roadrunner", force=True))
    assert "roadrunner" not in {e.name for e in typelist.load(c.paths)}


# --------------------------------------------------------------------------
# registry writes: one path, through the registry lock
# --------------------------------------------------------------------------


UNPROVISIONED = "RR-UNPROVISIONED-50543165187A4D1C"


def _main(fake_root, monkeypatch, argv: list[str]) -> int:
    """Drive `cli.main` against the test tree; the exit code it ends with.

    Through `main` rather than the handler, because "a refusal prints a message
    and exits non-zero" is `main`'s top-level `UpdaterError` handler at work -
    a handler called directly raises, which is not what a user sees."""
    monkeypatch.setenv("MCU_UPDATER_HOME", str(fake_root))
    monkeypatch.setenv("MCU_UPDATER_FAKE_BUS", str(fake_root / "bus"))
    monkeypatch.delenv("MCU_UPDATER_CONFIG_DIR", raising=False)
    monkeypatch.delenv("MCU_UPDATER_DATA_DIR", raising=False)
    try:
        cli.main(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1
    return 0


def test_the_flash_prompt_tracks_a_new_serial_through_tracking(
    c, fake_root, captured, monkeypatch
):
    """The add-prompt used to write with no lock, onto a registry read before
    the prompt - so an edit the panel made while the prompt waited was erased
    by the save."""
    calls: list[tuple[str, str]] = []
    real = tracking.add_serial

    def spy(paths, name, serial):
        calls.append((name, serial))
        return real(paths, name, serial)

    monkeypatch.setattr(tracking, "add_serial", spy)
    monkeypatch.setattr(cli, "_confirm", lambda prompt: True)

    code = _main(fake_root, monkeypatch, ["flash", "-t", "board", "-s", "BBBB-if00", "-y"])

    assert code == 0
    assert calls == [("board", "BBBB-if00")]
    assert Registry.load(c.paths).declared_serials("board") == ["AAAA-if00", "BBBB-if00"]


def test_the_flash_prompt_refuses_a_serial_tracked_elsewhere_while_it_waited(
    c, fake_root, cmake_flashable, captured, monkeypatch, capsys
):
    """The serial was untracked when the prompt was asked, and tracked under
    another type by the time it was answered. The write re-checks under the
    lock, so one board is not tracked under two types."""

    def confirm_while_the_panel_tracks_it(prompt):
        with Registry.mutate(c.paths, "the panel") as writable:
            writable.add_declared_serial("roadrunner", "BBBB-if00")
        return True

    monkeypatch.setattr(cli, "_confirm", confirm_while_the_panel_tracks_it)

    code = _main(fake_root, monkeypatch, ["flash", "-t", "board", "-s", "BBBB-if00", "-y"])

    assert code == 1
    assert "already tracked under 'roadrunner'" in capsys.readouterr().err
    assert Registry.load(c.paths).declared_serials("board") == ["AAAA-if00"]
    assert captured == []


def test_flash_refuses_a_serial_tracked_elsewhere_with_a_message(
    c, fake_root, cmake_flashable, captured, monkeypatch, capsys
):
    monkeypatch.setattr(cli, "_confirm", lambda prompt: True)

    code = _main(fake_root, monkeypatch, ["flash", "-t", "board", "-s", RR_SERIAL, "-y"])

    err = capsys.readouterr().err
    assert code == 1
    assert err.startswith("ERROR: ") and "Traceback" not in err
    assert RR_SERIAL not in Registry.load(c.paths).declared_serials("board")
    assert captured == []


def test_the_flash_prompt_refuses_an_unprovisioned_roadrunner_serial(
    c, fake_root, captured, monkeypatch, capsys
):
    """`add_declared_serial` has no idea what an unprovisioned serial is, so
    answering "y" persisted the RP2040's flash UID.

    A trackability helper is configured without provisioning capability, so
    this reaches the refusal path without driving hardware."""
    from mcu_updater.helpers import registry as helpers_registry

    monkeypatch.setitem(helpers_registry._BY_NAME, "roadrunner", _TrackableOnly())
    _cmake_flashable(c, fake_root, helper=True)
    monkeypatch.setattr(cli, "_confirm", lambda prompt: True)

    code = _main(
        fake_root, monkeypatch, ["flash", "-t", "roadrunner", "-s", UNPROVISIONED, "-y"]
    )

    err = capsys.readouterr().err
    assert code == 1
    assert err.startswith("ERROR: ") and "provision it first" in err
    assert UNPROVISIONED not in Registry.load(c.paths).declared_serials("roadrunner")
    assert captured == []


def test_the_flash_prompt_provisions_an_unprovisioned_roadrunner_then_flashes_it(
    c, fake_root, monkeypatch, capsys
):
    """Fix 3: with a real provisioner in play, the flash prompt provisions the
    board, tracks the serial provisioning returned (not the diagnostic
    identity it was asked about), and flashes *that* serial - not the stale
    one it started with.

    `_cmake_targets` is spied on rather than driven to a real write: routing
    a fake helper through the bootsel flasher's own device-presence check is
    a different test than this one, which is only about which serial reaches
    the targets call.
    """
    from mcu_updater.helpers import registry as helpers_registry

    class _FakeRoadrunner:
        name = "roadrunner"
        label = "Roadrunner"

        def __init__(self) -> None:
            self.calls: list[str] = []

        def is_trackable(self, serial: str) -> helpers.TrackVerdict:
            if serial.startswith("RR-UNPROVISIONED-"):
                return helpers.TrackVerdict(
                    ok=False,
                    reason="fake helper says provision this identity first",
                    remedy="provision",
                )
            return helpers.TrackVerdict(ok=True)

        def provision(self, paths, serial: str) -> str:
            self.calls.append(serial)
            return RR_SERIAL

    helper = _FakeRoadrunner()
    monkeypatch.setitem(helpers_registry._BY_NAME, "roadrunner", helper)
    _cmake_flashable(c, fake_root, helper=True, staged=True)
    # `_cmake_flashable` already tracks `RR_SERIAL`; provisioning must not be
    # refused as "tracked elsewhere" by the serial it is about to become.
    monkeypatch.setattr(cli, "_confirm", lambda prompt: True)

    targeted: list[str] = []

    def spy_cmake_targets(c, mcu_type, serial):
        targeted.append(serial)
        return []

    monkeypatch.setattr(cli, "_cmake_targets", spy_cmake_targets)

    code = _main(
        fake_root, monkeypatch, ["flash", "-t", "roadrunner", "-s", UNPROVISIONED, "-y"]
    )

    captured_io = capsys.readouterr()
    assert helper.calls == [UNPROVISIONED]
    assert f"Provisioned {UNPROVISIONED} as {RR_SERIAL}" in captured_io.out
    assert targeted == [RR_SERIAL], "targeted the tracked serial, not the stale one"
    assert code == 0, f"STDOUT={captured_io.out!r} STDERR={captured_io.err!r}"


def _adopting(monkeypatch, *serials: str) -> None:
    """add-mcu as far as the adoption prompt, with every candidate accepted."""
    monkeypatch.setattr(
        cli,
        "_build_interactive",
        lambda c, t, fw: types.SimpleNamespace(bin_path="k.bin", uf2_path=None),
    )
    monkeypatch.setattr(cli, "flash_initial_bootloader", lambda *a, **kw: None)
    monkeypatch.setattr(
        cli,
        "adoptable_devices",
        lambda paths, before, chipset: [
            types.SimpleNamespace(serial=s, path=f"/dev/serial/by-id/{s}") for s in serials
        ],
    )
    monkeypatch.setattr(cli, "_confirm", lambda prompt: True)


def test_add_mcu_adopts_a_new_board_through_tracking(c, fake_root, monkeypatch):
    _adopting(monkeypatch, "CCCC-if00")

    code = _main(fake_root, monkeypatch, ["add-mcu", "-t", "board"])

    assert code == 0
    assert Registry.load(c.paths).declared_serials("board") == ["AAAA-if00", "CCCC-if00"]


def test_add_mcu_refuses_to_adopt_a_board_tracked_under_another_type(
    c, fake_root, monkeypatch, capsys
):
    """Adoption wrote with `reg.add_serial`, which looks only at the type it
    adds to - a board another type already tracked became tracked twice."""
    _declare_roadrunner(c.paths, "RR-ONE")
    _adopting(monkeypatch, "RR-ONE")

    code = _main(fake_root, monkeypatch, ["add-mcu", "-t", "board"])

    err = capsys.readouterr().err
    assert code == 1
    assert "already tracked under 'roadrunner'" in err
    assert Registry.load(c.paths).declared_serials("board") == ["AAAA-if00"]


def _registry_lock_is_held(c, monkeypatch) -> None:
    """The registry lock refusing, on every platform - flock is a no-op on
    Windows, so really holding it proves nothing there."""
    real = ExclusiveLock.acquire

    def acquire(self, label):
        if self.path == c.paths.registry_lock_file:
            raise BusyError("another firmware operation is already running (the panel).")
        return real(self, label)

    monkeypatch.setattr(ExclusiveLock, "acquire", acquire)


def test_add_type_writes_under_the_registry_lock(c, fake_root, monkeypatch, capsys):
    _registry_lock_is_held(c, monkeypatch)

    code = _main(fake_root, monkeypatch, ["add-type", "-t", "newboard", "-c", "rp2040"])

    err = capsys.readouterr().err
    assert code == 1
    assert err.startswith("ERROR: ") and "already running" in err
    assert "newboard" not in Registry.load(c.paths).names()


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="flock is unavailable on Windows; the lock degrades to a no-op there",
)
def test_add_type_is_refused_while_the_registry_lock_is_really_held(
    c, fake_root, monkeypatch, capsys
):
    with ExclusiveLock(c.paths, path=c.paths.registry_lock_file).acquire("the panel"):
        code = _main(fake_root, monkeypatch, ["add-type", "-t", "newboard", "-c", "rp2040"])

    assert code == 1
    assert "the panel" in capsys.readouterr().err
    assert "newboard" not in Registry.load(c.paths).names()


def test_add_type_writes_when_the_lock_is_free(c, fake_root, monkeypatch):
    code = _main(fake_root, monkeypatch, ["add-type", "-t", "newboard", "-c", "rp2040"])

    assert code == 0
    assert Registry.load(c.paths).get("newboard").chipset == "rp2040"


def test_add_type_refuses_the_name_of_a_type_another_builder_declares(
    c, fake_root, monkeypatch, capsys
):
    """The prompt's pre-check sees only kconfig types, so no prompt appears -
    and the write passes `overwrite=True`. The refusal has to come from
    `add_type` under the lock, or the cmake section is rewritten as a kconfig
    one with its serials emptied."""
    _declare_roadrunner(c.paths, "RR-ONE")
    before = open(c.paths.main_config, encoding="utf-8").read()

    code = _main(fake_root, monkeypatch, ["add-type", "-t", "roadrunner", "-c", "rp2040"])

    err = capsys.readouterr().err
    assert code == 1
    assert "'cmake'" in err and "remove that type first" in err
    assert open(c.paths.main_config, encoding="utf-8").read() == before


def test_add_type_keeps_an_edit_made_while_the_overwrite_prompt_waited(
    c, fake_root, monkeypatch
):
    """The overwrite prompt reads the registry without the lock. Saving that
    read would erase whatever was written while the prompt waited; the write
    re-reads under the lock instead."""

    def confirm_while_the_panel_adds_a_type(prompt):
        with Registry.mutate(c.paths, "the panel") as writable:
            writable.add_type("paneltype", "rp2040")
        return True

    monkeypatch.setattr(cli, "_confirm", confirm_while_the_panel_adds_a_type)

    code = _main(fake_root, monkeypatch, ["add-type", "-t", "board", "-c", "stm32g0b1xx"])

    assert code == 0
    reloaded = Registry.load(c.paths)
    assert reloaded.get("board").chipset == "stm32g0b1xx"
    assert "paneltype" in reloaded.names()


def test_add_mcu_offers_the_rest_after_refusing_one(c, fake_root, monkeypatch, capsys):
    """Every candidate was just flashed; one refusal must not leave the others
    unasked. The refusal still shows, in the message and the exit code."""
    _declare_roadrunner(c.paths, "RR-ONE")
    _adopting(monkeypatch, "RR-ONE", "CCCC-if00")

    code = _main(fake_root, monkeypatch, ["add-mcu", "-t", "board"])

    err = capsys.readouterr().err
    assert code == 1
    assert "ERROR: " in err and "already tracked under 'roadrunner'" in err
    assert Registry.load(c.paths).declared_serials("board") == ["AAAA-if00", "CCCC-if00"]


def test_the_flash_prompt_says_so_when_the_serial_was_tracked_while_it_waited(
    c, fake_root, captured, monkeypatch, capsys
):
    def confirm_while_the_panel_tracks_it_here(prompt):
        with Registry.mutate(c.paths, "the panel") as writable:
            writable.add_declared_serial("board", "BBBB-if00")
        return True

    monkeypatch.setattr(cli, "_confirm", confirm_while_the_panel_tracks_it_here)

    code = _main(fake_root, monkeypatch, ["flash", "-t", "board", "-s", "BBBB-if00", "-y"])

    out = capsys.readouterr().out
    assert code == 0
    assert "Serial BBBB-if00 is already tracked under board" in out
    assert "Added serial" not in out
    assert Registry.load(c.paths).declared_serials("board") == ["AAAA-if00", "BBBB-if00"]


def test_registry_has_no_public_save():
    """`Registry.mutate` is the one write path: lock, re-read, change, save."""
    assert not hasattr(Registry, "save")
