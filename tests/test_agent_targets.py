"""`fw.status.targets` - MCU types and displays said in one shape.

The panel needs a component per wire shape, and there are two of them saying
overlapping things in different words. `targets[]` is those two projected onto
one shape, so one component renders both - and renders whatever comes next
without being taught to.

**It is a projection, not a second source of truth.** The load-bearing test in
this file is `test_every_fact_in_the_old_keys_survives_the_projection`: if a fact
lives in `types[]` or `displays[]` and cannot be found here, that is a bug in the
projection rather than a reason to add a key. Two wire shapes kept in step by
hand is exactly what this exists to stop.
"""

from __future__ import annotations

import os

import pytest

from mcu_updater.agent.methods import Api
from mcu_updater.states import (
    TONE_ATTENTION,
    TONE_UNKNOWN,
    ArtifactStatus,
    DeviceStatus,
)

from .conftest import display_objects, make_device, serve_klipper, write_settings

ENV = "knomi_toolchanger"


@pytest.fixture
def api(paths, live_registry_text):
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    return Api(paths)


def _targets(api, kind=None):
    out = api.dispatch("fw.status")["targets"]
    return {t["name"]: t for t in out if kind is None or t["kind"] == kind}


def _add_display(paths, fake_root, api):
    """A `[display ...]` section plus a screen Klipper reports."""
    port = fake_root / "knomi_t0"
    port.write_text("", encoding="utf-8")
    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write(f"\n[display {ENV}]\nsource: {fake_root}\n")
    api._call = serve_klipper(
        display_objects({"knomi_serial t0_knomi": {"serial": str(port)}}),
        reachable=True,
    )
    return str(port)


# --------------------------------------------------------------------------
# the shape
# --------------------------------------------------------------------------


def test_an_mcu_type_projects_onto_the_shared_shape(api):
    ebb = _targets(api)["bttebb36"]

    assert ebb["provider"] == "kconfig_make"
    # The old two-word discriminator, still sent for a panel that switches on it.
    assert ebb["kind"] == "mcu"
    assert ebb["descriptor"] == "stm32g0b1xx"
    assert ebb["firmware"] == "klipper"
    assert set(ebb) == {
        "provider",
        "kind",
        "name",
        "descriptor",
        "firmware",
        "artifact",
        "profile",
        "needs_flash",
        "devices",
        "actions",
    }
    assert set(ebb["artifact"]) == {"state", "tone", "label", "reason"}
    assert set(ebb["devices"][0]) == {
        "id",
        "name",
        "present",
        "state",
        "path",
        "version",
        "needs_flash",
        "tone",
        "label",
        "reason",
        "actions",
    }


def test_a_display_projects_onto_the_same_shape(api, paths, fake_root):
    _add_display(paths, fake_root, api)
    display = _targets(api, "display")[ENV]

    mcu_keys = set(_targets(api, "mcu")["bttebb36"])
    # The whole point: a display carries everything an MCU does, plus a bag of
    # things only a screen has. A reader that never opens `extra` renders both.
    assert set(display) == mcu_keys | {"extra"}
    assert set(display["devices"][0]) == set(
        _targets(api, "mcu")["bttebb36"]["devices"][0]
    )
    assert display["kind"] == "display"
    assert display["descriptor"] == ENV


def test_a_display_build_is_blocked_by_a_missing_source_tree(api, paths, fake_root):
    """The preview and the batch have to agree about what will happen.

    A fleet build skips a display with no tree to build in, exactly as it skips
    an MCU type that has never been through menuconfig. The button offering that
    build has to say so - a panel naming work the agent will pass over is how a
    screen sits a month behind while the UI reports a clean run.

    Both come from the same function in the PlatformIO provider, so they cannot
    drift into disagreeing.
    """
    port = fake_root / "knomi_t0"
    port.write_text("", encoding="utf-8")
    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write(f"\n[display {ENV}]\nsource: /nope/not/here\n")
    api = Api(
        paths,
        runner=_runner(),
        call=serve_klipper(
            display_objects({"knomi_serial t0_knomi": {"serial": str(port)}}),
            reachable=True,
        ),
    )

    build = _action(_targets(api, "display")[ENV], "build")

    assert build["blocked"]["code"] == Api.BLOCKED_NO_SOURCE
    assert "not found" in build["blocked"]["message"]


def test_a_display_with_its_tree_can_be_built(api, paths, fake_root):
    """The other half: a block that never clears is a disabled button."""
    _add_display(paths, fake_root, api)
    api = Api(paths, runner=_runner(), call=api._call)
    assert _action(_targets(api, "display")[ENV], "build")["blocked"] is None


def test_a_display_has_no_firmware_family_and_says_so(api, paths, fake_root):
    """PlatformIO builds from its own tree, not from a `[firmware ...]` family.

    None rather than a guess: naming klipper here is exactly the reflex that put
    a cartographer type's artifact under `artifacts.klipper`.
    """
    _add_display(paths, fake_root, api)
    assert _targets(api, "display")[ENV]["firmware"] is None


# --------------------------------------------------------------------------
# the verdicts
# --------------------------------------------------------------------------


def test_the_verdict_carries_its_own_wording(api):
    """Four colour maps and four sets of wording grew up in the panel deriving
    this from raw reason codes. One vocabulary means one wording."""
    art = _targets(api)["bttebb36"]["artifact"]

    assert art["reason"] == "never_built"
    assert art["state"] == "absent"
    assert art["tone"] == TONE_ATTENTION
    assert art["label"] == ArtifactStatus("never_built").label


def test_every_artifact_reason_survives_to_the_target(api, paths, monkeypatch):
    """`stale_reason` collapses `no_provenance` onto `never_built` because that
    string is a documented API value. The projection must not inherit the
    collapse - "you have never built this" and "somebody rebuilt behind you"
    want different words."""
    import mcu_updater.build as build_mod

    for reason in (None, "never_built", "config_changed", "source_changed",
                   "built_dirty", "foreign_build", "no_provenance"):
        monkeypatch.setattr(
            build_mod, "artifact_status", lambda *a, _r=reason, **k: ArtifactStatus(_r)
        )
        assert _targets(api)["bttebb36"]["artifact"]["reason"] == reason


def test_an_unbuilt_binary_with_no_sidecar_is_not_never_built(api, paths):
    """The one case the legacy string genuinely cannot express."""
    os.makedirs(os.path.dirname(paths.bin_file("bttebb36", "klipper")), exist_ok=True)
    with open(paths.bin_file("bttebb36", "klipper"), "wb") as fh:
        fh.write(b"\x00")

    status = api.dispatch("fw.status")
    legacy = {t["name"]: t for t in status["types"]}["bttebb36"]
    target = {t["name"]: t for t in status["targets"]}["bttebb36"]

    assert legacy["artifacts"]["klipper"]["stale_reason"] == "never_built"
    assert target["artifact"]["reason"] == "no_provenance"
    assert target["artifact"]["state"] == "unprovable"
    assert target["artifact"]["tone"] == TONE_UNKNOWN


def test_an_offline_board_is_never_reported_as_up_to_date(api):
    board = _targets(api)["bttmmbv1"]["devices"][0]

    assert board["present"] is False
    assert board["needs_flash"] is None
    assert board["tone"] == TONE_UNKNOWN
    assert board["reason"] == "offline"


def test_a_type_whose_boards_are_all_offline_reports_unknown_not_clean(api):
    """`any()` reads None as falsey, so the old aggregate reported "nothing to
    do" about a fleet nobody could see."""
    assert _targets(api)["bttmmbv1"]["needs_flash"] is None


def test_a_type_needs_flashing_when_any_one_board_does(api, fake_root):
    make_device(fake_root / "bus", "katapult", "stm32f103xe", "36FFD9054755303923891357-if00")
    sv08 = _targets(api)["sv08Mainboard"]

    assert sv08["needs_flash"] is True
    waiting = [d for d in sv08["devices"] if d["reason"] == "in_bootloader"]
    assert waiting and waiting[0]["tone"] == TONE_ATTENTION


def test_a_screen_that_cannot_be_reached_is_offline_not_current(api, paths, fake_root):
    """A port that does not resolve says nothing about the firmware on the far
    end, and the klippy module swallows the failure entirely."""
    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write(f"\n[display {ENV}]\nsource: {fake_root}\n")
    api._call = serve_klipper(
        display_objects(
            {"knomi_serial t0_knomi": {"serial": str(fake_root / "gone")}}
        ),
        reachable=True,
    )
    screen = _targets(api, "display")[ENV]["devices"][0]

    assert screen["present"] is False
    assert screen["state"] == "missing"
    assert screen["needs_flash"] is None
    assert screen["reason"] == "offline"


def test_a_protocol_mismatch_outranks_the_version_comparison(api, paths, fake_root):
    """Two independent reasons to reflash, and the version check has no word for
    the first: a screen can be on the right commit and still be unable to talk
    to the module."""
    port = fake_root / "knomi_t0"
    port.write_text("", encoding="utf-8")
    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write(f"\n[display {ENV}]\nsource: {fake_root}\n")
    api._call = serve_klipper(
        display_objects(
            {"knomi_serial t0_knomi": {"serial": str(port)}},
            {"knomi_serial t0_knomi": {"protocol_match": False, "device_online": True}},
        ),
        reachable=True,
    )
    screen = _targets(api, "display")[ENV]["devices"][0]

    assert screen["reason"] == "protocol_mismatch"
    assert screen["needs_flash"] is True
    assert screen["label"] == DeviceStatus("protocol_mismatch").label


# --------------------------------------------------------------------------
# actions
# --------------------------------------------------------------------------


def test_a_read_only_agent_offers_no_actions_at_all(api):
    """A capability is the presence of an action. Nothing here to grey out,
    because nothing here is offered."""
    assert _targets(api)["bttebb36"]["actions"] == []


def test_build_names_the_family_the_type_actually_runs(paths, live_registry_text):
    """The bug this key exists to kill: a cartographer type whose Build button
    compiles upstream klipper into the wrong tree."""
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
        fh.write("\n[mcu carto_v4]\nchipset: stm32g431xx\nfirmware: cartographer\n")
    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write("\n[firmware cartographer]\nsource: ~/carto\nartifact: klipper\n")
    api = Api(paths, runner=_runner())

    carto = _targets(api)["carto_v4"]
    build = _action(carto, "build")

    assert carto["firmware"] == "cartographer"
    assert build["params"] == {"name": "carto_v4", "fw": "cartographer"}


def test_a_device_carries_its_own_flash_call(paths, live_registry_text):
    """Flashing one board and flashing one screen are different RPCs. Putting
    each on its device is what lets a reader render both rows with one piece of
    code instead of switching on `kind` at the last moment."""
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    write_settings(paths, enable_flashing="true")
    api = Api(paths, runner=_runner())

    device = _targets(api)["bttmmbv1"]["devices"][0]
    flash = _action(device, "flash")

    assert flash["method"] == "fw.flash"
    assert flash["params"] == {"name": "bttmmbv1", "serial": device["id"]}
    # Offline, and nothing built either - the artifact is the first thing to
    # fix, so that is what it says.
    assert flash["blocked"]["code"] == Api.BLOCKED_NO_ARTIFACT


def test_a_screen_carries_the_display_flash_call_pinned_to_its_port(
    api, paths, fake_root
):
    """A port is never inferred: every screen of a type is an identical CH340,
    and PlatformIO's auto-detect was seen picking between two of them."""
    write_settings(paths, enable_flashing="true")
    port = _add_display(paths, fake_root, api)
    api = Api(paths, runner=_runner(), call=api._call)

    device = _targets(api, "display")[ENV]["devices"][0]
    flash = _action(device, "flash")

    assert flash["method"] == "fw.display.flash"
    assert flash["params"] == {"name": ENV, "port": port}


def test_untrack_is_offered_per_board_and_never_for_a_screen(
    paths, live_registry_text, fake_root
):
    """A screen is not in our registry at all - it is Klipper's, named by
    `[knomi_serial ...]` - so there is nothing to stop tracking."""
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    api = Api(paths, runner=_runner())
    _add_display(paths, fake_root, api)

    board = _targets(api)["bttmmbv1"]["devices"][0]
    untrack = _action(board, "untrack")
    assert untrack["method"] == "fw.serial.remove"
    assert untrack["params"] == {"name": "bttmmbv1", "serial": board["id"]}

    screen = _targets(api, "display")[ENV]["devices"][0]
    assert _action(screen, "untrack") is None


def test_the_artifact_shown_is_the_one_this_type_would_flash(paths, live_registry_text):
    """A cartographer type carries klipper config keys it will never use, so
    `artifacts` has a `klipper` entry that stays "never built" forever. Reading
    that one is what makes the panel say a perfectly good probe was never built,
    and disable its flash button for good."""
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
        fh.write("\n[mcu carto_v4]\nchipset: stm32g431xx\nfirmware: cartographer\n")
        fh.write("\n[firmware cartographer]\nsource: ~/carto\nartifact: klipper\n")
    binary = paths.bin_file("carto_v4", "cartographer")
    os.makedirs(os.path.dirname(binary), exist_ok=True)
    with open(binary, "wb") as fh:
        fh.write(b"\x00")
    api = Api(paths)

    status = api.dispatch("fw.status")
    legacy = {t["name"]: t for t in status["types"]}["carto_v4"]
    target = {t["name"]: t for t in status["targets"]}["carto_v4"]

    assert legacy["artifacts"]["klipper"]["stale_reason"] == "never_built"
    assert legacy["artifacts"]["cartographer"]["has_bin"] is True
    assert target["artifact"]["reason"] == "no_provenance"


def test_an_action_carries_the_call_the_panel_would_make(paths, live_registry_text):
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    api = Api(paths, runner=_runner())

    build = _action(_targets(api)["bttebb36"], "build")
    assert build["method"] == "fw.build"
    assert set(build) == {"id", "label", "method", "params", "blocked"}


def test_flash_is_absent_until_flashing_is_enabled(paths, live_registry_text):
    """Not greyed out - absent. `enable_flashing` is off by default and the
    agent does not advertise the method, so there is no control to offer."""
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    api = Api(paths, runner=_runner())

    assert _action(_targets(api)["bttebb36"], "flash") is None

    write_settings(paths, enable_flashing="true")
    assert _action(_targets(api)["bttebb36"], "flash") is not None


def test_flash_is_blocked_rather_than_hidden_with_nothing_built(paths, live_registry_text):
    """A requirement is only ever visible as `blocked`. Hiding the button says
    the printer cannot do this; blocking it says what to do first."""
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    write_settings(paths, enable_flashing="true")
    api = Api(paths, runner=_runner())

    blocked = _action(_targets(api)["bttebb36"], "flash")["blocked"]
    assert blocked["code"] == Api.BLOCKED_NO_ARTIFACT
    assert set(blocked) == {"code", "message", "data"}


def test_flash_is_blocked_with_something_built_but_nothing_connected(
    paths, live_registry_text
):
    """The other half of the precondition, and a different thing to tell the
    user: "press build" versus "plug the board in". Every board of this type is
    offline in the fixture."""
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    write_settings(paths, enable_flashing="true")
    binary = paths.bin_file("bttmmbv1", "klipper")
    os.makedirs(os.path.dirname(binary), exist_ok=True)
    with open(binary, "wb") as fh:
        fh.write(b"\x00")
    api = Api(paths, runner=_runner())

    target = _targets(api)["bttmmbv1"]
    assert all(d["present"] is False for d in target["devices"])
    assert _action(target, "flash")["blocked"]["code"] == Api.BLOCKED_NO_DEVICE
    # Build-and-flash has nowhere to write either, and says the same thing.
    assert _action(target, "update")["blocked"]["code"] == Api.BLOCKED_NO_DEVICE
    # And per device, which is where the reason can differ between two boards
    # of one type: naming the board is what makes it actionable.
    per_device = _action(target["devices"][0], "flash")["blocked"]
    assert per_device["code"] == Api.BLOCKED_NO_DEVICE
    assert target["devices"][0]["id"] in per_device["message"]


def test_build_is_blocked_without_saved_menuconfig_answers(paths, live_registry_text):
    """menuconfig needs a TTY, so the agent cannot resolve this itself.

    Upstream Klipper ships no profiles, which is the correct answer for a tree
    that builds for two hundred boards - so this message and this code are what
    the common case must keep saying, unchanged by anything profiles added.
    """
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    api = Api(paths, runner=_runner())

    blocked = _action(_targets(api)["bttebb36"], "build")["blocked"]
    assert blocked["code"] == Api.BLOCKED_NO_CONFIG
    assert blocked["message"] == (
        "'bttebb36' has no saved klipper configuration yet. Run menuconfig for it first."
    )
    assert "profile" not in _ids(_targets(api)["bttebb36"])

    os.makedirs(paths.type_dir("bttebb36"), exist_ok=True)
    with open(paths.config_file("bttebb36", "klipper"), "w", encoding="utf-8") as fh:
        fh.write("CONFIG_MACH_STM32=y\n")
    assert _action(_targets(api)["bttebb36"], "build")["blocked"] is None


def _ships_seeds(paths, *names: str) -> None:
    """Give the klipper tree vendor answer files, as a fork's root has."""
    for name in names or ("config.BoardUSB", "config.BoardCAN"):
        with open(os.path.join(paths.fw_dir("klipper"), name), "w", encoding="utf-8") as fh:
            fh.write("CONFIG_MACH_STM32=y\n")
            fh.write(f"CONFIG_BOARD_NAME=\"{name}\"\n")


def test_a_tree_shipping_profiles_offers_one_instead_of_menuconfig(
    paths, live_registry_text
):
    """The visibly broken thing this phase exists for: a blocked Build saying
    "run menuconfig", in front of a tree that already ships the answers."""
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    _ships_seeds(paths)
    api = Api(paths, runner=_runner())

    target = _targets(api)["bttebb36"]
    assert _action(target, "build")["blocked"]["code"] == Api.BLOCKED_NO_PROFILE

    picker = _action(target, "profile")
    assert picker["method"] == "fw.profile.apply"
    assert picker["label"] == "Choose profile"
    # The options are fetched when the dialog opens rather than carried on every
    # status poll - naming them costs a Kconfig parse, and a click can afford it.
    assert picker["choices"] == {
        "method": "fw.profile.list",
        "params": {"name": "bttebb36", "fw": "klipper", "detail": True},
        "param": "profile",
    }
    # Dissuaded, never blocked.
    assert "configure:klipper" in _ids(target) or not api.kconfig_available()["klipper"]


def test_a_customised_target_says_what_it_changed_and_how_to_go_back(
    paths, live_registry_text
):
    from mcu_updater import profiles

    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    _ships_seeds(paths)
    os.makedirs(paths.type_dir("bttebb36"), exist_ok=True)
    with open(paths.config_file("bttebb36", "klipper"), "w", encoding="utf-8") as fh:
        fh.write("CONFIG_MACH_STM32=y\n")
    profiles.write_record(
        paths,
        "bttebb36",
        "klipper",
        profiles.SeedResult(
            type="bttebb36",
            fw="klipper",
            profile="config.BoardUSB",
            config_path=paths.config_file("bttebb36", "klipper"),
            answers=['CONFIG_BOARD_NAME="config.BoardUSB"'],
            config_sha256="not-what-is-on-disk",
        ),
    )
    profiles.capture_custom(
        paths,
        "bttebb36",
        "klipper",
        answers=['CONFIG_BOARD_NAME="mine"'],
        parent="config.BoardUSB",
    )
    api = Api(paths, runner=_runner())

    target = _targets(api)["bttebb36"]
    assert target["profile"]["reason"] == profiles.CUSTOMISED
    # Your own answers are a destination, not drift.
    assert target["profile"]["tone"] == "ok"
    assert [row["symbol"] for row in target["profile"]["changes"]] == ["BOARD_NAME"]

    back = _action(target, "profile:revert")
    assert back["label"] == "Back to config.BoardUSB"
    assert back["params"]["profile"] == "config.BoardUSB"
    # Force, because the config being replaced is the user's - and it is only
    # offerable at all because those answers were kept first.
    assert back["params"]["force"] is True


def test_a_target_with_nothing_to_seed_from_carries_no_profile_actions(
    paths, live_registry_text
):
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    api = Api(paths, runner=_runner())

    target = _targets(api)["bttebb36"]
    assert _ids(target) & {"profile", "profile:revert"} == set()
    assert target["profile"]["reason"] == "unmanaged"


def test_build_and_flash_is_not_blocked_by_a_missing_artifact(paths, live_registry_text):
    """It builds one. Refusing it for the reason a plain flash is refused would
    make the composed operation useless in exactly the case it is for."""
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    write_settings(paths, enable_flashing="true")
    make_device(
        _bus(paths), "klipper", "stm32f103xe", "36FFD9054755303923891357-if00"
    )
    api = Api(paths, runner=_runner())

    sv08 = _targets(api)["sv08Mainboard"]
    assert _action(sv08, "flash")["blocked"]["code"] == Api.BLOCKED_NO_ARTIFACT
    assert _action(sv08, "update")["blocked"] is None


def test_configure_is_offered_per_family_not_as_a_fixed_pair(paths, live_registry_text, monkeypatch):
    """The panel's literal `['klipper', 'katapult']` cannot grow a third."""
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
        fh.write("\n[mcu carto_v4]\nchipset: stm32g431xx\nfirmware: cartographer\n")
        fh.write("\n[firmware cartographer]\nsource: ~/carto\nartifact: klipper\n")
    api = Api(paths, runner=_runner())
    monkeypatch.setattr(Api, "kconfig_available", lambda self, families=None: {
        "klipper": True, "katapult": True, "cartographer": True
    })

    assert _ids(_targets(api)["bttebb36"]) & {"configure:klipper", "configure:katapult"} == {
        "configure:klipper",
        "configure:katapult",
    }
    # Its own application family, and katapult - never klipper, which this board
    # carries config keys for and will never run.
    carto = _ids(_targets(api)["carto_v4"])
    assert "configure:cartographer" in carto
    assert "configure:klipper" not in carto


def test_configure_is_absent_where_the_source_tree_is_not_checked_out(paths, live_registry_text):
    """A stat per tree decides this, so the button is never offered on a host
    that would fail the call."""
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    api = Api(paths, runner=_runner())

    assert not {a for a in _ids(_targets(api)["bttebb36"]) if a.startswith("configure:")}


# --------------------------------------------------------------------------
# the projection is a projection
# --------------------------------------------------------------------------


def test_every_fact_in_the_old_keys_survives_the_projection(api, paths, fake_root):
    """The criterion for retiring `types[]`/`displays[]` one day.

    Deliberately checks identity of the *facts*, not of the wording: the point
    of the projection is that it says the same things, not that it says them the
    same way.
    """
    port = _add_display(paths, fake_root, api)
    make_device(
        fake_root / "bus", "klipper", "stm32f103xe", "36FFD9054755303923891357-if00"
    )

    status = api.dispatch("fw.status")
    targets = {t["name"]: t for t in status["targets"]}

    assert set(targets) == {t["name"] for t in status["types"]} | {
        d["name"] for d in status["displays"]
    }

    for legacy in status["types"]:
        target = targets[legacy["name"]]
        assert target["descriptor"] == legacy["chipset"]
        assert target["firmware"] == legacy["firmware"]
        assert target["needs_flash"] in (legacy["needs_flash"], True, None)
        assert [d["id"] for d in target["devices"]] == [
            s["serial"] for s in legacy["serials"]
        ]
        for device, serial in zip(target["devices"], legacy["serials"]):
            assert device["needs_flash"] == serial["needs_flash"]
            assert device["reason"] == serial["reason"]
            assert device["state"] == serial["state"]
            assert device["version"] == serial["running_version"]
            assert device["name"] == serial["mcu"]

    for legacy in status["displays"]:
        target = targets[legacy["name"]]
        assert target["descriptor"] == legacy["env"]
        assert [d["id"] for d in target["devices"]] == [
            s["configured_path"] for s in legacy["screens"]
        ]
        assert target["extra"]["module_version"] == legacy["module_version"]
        assert target["extra"]["source_version"] == legacy["source_version"]
        assert target["extra"]["reachable"] == legacy["reachable"]
        for device, screen in zip(target["devices"], legacy["screens"]):
            assert device["present"] == screen["present"]
            assert device["version"] == screen["firmware_version"]
            assert device["name"] == screen["section"]
    assert targets[ENV]["devices"][0]["id"] == port


def test_the_legacy_keys_are_unchanged_by_the_projection_existing(api):
    """`targets[]` is additive. A panel that has never heard of it must see
    exactly what it saw before."""
    ebb = {t["name"]: t for t in api.dispatch("fw.status")["types"]}["bttebb36"]

    assert set(ebb["serials"][0]) == {
        "serial",
        "state",
        "path",
        "mcu",
        "running_version",
        "running_sha",
        "needs_flash",
        "reason",
    }
    assert ebb["artifacts"]["klipper"]["stale_reason"] == "never_built"


def test_a_printer_with_no_screens_has_no_display_targets(api):
    """The whole feature costs nothing when unconfigured - not even the query."""
    assert _targets(api, "display") == {}


# --------------------------------------------------------------------------
# fw.status.firmware_families
# --------------------------------------------------------------------------


def test_firmware_families_says_what_exists_not_just_what_parses(api, paths):
    """The panel has been using `kconfig_available`'s keys as a family list.
    That works by accident: its values mean "has a parseable Kconfig", so a
    declared family whose tree is not cloned yet reads as absent rather than as
    present-and-not-ready."""
    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write("\n[firmware cartographer]\nsource: ~/nowhere-at-all\n")

    families = {f["name"]: f for f in api.dispatch("fw.status")["firmware_families"]}

    assert set(families) == {"klipper", "katapult", "cartographer"}
    assert families["cartographer"]["present"] is False
    assert families["cartographer"]["configurable"] is False
    assert families["cartographer"]["builtin"] is False
    assert families["klipper"]["builtin"] is True


def test_firmware_families_carries_builder_and_bootloader(api):
    families = {f["name"]: f for f in api.dispatch("fw.status")["firmware_families"]}

    assert families["klipper"]["builder"] == "kconfig_make"
    assert families["klipper"]["bootloader"] is False
    assert families["katapult"]["bootloader"] is True


def test_firmware_families_keeps_the_builtins_first(api):
    """Same order the CLI has always listed and the artifacts payload carries."""
    names = [f["name"] for f in api.dispatch("fw.status")["firmware_families"]]
    assert names[:2] == ["klipper", "katapult"]


def test_a_type_says_which_family_it_runs(api):
    """Absent until now, which left every consumer assuming klipper."""
    assert _targets(api)["bttebb36"]["firmware"] == "klipper"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _bus(paths):
    import pathlib

    return pathlib.Path(paths.serial_by_id)


def _runner():
    """A job runner stub. Its only job here is to make the agent advertise the
    job methods, which is what puts actions on a target at all."""

    class _Runner:
        def current(self):
            return None

        def recent(self, _n):
            return []

    return _Runner()


def _action(target, action_id):
    return next((a for a in target["actions"] if a["id"] == action_id), None)


def _ids(target):
    return {a["id"] for a in target["actions"]}


# --------------------------------------------------------------------------
# declaring a type before its hardware exists
# --------------------------------------------------------------------------


def test_a_type_can_be_declared_with_nothing_plugged_in(paths, live_registry_text):
    """A type describes a *model*, not a board on the bus.

    The agent has always allowed this; the panel offered it only from a device
    it could already see, which made a probe still in the post unreachable. This
    is the order the work actually happens in: declare the type, run menuconfig,
    build, then plug the board in and adopt it.
    """
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    api = Api(paths)

    res = api.dispatch("fw.type.add", {"name": "newprobe", "chipset": "stm32g431xx"})

    assert res["name"] == "newprobe"
    assert api.registry().get("newprobe").serials == []
    # And it is a target immediately, so the panel can offer menuconfig for it.
    assert "newprobe" in _targets(api)


def test_a_type_can_name_the_firmware_it_runs_when_it_is_created(paths, live_registry_text):
    """The gap that made cartographer a hand-edit: `fw.type.add` had no way to
    say the board runs anything but klipper, so the section had to be written
    into the cfg by hand before menuconfig could be reached at all."""
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write("\n[firmware cartographer]\nsource: ~/carto\nartifact: klipper\n")
    api = Api(paths)

    res = api.dispatch(
        "fw.type.add",
        {"name": "carto_v4", "chipset": "stm32g431xx", "firmware": "cartographer"},
    )

    assert res["firmware"] == "cartographer"
    assert api.registry().get("carto_v4").firmware == "cartographer"
    assert _targets(api)["carto_v4"]["firmware"] == "cartographer"


def test_an_undeclared_family_is_refused_rather_than_quietly_accepted(paths, live_registry_text):
    """An unknown family resolves to the conventional ~/<name>, so a typo would
    produce a type that builds nothing and reports "never built" for good."""
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    api = Api(paths)

    with pytest.raises(Exception) as exc:
        api.dispatch(
            "fw.type.add",
            {"name": "typo", "chipset": "stm32g431xx", "firmware": "cartographe"},
        )

    assert "cartographe" in str(exc.value)
    # The known families are named, so the panel can offer them rather than
    # making the user guess what it wanted.
    assert "klipper" in str(exc.value)
    assert "typo" not in api.registry().types


def test_changing_the_firmware_warns_that_provenance_cannot_see_it(paths, live_registry_text):
    """Staleness compares a tree against itself. Swapping the tree leaves the
    old binary looking perfectly current, which is a flash of the wrong
    firmware with nothing to say so."""
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    with open(paths.main_config, "a", encoding="utf-8") as fh:
        fh.write("\n[firmware cartographer]\nsource: ~/carto\nartifact: klipper\n")
    binary = paths.bin_file("bttebb36", "klipper")
    os.makedirs(os.path.dirname(binary), exist_ok=True)
    with open(binary, "wb") as fh:
        fh.write(b"\x00")
    api = Api(paths)

    res = api.dispatch("fw.type.update", {"name": "bttebb36", "firmware": "cartographer"})

    assert res["firmware"] == "cartographer"
    assert res["warnings"] and "Rebuild before flashing" in res["warnings"][0]


def test_changing_nothing_warns_about_nothing(paths, live_registry_text):
    """Re-sending the family a type already has is not a change."""
    with open(paths.registry_file, "w", encoding="utf-8") as fh:
        fh.write(live_registry_text)
    binary = paths.bin_file("bttebb36", "klipper")
    os.makedirs(os.path.dirname(binary), exist_ok=True)
    with open(binary, "wb") as fh:
        fh.write(b"\x00")
    api = Api(paths)

    res = api.dispatch("fw.type.update", {"name": "bttebb36", "firmware": "klipper"})
    assert res["warnings"] == []
