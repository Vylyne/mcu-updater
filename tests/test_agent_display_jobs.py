"""Building and flashing displays through the agent.

Two properties carry the risk here.

**The screen list is read before Klipper stops.** It comes from
`configfile.settings`, which only a running Klipper can answer - so reading it
after the stop would find nothing and flash nothing. Every other flow in the
agent can query mid-job; this one cannot, and the ordering is the whole design.

**A port is never inferred.** PlatformIO auto-detects one when told nothing, and
every display here is an indistinguishable CH340 - it was seen choosing between
two of them on the printer, with no way for the user to know which it took.
"""

from __future__ import annotations

import pathlib

import pytest

from mcu_updater.agent.methods import Api
from mcu_updater.agent.rpc import RpcError
from mcu_updater.jobs import JobRunner
from mcu_updater.providers import pio as pio_mod
from mcu_updater.service import NullService

from .conftest import display_objects, serve_klipper, write_settings

ENV = "knomi_toolchanger"


def _moonraker(sections: dict, print_state="standby", idle_state="Ready"):
    return serve_klipper(
        display_objects(sections), print_state=print_state, idle_state=idle_state
    )


@pytest.fixture
def screens(fake_root):
    """Two ports that exist, as udev symlinks would."""
    out = {}
    for name in ("t0_knomi", "t1_knomi"):
        port = fake_root / f"knomi_{name}"
        port.write_text("", encoding="utf-8")
        out[f"knomi_serial {name}"] = {"serial": str(port)}
    return out


@pytest.fixture
def api(paths, live_registry_text, fake_root, screens):
    # live_registry_text already declares [firmware knomi_serial], pointed
    # at ~/knomi_serial - build the tree there (paths.home is fake_root)
    # rather than declaring a second, colliding family.
    tree = fake_root / "knomi_serial"
    (tree / ".pio" / "build" / ENV).mkdir(parents=True)

    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    write_settings(paths, dry_run="true", service_backend="null", enable_flashing="true")
    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write(f"\n[type {ENV}]\nchipset: esp32\nfirmware: knomi_serial\nenv: {ENV}\n")

    runner = JobRunner(
        paths,
        lambda: __import__(
            "mcu_updater.settings", fromlist=["load_settings"]
        ).load_settings(paths.settings_file),
    )
    agent = Api(paths, runner=runner, call=_moonraker(screens))
    agent.KLIPPY_READY_TIMEOUT = 2.0
    agent.KLIPPY_RESTART_TIMEOUT = 2.0
    agent.KLIPPY_POLL_INTERVAL = 0.05
    yield agent
    runner._cancel.set()
    runner.wait(timeout=20)


@pytest.fixture
def no_pio(monkeypatch):
    """Stand in for PlatformIO, capturing what it would have been told."""
    calls: list[list[str]] = []

    def fake(cmd, **kwargs):
        calls.append(cmd)
        reporter = kwargs.get("reporter")
        if reporter and "upload" in cmd:
            reporter("stdout", "Chip is ESP32-S3 (QFN56) (revision v0.2)")
            reporter("stdout", f"MAC: cc:ba:97:19:aa:{len(calls):02d}")
        return 0

    monkeypatch.setattr(pio_mod, "run_streamed", fake)
    monkeypatch.setattr(pio_mod, "find_pio", lambda s: "/usr/bin/pio")
    return calls


# --------------------------------------------------------------------------
# gates
# --------------------------------------------------------------------------


def test_flashing_displays_needs_it_enabled(paths, live_registry_text, fake_root, screens):
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    write_settings(paths, dry_run="true", service_backend="null")
    with open(paths.registry_file, "a", encoding="utf-8") as fh:
        fh.write(f"\n[type {ENV}]\nchipset: esp32\nfirmware: knomi_serial\nenv: {ENV}\n")
    runner = JobRunner(paths, lambda: __import__(
        "mcu_updater.settings", fromlist=["load_settings"]
    ).load_settings(paths.settings_file))

    with pytest.raises(RpcError) as exc:
        Api(paths, runner=runner, call=_moonraker(screens)).flash({"name": ENV})
    assert exc.value.data["code"] == "flashing_disabled"


def test_an_unknown_display_type_fails_before_a_job(api):
    with pytest.raises(RpcError) as exc:
        api.dispatch("fw.flash", {"name": "nosuchscreen"})
    assert exc.value.data["code"] == "unknown_type"
    assert api.runner.current() is None


def test_no_reachable_screen_is_refused_rather_than_a_job_that_does_nothing(
    api, paths, fake_root, screens
):
    """Every configured port missing means there is nothing to write to - and
    stopping Klipper to discover that would be an outage for nothing."""
    for section in screens.values():
        import os

        os.remove(section["serial"])

    with pytest.raises(RpcError) as exc:
        api.dispatch("fw.flash", {"name": ENV})
    assert exc.value.data["code"] == "nothing_to_do"
    assert api.runner.current() is None


def test_a_moving_printer_blocks_a_display_flash(api, screens):
    """Klipper gets stopped for this, so the same gate applies as any other
    flash - a display is not special enough to interrupt a QGL for."""
    api._call = _moonraker(screens, idle_state="Printing")
    with pytest.raises(RpcError) as exc:
        api.dispatch("fw.flash", {"name": ENV})
    assert exc.value.data["code"] == "print_in_progress"


# --------------------------------------------------------------------------
# the ordering that makes it work at all
# --------------------------------------------------------------------------


def test_the_screens_are_read_before_klipper_is_stopped(api, no_pio, monkeypatch):
    """The list comes from a *running* Klipper. Reading it after the stop would
    find nothing, and the batch would silently flash zero displays."""
    svc = NullService()
    monkeypatch.setattr("mcu_updater.service.make_controller", lambda *a, **k: svc)

    order: list[str] = []
    real_stop = svc.stop

    def watched_stop(*args, **kwargs):
        order.append("stopped")
        return real_stop(*args, **kwargs)

    monkeypatch.setattr(svc, "stop", watched_stop)

    original = api.device_list

    def watched_list(args):
        order.append("listed")
        return original(args)

    monkeypatch.setattr(api, "device_list", watched_list)

    res = api.dispatch("fw.flash", {"name": ENV})
    assert api.runner.wait(timeout=30)
    job = api.runner.get(res["job_id"])
    assert job.state == "succeeded", job.error

    assert order[:2] == ["listed", "stopped"]


def test_klipper_is_stopped_once_for_the_whole_batch(api, no_pio, monkeypatch):
    svc = NullService()
    monkeypatch.setattr("mcu_updater.service.make_controller", lambda *a, **k: svc)

    res = api.dispatch("fw.flash", {"name": ENV})
    assert api.runner.wait(timeout=30)
    job = api.runner.get(res["job_id"])

    assert job.state == "succeeded", job.error
    assert len(job.result["flashed"]) == 2
    assert svc.actions == ["stop", "start"]


# --------------------------------------------------------------------------
# ports and identity
# --------------------------------------------------------------------------


def test_every_upload_names_its_port(api, no_pio):
    """The guard that stops PlatformIO choosing between identical CH340s."""
    res = api.dispatch("fw.flash", {"name": ENV})
    assert api.runner.wait(timeout=30)
    assert api.runner.get(res["job_id"]).state == "succeeded"

    uploads = [c for c in no_pio if "upload" in c]
    assert len(uploads) == 2
    for cmd in uploads:
        assert "--upload-port" in cmd
        assert cmd[cmd.index("--upload-port") + 1].endswith("knomi_t0_knomi") or cmd[
            cmd.index("--upload-port") + 1
        ].endswith("knomi_t1_knomi")


def test_one_screen_can_be_singled_out(api, no_pio, screens):
    port = screens["knomi_serial t0_knomi"]["serial"]
    res = api.dispatch("fw.flash", {"name": ENV, "port": port})

    assert len(res["displays"]) == 1
    assert api.runner.wait(timeout=30)
    assert len(api.runner.get(res["job_id"]).result["flashed"]) == 1


def test_a_flash_result_says_nothing_about_which_board_it_wrote(api, no_pio, screens):
    """We are an updater, not an asset tracker. Identity is knomi_serial's, and
    it is resolved at flash time by discovery - so the result reports what was
    written and where, and keeps no history of which board lives on which port."""
    port = screens["knomi_serial t0_knomi"]["serial"]
    res = api.dispatch("fw.flash", {"name": ENV, "port": port})
    assert api.runner.wait(timeout=30)
    job = api.runner.get(res["job_id"])

    assert job.state == "succeeded", job.error
    assert "moved" not in job.result
    assert all("mac" not in f for f in job.result["flashed"])


def test_one_failing_screen_does_not_abandon_the_others(api, no_pio, monkeypatch, screens):
    """Same contract as every other batch here: report it and carry on."""
    first = screens["knomi_serial t0_knomi"]["serial"]

    def selective(cmd, **kwargs):
        if "upload" in cmd and first in cmd:
            return 3
        if kwargs.get("reporter") and "upload" in cmd:
            kwargs["reporter"]("stdout", "MAC: cc:ba:97:19:aa:38")
        return 0

    monkeypatch.setattr(pio_mod, "run_streamed", selective)

    res = api.dispatch("fw.flash", {"name": ENV})
    assert api.runner.wait(timeout=30)
    job = api.runner.get(res["job_id"])

    assert job.state == "succeeded", job.error
    assert len(job.result["failures"]) == 1
    assert len(job.result["flashed"]) == 1


# --------------------------------------------------------------------------
# building
# --------------------------------------------------------------------------


def test_a_build_touches_no_display_and_needs_no_flash_permission(
    paths, live_registry_text, fake_root, screens, no_pio
):
    """Compiling is safe, so it stays available with flashing switched off."""
    tree = fake_root / "knomi_serial"
    (tree / ".pio" / "build" / ENV).mkdir(parents=True, exist_ok=True)
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    write_settings(paths, dry_run="true", service_backend="null")
    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write(f"\n[type {ENV}]\nchipset: esp32\nfirmware: knomi_serial\nenv: {ENV}\n")

    runner = JobRunner(paths, lambda: __import__(
        "mcu_updater.settings", fromlist=["load_settings"]
    ).load_settings(paths.settings_file))
    agent = Api(paths, runner=runner, call=_moonraker(screens))

    caps = agent.dispatch("fw.ping")["capabilities"]
    assert "fw.display.build" in caps
    assert "fw.flash" not in caps

    res = agent.dispatch("fw.display.build", {"name": ENV})
    assert runner.wait(timeout=30)
    job = runner.get(res["job_id"])

    assert job.state == "succeeded", job.error
    assert job.result["env"] == ENV
    assert not any("upload" in cmd for cmd in no_pio)


# --------------------------------------------------------------------------
# the port watcher
#
# knomi_serial opens any port that appears and has not been identified yet, and
# pyserial's exclusive open is an advisory flock - so if a port turns up at the
# moment esptool wants it, one of them loses and the upload fails. Stopping the
# watcher removes the race. It is *not* klipper: the failure it prevents is a
# clean retryable one, not an unsafe write.
# --------------------------------------------------------------------------


def _services(order: list[str]) -> tuple[dict, object]:
    """A make_controller that hands out one service per unit name."""
    made: dict[str, NullService] = {}

    def factory(settings, *, call=None, name=None):
        unit = name or "klipper"
        svc = made.get(unit)
        if svc is None:
            svc = made[unit] = NullService(unit)
            for action in ("stop", "start"):
                def watched(*a, _s=svc, _a=action, **k):
                    order.append(f"{_a} {_s.name}")
                    return getattr(NullService, _a)(_s, *a, **k)

                setattr(svc, action, watched)
        return svc

    return made, factory


def test_the_watcher_stops_inside_the_klipper_stop(api, no_pio, monkeypatch):
    """The order knomi_serial's own docs give: klipper down, watcher down,
    upload, watcher up, klipper up. Klipper holds the port outright; the
    watcher only contends for it, so it is the inner pair."""
    order: list[str] = []
    _made, factory = _services(order)
    monkeypatch.setattr("mcu_updater.service.make_controller", factory)

    res = api.dispatch("fw.flash", {"name": ENV})
    assert api.runner.wait(timeout=30)
    assert api.runner.get(res["job_id"]).state == "succeeded"

    assert order == [
        "stop klipper",
        "stop knomi_serial",
        "start knomi_serial",
        "start klipper",
    ]


def test_the_watcher_never_touches_the_crash_journal(api, no_pio, monkeypatch):
    """The journal holds exactly one pending stop and record_stop overwrites.
    Recording the watcher would erase klipper's entry, so a crash would bring
    the watcher back and leave klipper down - the exact failure the journal
    exists to prevent."""
    recorded: list[str] = []
    monkeypatch.setattr(
        "mcu_updater.service.Journal.record_stop",
        lambda self, service, label: recorded.append(service),
    )
    _made, factory = _services([])
    monkeypatch.setattr("mcu_updater.service.make_controller", factory)

    res = api.dispatch("fw.flash", {"name": ENV})
    assert api.runner.wait(timeout=30)
    assert api.runner.get(res["job_id"]).state == "succeeded"

    assert recorded == ["klipper"]


def test_a_watcher_that_is_not_running_is_left_alone(api, no_pio, monkeypatch):
    """A host without the unit installed, or one where it is already stopped.
    systemctl is-active is false for a unit it has never heard of, so this is
    also the no-op for every install that does not use the watcher."""
    order: list[str] = []
    made, factory = _services(order)
    monkeypatch.setattr("mcu_updater.service.make_controller", factory)
    # Build it up front and mark it down, so the flash finds it inactive.
    factory(None, name="knomi_serial")._active = False

    res = api.dispatch("fw.flash", {"name": ENV})
    assert api.runner.wait(timeout=30)
    assert api.runner.get(res["job_id"]).state == "succeeded"

    assert order == ["stop klipper", "start klipper"]


def test_a_watcher_that_will_not_stop_does_not_abort_the_flash(api, no_pio, monkeypatch):
    """Unlike klipper, this one is never verified. The worst case is the flake
    it was meant to remove - a clean failure that retrying fixes - and refusing
    to flash at all would be worse than that."""
    made, factory = _services([])
    monkeypatch.setattr("mcu_updater.service.make_controller", factory)
    watcher = factory(None, name="knomi_serial")
    monkeypatch.setattr(watcher, "stop", lambda *a, **k: None)  # stays "active"

    res = api.dispatch("fw.flash", {"name": ENV})
    assert api.runner.wait(timeout=30)
    job = api.runner.get(res["job_id"])
    assert job.state == "succeeded", job.error
    assert len(job.result["flashed"]) == 2


def test_a_display_family_can_declare_it_has_no_watcher(api, paths, no_pio, monkeypatch):
    """`service:` with nothing after it. Absent means the default watcher;
    blank is how a family says there is nothing to pause."""
    from mcu_updater.cfgdoc import CfgDocument

    with open(paths.main_config, encoding="utf-8") as fh:
        doc = CfgDocument(fh.read())
    doc.set(f"type {ENV}", "service", "")
    with open(paths.main_config, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(doc.render())

    order: list[str] = []
    _made, factory = _services(order)
    monkeypatch.setattr("mcu_updater.service.make_controller", factory)

    res = api.dispatch("fw.flash", {"name": ENV})
    assert api.runner.wait(timeout=30)
    assert api.runner.get(res["job_id"]).state == "succeeded"

    assert order == ["stop klipper", "start klipper"]


# --------------------------------------------------------------------------
# resolving identity at flash time
#
# The screen list is read from Klipper *before* the stop, so its paths say
# where displays were. Once the ports are free the screens can be asked
# directly, which is the only moment identity is a fact rather than a memory.
# --------------------------------------------------------------------------


def screens_port(screens: dict, which: str) -> str:
    for section, values in screens.items():
        if which in section:
            return values["serial"]
    raise KeyError(which)


def _found(**by_id):
    from mcu_updater.providers.pio import WatcherDevice

    return {
        i: WatcherDevice(device_id=i, port=p, present=True) for i, p in by_id.items()
    }


def _with_ids(screens, **ids):
    """The live get_status half, giving each section a reported id."""
    return {f"knomi_serial {name}": {"reported_id": i} for name, i in ids.items()}


def test_a_screen_is_written_where_it_answered_not_where_it_was(
    api, paths, no_pio, screens, monkeypatch, fake_root
):
    """The move nothing else would notice: a display swapped sockets since
    Klipper last looked, so its remembered path now names a different screen."""
    moved_to = str(fake_root / "ttyUSB9")
    write_settings(paths, dry_run="false", enable_flashing="true", service_backend="null")
    monkeypatch.setattr(
        "mcu_updater.providers.pio.discover", lambda *a, **k: _found(aaa111=moved_to)
    )
    api._call = serve_klipper(display_objects(screens, _with_ids(screens, t0_knomi="aaa111")))

    ports: list[str] = []
    monkeypatch.setattr(
        "mcu_updater.providers.pio.upload",
        lambda p, s, d, port, **k: ports.append(port) or {"port": port, "chip": None},
    )

    res = api.dispatch("fw.flash", {"name": ENV, "port": screens_port(screens, "t0")})
    assert api.runner.wait(timeout=30)
    job = api.runner.get(res["job_id"])
    assert job.state == "succeeded", job.error

    assert ports == [moved_to], "wrote to where it is, not where it was"


def test_a_screen_that_does_not_answer_is_not_flashed_at_its_old_port(
    api, paths, no_pio, screens, monkeypatch, fake_root
):
    """The ports were free and something else answered, so this one is not
    there - and its old path now names whatever is on that path."""
    write_settings(paths, dry_run="false", enable_flashing="true", service_backend="null")
    monkeypatch.setattr(
        "mcu_updater.providers.pio.discover",
        lambda *a, **k: _found(somebodyelse=str(fake_root / "ttyUSB9")),
    )
    api._call = serve_klipper(display_objects(screens, _with_ids(screens, t0_knomi="aaa111")))

    ports: list[str] = []
    monkeypatch.setattr(
        "mcu_updater.providers.pio.upload",
        lambda p, s, d, port, **k: ports.append(port) or {"port": port, "chip": None},
    )

    res = api.dispatch("fw.flash", {"name": ENV, "port": screens_port(screens, "t0")})
    assert api.runner.wait(timeout=30)
    job = api.runner.get(res["job_id"])

    assert job.state == "succeeded", job.error
    assert ports == [], "nothing was written"
    assert len(job.result["failures"]) == 1
    assert "did not answer" in job.result["failures"][0]["error"]


def test_discovery_failing_falls_back_to_the_configured_ports(
    api, paths, no_pio, screens, monkeypatch
):
    """No pyserial, no source tree. Every flash worked this way before
    discovery existed, so degrading to it is what it used to do - refusing
    would be a new way to fail."""
    from mcu_updater.errors import ToolMissingError

    def boom(*a, **k):
        raise ToolMissingError("pyserial is not installed", tool="discover")

    write_settings(paths, dry_run="false", enable_flashing="true", service_backend="null")
    monkeypatch.setattr("mcu_updater.providers.pio.discover", boom)
    api._call = serve_klipper(display_objects(screens, _with_ids(screens, t0_knomi="aaa111")))

    ports: list[str] = []
    monkeypatch.setattr(
        "mcu_updater.providers.pio.upload",
        lambda p, s, d, port, **k: ports.append(port) or {"port": port, "chip": None},
    )

    want = screens_port(screens, "t0")
    res = api.dispatch("fw.flash", {"name": ENV, "port": want})
    assert api.runner.wait(timeout=30)
    assert api.runner.get(res["job_id"]).state == "succeeded"
    assert ports == [want]


def test_a_screen_with_no_hardware_id_is_still_flashed(
    api, paths, no_pio, screens, monkeypatch, fake_root
):
    """A `serial:` section names a socket, and its identity only arrives from
    the module's own report. A module too old to send one must not lose the
    ability to flash."""
    write_settings(paths, dry_run="false", enable_flashing="true", service_backend="null")
    monkeypatch.setattr(
        "mcu_updater.providers.pio.discover",
        lambda *a, **k: _found(somebodyelse=str(fake_root / "ttyUSB9")),
    )
    api._call = serve_klipper(display_objects(screens))  # no live fields at all

    ports: list[str] = []
    monkeypatch.setattr(
        "mcu_updater.providers.pio.upload",
        lambda p, s, d, port, **k: ports.append(port) or {"port": port, "chip": None},
    )

    want = screens_port(screens, "t0")
    res = api.dispatch("fw.flash", {"name": ENV, "port": want})
    assert api.runner.wait(timeout=30)
    assert api.runner.get(res["job_id"]).state == "succeeded"
    assert ports == [want]


def test_a_dry_run_never_opens_a_serial_port(api, paths, no_pio, screens, monkeypatch):
    """Discovery opens real ports. A rehearsal that touches hardware is not a
    rehearsal."""
    def boom(*a, **k):
        raise AssertionError("opened serial ports during a dry run")

    monkeypatch.setattr("mcu_updater.providers.pio.discover", boom)
    write_settings(paths, dry_run="true", enable_flashing="true", service_backend="null")
    api._call = serve_klipper(display_objects(screens, _with_ids(screens, t0_knomi="aaa111")))

    res = api.dispatch("fw.flash", {"name": ENV})
    assert api.runner.wait(timeout=30)
    assert api.runner.get(res["job_id"]).state == "succeeded"


# --------------------------------------------------------------------------
# a fleet flash reaches the screens
#
# The complaint this whole restructure came from: "Flash All does not include
# the pio boards only the kmake". Not an oversight in the loop - `flash_all`
# walked the `[mcu ...]` registry, so a screen could not be selected even in
# principle.
# --------------------------------------------------------------------------


def _built(api, paths, fake_root) -> None:
    """An image on disk for the display env, so there is something to write."""
    from mcu_updater.providers import pio as dm

    display = api.display_types()[ENV]
    path = pathlib.Path(dm.firmware_bin(display))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * 512)


def test_flash_all_selects_screens_beside_boards(api, paths, fake_root, screens):
    """Both kinds, one selection, one confirmation.

    `scope: all` because a screen's staleness is a version comparison against a
    source tree and these fixtures have neither - what is being asserted is that
    a screen can be *chosen* at all, which it previously could not be.
    """
    _built(api, paths, fake_root)

    res = api.dispatch("fw.flash_all", {"scope": "all"})
    api.runner.cancel(res["job_id"])
    api.runner.wait(timeout=30)

    assert [d["flasher"] for d in res["displays"]] == ["esptool", "esptool"]
    assert {d["id"] for d in res["displays"]} == {
        screens_port(screens, "t0"),
        screens_port(screens, "t1"),
    }
    assert all(d["reason"] == "forced" for d in res["displays"])


def test_a_display_with_nothing_built_is_not_selected(api, paths, screens):
    """Same rule as a board with no artifact: there is nothing to write, so
    including it would guarantee a failure partway through a batch that has
    already stopped Klipper."""
    with pytest.raises(RpcError) as exc:
        api.dispatch("fw.flash_all", {"scope": "all"})
    assert exc.value.data["code"] == "nothing_to_do"


def test_a_fleet_flash_writes_boards_and_screens_under_one_stop(
    api, paths, fake_root, no_pio, screens, monkeypatch
):
    """The point of grouping by requirement rather than by kind.

    Both need Klipper down - a board because *getting* to Katapult goes over the
    port Klipper holds, a screen because the klippy module holds it for the
    write - so one stop covers both and neither loop knows the other exists.
    """
    _built(api, paths, fake_root)
    svc = NullService()
    monkeypatch.setattr("mcu_updater.service.make_controller", lambda *a, **k: svc)

    res = api.dispatch("fw.flash_all", {"scope": "all"})
    assert api.runner.wait(timeout=60)
    job = api.runner.get(res["job_id"])

    assert job.state == "succeeded", job.error
    assert [f["flasher"] for f in job.result["flashed"]] == ["esptool", "esptool"]
    # Stopped once for the batch, not once per device.
    assert svc.actions == ["stop", "start"]


# --------------------------------------------------------------------------
# one method per operation, whichever build system owns the type
#
# `fw.display.build` and (formerly) `fw.display.flash` named a build system in
# the method, so a caller had to know which kind of thing it was addressing
# before it could pick one - which is the branching the Provider and Flasher
# seams removed everywhere else. `fw.build` and `fw.flash` route on the type's
# own provider. `fw.display.flash` retired once nothing called it (Step 14);
# `fw.display.build` stays registered: a panel built before this is still
# calling it.
# --------------------------------------------------------------------------


def test_the_generic_build_reaches_a_platformio_type(api, no_pio):
    """No `fw` argument. A PlatformIO env already names the board, the partition
    table and the flags, so there is no family axis to name one on."""
    res = api.dispatch("fw.build", {"name": ENV})
    assert api.runner.wait(timeout=30)
    job = api.runner.get(res["job_id"])

    assert job.state == "succeeded", job.error
    assert job.result["env"] == ENV


def test_the_generic_flash_reaches_a_platformio_type(api, no_pio, screens):
    res = api.dispatch("fw.flash", {"name": ENV})
    assert api.runner.wait(timeout=30)
    job = api.runner.get(res["job_id"])

    assert job.state == "succeeded", job.error
    assert len(job.result["flashed"]) == 2


def test_the_uniform_id_slot_pins_one_device(api, no_pio, screens):
    """`targets[].devices[].id` is a serial for a board and a port for a screen.
    A caller reading one off the wire must be able to hand it straight back."""
    port = screens["knomi_serial t0_knomi"]["serial"]
    res = api.dispatch("fw.flash", {"name": ENV, "id": port})
    assert api.runner.wait(timeout=30)
    job = api.runner.get(res["job_id"])

    assert job.state == "succeeded", job.error
    assert [f["port"] for f in job.result["flashed"]] == [port]


def test_the_old_method_names_still_answer(api, no_pio):
    """They are two lines each, and they are what a deployed panel calls."""
    assert "displays" in api.dispatch("fw.display.list")
    assert "displays" in api.dispatch("fw.device.list")

    res = api.dispatch("fw.display.build", {"name": ENV})
    assert api.runner.wait(timeout=30)
    assert api.runner.get(res["job_id"]).state == "succeeded"


def test_a_name_belonging_to_no_type_is_refused_rather_than_guessed(api, no_pio):
    """Defaulting an unknown name to kconfig would answer a mistyped screen with
    "no saved klipper config", which sends somebody to run menuconfig for a
    display."""
    with pytest.raises(RpcError) as exc:
        api.dispatch("fw.build", {"name": "not_a_type"})
    assert exc.value.data["code"] == "unknown_type"
