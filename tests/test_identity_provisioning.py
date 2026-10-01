"""fw.identity.* - an identity write routed to the one helper that claims it.

The call names no family: the board is untracked, usually before any type for
it exists. So the core asks every registered `Provisioner` whether the serial
is its identity, and in which state, and needs exactly one yes. These tests
run against stand-ins registered beside the real helpers; nothing here opens a
port.
"""

from __future__ import annotations

import pytest

from mcu_updater.agent.methods import Api
from mcu_updater.agent.rpc import RpcError
from mcu_updater.helpers import registry as helpers_registry
from mcu_updater.jobs import JobRunner
from mcu_updater.settings import load_settings

from .conftest import write_settings

UNPROVISIONED = "FAKE-UNPROVISIONED-0001"
PROVISIONED = "FAKE-0001"


class FakeProvisioner:
    def __init__(self, name: str = "fake_identity") -> None:
        self.name = name
        self.calls: list[tuple[str, str]] = []

    def identity_state(self, serial):
        if serial.startswith("FAKE-UNPROVISIONED-"):
            return "unprovisioned"
        if serial.startswith("FAKE-"):
            return "provisioned"
        return None

    def provision(self, paths, serial):
        self.calls.append(("provision", serial))
        return PROVISIONED

    def clear(self, paths, serial):
        self.calls.append(("clear", serial))
        return UNPROVISIONED


@pytest.fixture
def api(paths):
    write_settings(paths, dry_run="true", service_backend="null", enable_flashing="true")
    return Api(paths, runner=JobRunner(paths, lambda: load_settings(paths.settings_file)))


@pytest.fixture
def fake(monkeypatch):
    prov = FakeProvisioner()
    monkeypatch.setitem(helpers_registry._BY_NAME, prov.name, prov)
    return prov


def _refusal(api, method, serial):
    with pytest.raises(RpcError) as exc:
        api.dispatch(method, {"serial": serial})
    return exc.value.data


def test_provision_goes_to_the_helper_that_claims_the_serial(api, fake):
    res = api.dispatch("fw.identity.provision", {"serial": UNPROVISIONED})

    assert res == {"serial": PROVISIONED, "prior_serial": UNPROVISIONED, "state": "provisioned"}
    assert fake.calls == [("provision", UNPROVISIONED)]


def test_clear_goes_to_the_helper_that_claims_the_serial(api, fake):
    res = api.dispatch("fw.identity.clear", {"serial": PROVISIONED})

    assert res == {"serial": UNPROVISIONED, "prior_serial": PROVISIONED, "state": "unprovisioned"}
    assert fake.calls == [("clear", PROVISIONED)]


def test_a_serial_no_helper_claims_is_not_provisionable(api, fake):
    data = _refusal(api, "fw.identity.provision", "E6614103E7452D2F")

    assert data["code"] == "not_provisionable"
    assert data["data"] == {"serial": "E6614103E7452D2F", "helpers": []}
    assert fake.calls == []


def test_two_helpers_claiming_one_serial_write_nothing(api, fake, monkeypatch):
    """Two firmwares both answering "mine" is a naming collision. Picking one
    would be a coin toss over an irreversible write."""
    other = FakeProvisioner("fake_identity_2")
    monkeypatch.setitem(helpers_registry._BY_NAME, other.name, other)

    data = _refusal(api, "fw.identity.provision", UNPROVISIONED)

    assert data["code"] == "not_provisionable"
    assert data["data"]["helpers"] == ["fake_identity", "fake_identity_2"]
    assert fake.calls == other.calls == []


def test_provisioning_an_already_provisioned_serial_is_refused(api, fake):
    data = _refusal(api, "fw.identity.provision", PROVISIONED)

    assert data["code"] == "not_provisionable"
    assert data["data"]["helpers"] == ["fake_identity"]
    assert fake.calls == []


def test_clearing_an_unprovisioned_serial_is_refused(api, fake):
    data = _refusal(api, "fw.identity.clear", UNPROVISIONED)

    assert data["code"] == "not_provisionable"
    assert fake.calls == []


def _track(paths, serial):
    # Appended, not written plain: the `api` fixture already wrote the
    # `[updater]` section into this same shared config file, and a plain
    # `open(..., "w")` would truncate it back to its enable_flashing=False
    # default - see write_settings' docstring in conftest.py for the same
    # footgun the other way round.
    with open(paths.registry_file, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(
            "\n[firmware fakefw]\nsource: ~/fakefw\nbuilder: cmake\nflashers: bootsel\n"
            f"\n[type faketype]\nchipset: rp2040\nfirmware: fakefw\nserials:\n    {serial}\n"
        )


@pytest.mark.parametrize(
    ("method", "serial"),
    [("fw.identity.provision", UNPROVISIONED), ("fw.identity.clear", PROVISIONED)],
)
def test_a_tracked_serial_is_refused_before_anything_is_written(api, fake, paths, method, serial):
    _track(paths, serial)

    data = _refusal(api, method, serial)

    assert data["code"] == "device_tracked"
    assert data["data"]["tracked_under"] == ["faketype"]
    assert fake.calls == []


@pytest.mark.parametrize(
    ("method", "write", "serial"),
    [
        ("fw.identity.provision", "provision", UNPROVISIONED),
        ("fw.identity.clear", "clear", PROVISIONED),
    ],
)
def test_a_serial_cannot_be_tracked_while_its_identity_is_being_written(
    api, fake, paths, monkeypatch, method, write, serial
):
    """RPCs run on a pool. The untracked check and the write are one step as far
    as `fw.serial.add` is concerned: tracked in between, the registry would be
    left naming a serial the board no longer has."""
    import threading

    with open(paths.registry_file, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(
            "\n[firmware fakefw]\nsource: ~/fakefw\nbuilder: cmake\nflashers: bootsel\n"
            "\n[type faketype]\nchipset: rp2040\nfirmware: fakefw\n"
        )
    adding = threading.Thread(
        target=lambda: api.dispatch("fw.serial.add", {"name": "faketype", "serial": serial})
    )
    tracked_during_the_write = []
    plain = getattr(fake, write)

    def write_while_someone_tracks_it(paths_, serial_):
        adding.start()
        adding.join(timeout=0.3)
        tracked_during_the_write.extend(api.registry().find_declared_types_for_serial(serial_))
        return plain(paths_, serial_)

    monkeypatch.setattr(fake, write, write_while_someone_tracks_it)

    api.dispatch(method, {"serial": serial})
    adding.join(timeout=10)

    assert not adding.is_alive()
    assert tracked_during_the_write == []


def test_the_old_method_names_are_gone(api):
    capabilities = api.dispatch("fw.ping")["capabilities"]

    assert "fw.identity.provision" in capabilities
    assert "fw.identity.clear" in capabilities
    assert not [m for m in capabilities if m.startswith("fw.roadrunner.")]


def test_no_wire_code_is_named_for_a_firmware():
    """Machine-readable names are generic; only human text may name a firmware."""
    import pathlib
    import re

    src = pathlib.Path(__file__).resolve().parents[1] / "src"
    named = [
        f"{path.name}: {match}"
        for path in src.rglob("*.py")
        for match in re.findall(r"""["'](roadrunner_[a-z_]+)["']""", path.read_text(encoding="utf-8"))
    ]
    assert named == []
