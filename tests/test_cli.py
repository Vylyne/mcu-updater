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

import pytest

from mcu_updater import cli, flashers
from mcu_updater.config import Registry
from mcu_updater.discovery import canbus
from mcu_updater.errors import UpdaterError
from mcu_updater.settings import Settings

ENV = "knomi_toolchanger"


@pytest.fixture
def c(paths, fake_root, monkeypatch):
    """A CLI context pinned to the test tree, with no real services."""
    context = cli.Context(
        paths=paths, settings=Settings(service_backend="null", clean_before_build=False)
    )
    monkeypatch.setattr(cli, "_ctx", context)

    reg = Registry.load(paths)
    reg.add_type("board", "stm32f072xb")
    reg.add_serial("board", "AAAA-if00")
    reg.save(paths)

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
            f"\n[firmware knomi_serial]\nsource: {tree}\nbuilder: platformio\n\n"
            f"[type {ENV}]\nfirmware: knomi_serial\nenv: {ENV}\nservice:\n"
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
            "\n[firmware no_tree_fw]\nbuilder: platformio\n\n"
            "[type no_tree]\nfirmware: no_tree_fw\nenv: no_tree\n"
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
            "\n[firmware no_tree_fw]\nbuilder: platformio\n\n"
            "[type no_tree]\nfirmware: no_tree_fw\nenv: no_tree\n"
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
            f"\n[firmware roadrunner]\nsource: {tree}\nbuilder: cmake\n\n"
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


def test_building_a_cmake_type_with_no_tree_refuses_before_the_lock(
    c, capsys, fake_root
):
    """The same shape as the PlatformIO refusal above, and for the same reason:
    a missing tree is setup that has to happen outside this tool, so it is said
    plainly rather than discovered as a cmake traceback."""
    with open(c.paths.main_config, "a", encoding="utf-8") as fh:
        fh.write(
            f"\n[firmware rr_no_tree]\nsource: {fake_root / 'nowhere'}\n"
            f"builder: cmake\n\n[type rr_no_tree]\nchipset: rp2040\n"
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
