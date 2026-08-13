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

import pytest

from mcu_updater import displays as displays_mod
from mcu_updater.agent.methods import Api
from mcu_updater.agent.rpc import RpcError
from mcu_updater.jobs import JobRunner
from mcu_updater.service import NullService

from .conftest import write_settings

ENV = "knomi_toolchanger"


def _moonraker(sections: dict, print_state="standby", idle_state="Ready"):
    def call(method, params, timeout):
        if method == "printer.objects.query":
            requested = (params or {}).get("objects") or {}
            status: dict = {}
            if "configfile" in requested:
                status["configfile"] = {"settings": sections}
            if "print_stats" in requested:
                status["print_stats"] = {"state": print_state}
            if "idle_timeout" in requested:
                status["idle_timeout"] = {"state": idle_state}
            return {"status": status}
        if method == "printer.info":
            return {"state": "ready", "state_message": "klippy is ready"}
        if method == "machine.system_info":
            return {"system_info": {"service_state": {"klipper": {"active_state": "active"}}}}
        return {}

    return call


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
    tree = fake_root / "knomi_serial"
    (tree / ".pio" / "build" / ENV).mkdir(parents=True)

    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    write_settings(paths, dry_run="true", service_backend="null", enable_flashing="true")
    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write(f"\n[display {ENV}]\nsource: {tree}\n")

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

    monkeypatch.setattr(displays_mod, "run_streamed", fake)
    monkeypatch.setattr(displays_mod, "find_pio", lambda s: "/usr/bin/pio")
    return calls


# --------------------------------------------------------------------------
# gates
# --------------------------------------------------------------------------


def test_flashing_displays_needs_it_enabled(paths, live_registry_text, fake_root, screens):
    write_settings(paths, dry_run="true", service_backend="null")
    with open(paths.registry_file, "a", encoding="utf-8") as fh:
        fh.write(f"\n[display {ENV}]\nsource: {fake_root}\n")
    runner = JobRunner(paths, lambda: __import__(
        "mcu_updater.settings", fromlist=["load_settings"]
    ).load_settings(paths.settings_file))

    with pytest.raises(RpcError) as exc:
        Api(paths, runner=runner, call=_moonraker(screens)).display_flash({"name": ENV})
    assert exc.value.data["code"] == "flashing_disabled"


def test_an_unknown_display_type_fails_before_a_job(api):
    with pytest.raises(RpcError) as exc:
        api.dispatch("fw.display.flash", {"name": "nosuchscreen"})
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
        api.dispatch("fw.display.flash", {"name": ENV})
    assert exc.value.data["code"] == "nothing_to_do"
    assert api.runner.current() is None


def test_a_moving_printer_blocks_a_display_flash(api, screens):
    """Klipper gets stopped for this, so the same gate applies as any other
    flash - a display is not special enough to interrupt a QGL for."""
    api._call = _moonraker(screens, idle_state="Printing")
    with pytest.raises(RpcError) as exc:
        api.dispatch("fw.display.flash", {"name": ENV})
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

    original = api.display_list

    def watched_list(args):
        order.append("listed")
        return original(args)

    monkeypatch.setattr(api, "display_list", watched_list)

    res = api.dispatch("fw.display.flash", {"name": ENV})
    assert api.runner.wait(timeout=30)
    job = api.runner.get(res["job_id"])
    assert job.state == "succeeded", job.error

    assert order[:2] == ["listed", "stopped"]


def test_klipper_is_stopped_once_for_the_whole_batch(api, no_pio, monkeypatch):
    svc = NullService()
    monkeypatch.setattr("mcu_updater.service.make_controller", lambda *a, **k: svc)

    res = api.dispatch("fw.display.flash", {"name": ENV})
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
    res = api.dispatch("fw.display.flash", {"name": ENV})
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
    res = api.dispatch("fw.display.flash", {"name": ENV, "port": port})

    assert len(res["displays"]) == 1
    assert api.runner.wait(timeout=30)
    assert len(api.runner.get(res["job_id"]).result["flashed"]) == 1


def test_a_display_that_moved_is_reported_but_not_fatal(api, no_pio, paths, screens):
    """A different MAC answering on a port means something was re-cabled. The
    write still succeeded, so it warns rather than failing - but nothing else in
    the system would ever notice."""
    port = screens["knomi_serial t0_knomi"]["serial"]
    displays_mod.record_mac(paths, port, "aa:bb:cc:dd:ee:ff", ENV)

    res = api.dispatch("fw.display.flash", {"name": ENV, "port": port})
    assert api.runner.wait(timeout=30)
    job = api.runner.get(res["job_id"])

    assert job.state == "succeeded", job.error
    assert len(job.result["moved"]) == 1
    assert job.result["moved"][0]["was"] == "aa:bb:cc:dd:ee:ff"


def test_a_first_flash_reports_no_movement(api, no_pio):
    res = api.dispatch("fw.display.flash", {"name": ENV})
    assert api.runner.wait(timeout=30)
    assert api.runner.get(res["job_id"]).result["moved"] == []


def test_one_failing_screen_does_not_abandon_the_others(api, no_pio, monkeypatch, screens):
    """Same contract as every other batch here: report it and carry on."""
    first = screens["knomi_serial t0_knomi"]["serial"]

    def selective(cmd, **kwargs):
        if "upload" in cmd and first in cmd:
            return 3
        if kwargs.get("reporter") and "upload" in cmd:
            kwargs["reporter"]("stdout", "MAC: cc:ba:97:19:aa:38")
        return 0

    monkeypatch.setattr(displays_mod, "run_streamed", selective)

    res = api.dispatch("fw.display.flash", {"name": ENV})
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
        fh.write(f"\n[display {ENV}]\nsource: {tree}\n")

    runner = JobRunner(paths, lambda: __import__(
        "mcu_updater.settings", fromlist=["load_settings"]
    ).load_settings(paths.settings_file))
    agent = Api(paths, runner=runner, call=_moonraker(screens))

    caps = agent.dispatch("fw.ping")["capabilities"]
    assert "fw.display.build" in caps
    assert "fw.display.flash" not in caps

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

    res = api.dispatch("fw.display.flash", {"name": ENV})
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

    res = api.dispatch("fw.display.flash", {"name": ENV})
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

    res = api.dispatch("fw.display.flash", {"name": ENV})
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

    res = api.dispatch("fw.display.flash", {"name": ENV})
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
    doc.set(f"display {ENV}", "service", "")
    with open(paths.main_config, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(doc.render())

    order: list[str] = []
    _made, factory = _services(order)
    monkeypatch.setattr("mcu_updater.service.make_controller", factory)

    res = api.dispatch("fw.display.flash", {"name": ENV})
    assert api.runner.wait(timeout=30)
    assert api.runner.get(res["job_id"]).state == "succeeded"

    assert order == ["stop klipper", "start klipper"]
