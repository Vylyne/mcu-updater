"""What a board is actually running, read Klipper-first and wire-second.

The provenance read is one question asked of two sources in an order the lock
forces. Klipper holds the usbserial connection when it is connected, so a read
that went to the wire first would fail on exactly the machines where the answer
was already sitting in the object graph.

The Roadrunner's klippy extra answers both halves - `identity.firmware_version`
and the whole `firmware_image` block - which is why the admin protocol is the
fallback for boards Klippy is not holding rather than the only digest source.

Almost every test here is about a *spelling*: the digest arrives as a hex string
from Klipper and as an int from INFO, the algorithm arrives as a name from one
and a number from the other, and the join key arrives with udev's interface
marker on one side and verbatim from the identity register on the other. Each of
those, left unconverted, produces a permanent mismatch on a board running
precisely what we flashed - and reflashing cannot clear a formatting difference.
"""

from __future__ import annotations

import pytest

from mcu_updater import uf2
from mcu_updater.agent.methods import Api
from mcu_updater.device_info import SOURCE_INFO, SOURCE_KLIPPER, DeviceInfo
from mcu_updater.discovery import roadrunner
from mcu_updater.helpers.roadrunner import RoadrunnerHelper

from .conftest import serve_klipper

SERIAL = "RR-0123456789ABCDEFGHJKMNPQRS"
OBJECT = "high_resolution_filament_sensor toolhead"


def _sensor(
    *,
    serial: str = SERIAL,
    version: str | None = "v1.2.0-3-gdeadbee",
    algorithm: object = "crc32-iso-hdlc",
    digest: object = "0xbbe38aa9",
    start: object = 0x10000000,
    length: object = 600,
) -> dict:
    """One sensor object exactly as the extra's `get_status` shapes it."""
    return {
        OBJECT: {
            "identity": {
                "provisioned": True,
                "state": 1,
                "serial": serial,
                "firmware_version": version,
                "transport": "usb",
                "led_order": "grb",
            },
            "firmware_image": {
                "algorithm": algorithm,
                "digest": digest,
                "start": start,
                "length": length,
            },
        }
    }


@pytest.fixture
def api(paths):
    return Api(paths)


REPORTER = RoadrunnerHelper()

WIRE_INFO = DeviceInfo(
    source=SOURCE_INFO,
    version="v1.2.0-3-gdeadbee",
    digest_algorithm=uf2.DIGEST_CRC32_ISO_HDLC,
    digest=0xBBE38AA9,
    image_start=0x10000000,
    image_length=600,
)


class _Spy:
    """A `wire` source that records which serials it was asked about.

    The point of the injection is that the wire is never touched for a board
    Klipper answered for, and the only way to assert that is to watch the
    source: a test that merely checked no port was opened would pass for the
    wrong reason, since the caller under test opens none either way.
    """

    def __init__(self, answers: dict[str, DeviceInfo] | None = None) -> None:
        self.asked: list[str] = []
        self.answers = answers or {}

    def __call__(self, serial: str) -> DeviceInfo | None:
        self.asked.append(serial)
        return self.answers.get(serial)


def test_klipper_answers_the_version_and_the_digest(api):
    api._call = serve_klipper(_sensor())

    found = api.reported_images(REPORTER, [SERIAL])

    assert found[SERIAL] == DeviceInfo(
        source=SOURCE_KLIPPER,
        version="v1.2.0-3-gdeadbee",
        digest_algorithm=uf2.DIGEST_CRC32_ISO_HDLC,
        # The extra sends "0xbbe38aa9"; every other side of this comparison
        # holds an int, and the conversion happens at this boundary.
        digest=0xBBE38AA9,
        image_start=0x10000000,
        image_length=600,
    )


def test_no_port_is_opened_for_a_board_klipper_answered_for(api):
    api._call = serve_klipper(_sensor())
    spy = _Spy({SERIAL: WIRE_INFO})

    found = api.reported_images(REPORTER, [SERIAL], wire=spy)

    assert spy.asked == []
    assert found[SERIAL].source == SOURCE_KLIPPER


def test_the_wire_answers_for_a_board_klipper_has_no_object_for(api):
    api._call = serve_klipper({})
    spy = _Spy({SERIAL: WIRE_INFO})

    found = api.reported_images(REPORTER, [SERIAL], wire=spy)

    assert spy.asked == [SERIAL]
    assert found[SERIAL] is WIRE_INFO


def test_without_a_wire_source_an_unanswered_serial_is_simply_absent(api):
    """`fw.status` passes no source, so it can never reach for a port.

    Absent is the honest answer there: "we did not ask" and "the board said
    nothing" both fall through to the version comparison, and neither may be
    turned into a mismatch.
    """
    api._call = serve_klipper({})

    assert api.reported_images(REPORTER, [SERIAL]) == {}


def test_the_join_survives_udevs_suffix_and_case(api):
    """The two sources spell one burned-in string two ways.

    A missed join is indistinguishable from "Klipper did not answer", so it
    would silently send every board to the wire - the failure this
    normalisation exists to prevent, and one a test that built both sides from
    the same literal could never catch.
    """
    api._call = serve_klipper(_sensor(serial=SERIAL))
    spy = _Spy()

    tracked = SERIAL.lower() + "-if00"
    found = api.reported_images(REPORTER, [tracked], wire=spy)

    assert spy.asked == []
    # Keyed as the caller spelled it, so a row finds its own answer.
    assert found[tracked].source == SOURCE_KLIPPER


def test_a_digest_without_a_range_is_carried_not_probed_for(api):
    """Firmware that reports no range has reported no comparable digest.

    That is absence, and it falls through to the version comparison. Going to
    the wire to fill the range in would be the host substituting for the board.
    """
    api._call = serve_klipper(_sensor(start=None, length=None))
    spy = _Spy({SERIAL: WIRE_INFO})

    found = api.reported_images(REPORTER, [SERIAL], wire=spy)

    assert spy.asked == []
    assert found[SERIAL].digest == 0xBBE38AA9
    assert found[SERIAL].image_start is None
    assert found[SERIAL].has_digest() is False


def test_an_algorithm_this_host_never_heard_of_is_absence(api):
    """Not mismatch. A permanent mismatch no flash can clear is the bug."""
    api._call = serve_klipper(_sensor(algorithm="sha256-truncated"))

    assert api.reported_images(REPORTER, [SERIAL])[SERIAL].digest_algorithm is None


def test_an_unreported_algorithm_is_absence(api):
    api._call = serve_klipper(_sensor(algorithm=None, digest=None))

    found = api.reported_images(REPORTER, [SERIAL])[SERIAL]

    assert found.digest_algorithm is None
    assert found.digest is None


def test_an_unparseable_digest_is_absence(api):
    api._call = serve_klipper(_sensor(digest="not-a-number"))

    assert api.reported_images(REPORTER, [SERIAL])[SERIAL].digest is None


def test_a_sensor_with_no_serial_yet_is_not_guessed_at(api):
    """A board mid-connect reports the object with nothing in it.

    Attaching its digest to whichever serial was being asked about would put
    one board's image on another board's row.
    """
    api._call = serve_klipper(_sensor(serial=""))
    spy = _Spy()

    assert api.reported_images(REPORTER, [SERIAL], wire=spy) == {}
    assert spy.asked == [SERIAL]


def test_an_unreachable_klipper_falls_through_to_the_wire(api):
    api._call = serve_klipper(_sensor(), reachable=False)
    spy = _Spy({SERIAL: WIRE_INFO})

    found = api.reported_images(REPORTER, [SERIAL], wire=spy)

    assert found[SERIAL] is WIRE_INFO


def test_only_the_fields_the_reporter_names_are_queried(api):
    """The sub-second budget is why the cached object list exists at all."""
    api._call = serve_klipper(_sensor())

    api.reported_images(REPORTER, [SERIAL])

    queries = [
        params for params in api._call.queries if OBJECT in (params.get("objects") or {})
    ]
    assert queries and queries[0]["objects"][OBJECT] == ["identity", "firmware_image"]


def test_the_roadrunner_wire_source_speaks_device_info(paths, monkeypatch):
    """INFO's dict becomes the same `DeviceInfo` Klipper's answer does, so the
    verdict never learns which source it came from."""
    monkeypatch.setattr(
        roadrunner,
        "wire_provenance",
        lambda _paths: lambda serial: {
            "fw_version": "v1.2.0-3-gdeadbee",
            "digest_algorithm": 1,
            "digest": 0xBBE38AA9,
            "image_start": 0x10000000,
            "image_length": 600,
        },
    )

    assert REPORTER.wire_source(paths)(SERIAL) == WIRE_INFO


def test_the_roadrunner_wire_source_passes_absence_through(paths, monkeypatch):
    monkeypatch.setattr(roadrunner, "wire_provenance", lambda _paths: lambda serial: None)

    assert REPORTER.wire_source(paths)(SERIAL) is None


# -- the wire half --------------------------------------------------------


def test_info_provenance_keeps_only_correctly_typed_fields():
    """These come from a subprocess's JSON.

    A string where an int belongs would reach the comparison and read as a
    mismatch against every artifact; dropped, it reads as absence instead.
    """
    kept = roadrunner.provenance(
        {
            "serial": SERIAL,
            "fw_version": "v1.2.0",
            "digest_algorithm": 1,
            "digest": 0xBBE38AA9,
            "image_start": "0x10000000",
            "image_length": None,
        }
    )

    assert kept == {
        "fw_version": "v1.2.0",
        "digest_algorithm": 1,
        "digest": 0xBBE38AA9,
    }


def test_a_bool_is_not_an_int_here():
    """`True == 1` in Python, so an unguarded check would take a bool for
    `DIGEST_CRC32_ISO_HDLC` and compare a digest that was never reported."""
    assert roadrunner.provenance({"digest_algorithm": True}) == {}


def test_an_empty_version_is_absence_not_an_empty_version():
    assert roadrunner.provenance({"fw_version": ""}) == {}


def test_the_wire_source_reports_absence_rather_than_raising(paths):
    """`find_provisioned` raises for a board it cannot confirm.

    Letting that out would turn "this board did not answer" into a failed call
    for whatever asked - and absence is never mismatch, so None is the only
    honest answer this source can give.
    """
    read = roadrunner.wire_provenance(paths)

    assert read("not-a-canonical-serial") is None
    assert read(SERIAL) is None


# -- shapes that are absence, not a plausible wrong number -----------------


def test_a_boolean_digest_is_absence(api):
    """`isinstance(True, int)` is True in Python.

    Unguarded, a JSON `true` in that field becomes the digest `1` - a number
    that looks like an answer and compares against every artifact. Absence is
    the only honest reading.
    """
    api._call = serve_klipper(_sensor(digest=True))

    assert api.reported_images(REPORTER, [SERIAL])[SERIAL].digest is None


def test_a_boolean_image_bound_is_absence(api):
    api._call = serve_klipper(_sensor(start=True, length=True))

    found = api.reported_images(REPORTER, [SERIAL])[SERIAL]

    assert found.image_start is None
    assert found.image_length is None


def test_the_algorithm_name_is_matched_however_it_is_cased(api):
    """The extra sends one constant, but the name is the wire contract and
    another build of it could spell the same algorithm differently."""
    api._call = serve_klipper(_sensor(algorithm="CRC32-ISO-HDLC"))

    found = api.reported_images(REPORTER, [SERIAL])[SERIAL]

    assert found.digest_algorithm == uf2.DIGEST_CRC32_ISO_HDLC


def test_a_sensor_object_with_no_serial_produces_no_entry_at_all():
    """Asserted on the reader, not the join: a junk key that happens never to
    be asked for would let the join test pass while the reader was broken."""
    assert REPORTER.from_klipper(_sensor(serial="")[OBJECT]) is None
