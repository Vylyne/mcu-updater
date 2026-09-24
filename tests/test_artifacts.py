"""What a build staged, by kind, and how each builder's sidecar vouches for it.

A kind names what a flasher consumes. The sidecar records one hash per kind;
a sidecar written before that vouches only for its builder's primary kind,
through the legacy `bin_sha256`.
"""

from __future__ import annotations

import hashlib
import json
import os
import types

from mcu_updater import build, providers
from mcu_updater.artifacts import (
    KIND_BIN,
    KIND_PIO_ENV,
    KIND_UF2,
    Artifact,
    Staged,
    recorded_kinds,
    recorded_sha256,
    sidecar_field,
)
from mcu_updater.firmware import FirmwareFamily
from mcu_updater.providers import cmake, pio

KLIPPER = FirmwareFamily(name="klipper", flashers=("flashtool",))
ROADRUNNER = FirmwareFamily(name="roadrunner", builder="cmake", flashers=("bootsel",))
KNOMI = FirmwareFamily(name="knomi", builder="platformio", flashers=("esptool",))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: str, data: bytes) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def _sidecar(paths, mcu_type: str, fw: str, record: dict) -> None:
    path = paths.sidecar_file(mcu_type, fw)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh)


# --- the model ---------------------------------------------------------------


def test_first_of_follows_the_kinds_order_not_the_staged_order():
    staged = Staged(
        fw="klipper",
        artifacts=(Artifact(KIND_UF2, "/a.uf2"), Artifact(KIND_BIN, "/a.bin")),
    )

    assert staged.first_of((KIND_BIN, KIND_UF2)) == Artifact(KIND_BIN, "/a.bin")
    assert staged.first_of((KIND_PIO_ENV,)) is None


def test_the_sidecar_field_records_one_hash_per_kind():
    assert sidecar_field({KIND_BIN: "b", KIND_UF2: None}) == {
        "bin": {"sha256": "b"},
        "uf2": {"sha256": None},
    }


def test_recorded_sha256_reads_the_per_kind_entry():
    side = {"bin_sha256": "legacy", "artifacts": sidecar_field({KIND_BIN: "b", KIND_UF2: "u"})}

    assert recorded_sha256(side, KIND_UF2, primary=KIND_BIN) == "u"
    assert recorded_sha256(side, KIND_BIN, primary=KIND_BIN) == "b"
    assert recorded_kinds(side) == {KIND_BIN, KIND_UF2}


def test_an_old_sidecar_vouches_only_for_its_primary_kind():
    """`bin_sha256` hashed the .bin for kconfig and the .uf2 for cmake. Reading
    it for any other kind would vouch for bytes it never described."""
    old = {"bin_sha256": "legacy"}

    assert recorded_sha256(old, KIND_BIN, primary=KIND_BIN) == "legacy"
    assert recorded_sha256(old, KIND_UF2, primary=KIND_BIN) is None
    assert recorded_sha256(old, KIND_UF2, primary=KIND_UF2) == "legacy"
    assert recorded_kinds(old) == set()


# --- kconfig -----------------------------------------------------------------


def test_kconfig_stages_the_bin_with_its_verified_hash(paths):
    _write(paths.bin_file("ebb36", "klipper"), b"built")
    _sidecar(paths, "ebb36", "klipper", {"fw_sha": "abc", "bin_sha256": _sha(b"built"), "version": "v1"})

    staged = build.staged(paths, "ebb36", KLIPPER)

    assert staged == Staged(
        fw="klipper",
        artifacts=(Artifact(KIND_BIN, paths.bin_file("ebb36", "klipper"), _sha(b"built")),),
        fw_sha="abc",
        version="v1",
    )


def test_kconfig_withholds_the_hash_of_a_bin_replaced_behind_it(paths):
    """Review Focus 2: flashable, but the ledger must not file the sidecar's
    hash against bytes the sidecar never described."""
    _write(paths.bin_file("ebb36", "klipper"), b"somebody else's build")
    _sidecar(paths, "ebb36", "klipper", {"fw_sha": "abc", "bin_sha256": _sha(b"built")})

    [artifact] = build.staged(paths, "ebb36", KLIPPER).artifacts

    assert artifact.kind == KIND_BIN
    assert artifact.sha256 is None


def test_kconfig_stages_a_uf2_only_when_the_sidecar_lists_it(paths):
    """Review Focus 3: a .uf2 beside a pre-artifacts sidecar may be older than
    the .bin, so it waits for one rebuild."""
    _write(paths.bin_file("pico", "klipper"), b"bin")
    _write(paths.uf2_file("pico", "klipper"), b"uf2")
    _sidecar(paths, "pico", "klipper", {"fw_sha": "abc", "bin_sha256": _sha(b"bin")})

    staged = build.staged(paths, "pico", KLIPPER)

    assert [a.kind for a in staged.artifacts] == [KIND_BIN]


def test_kconfig_stages_a_listed_uf2_with_its_hash(paths):
    _write(paths.bin_file("pico", "klipper"), b"bin")
    _write(paths.uf2_file("pico", "klipper"), b"uf2")
    _sidecar(
        paths,
        "pico",
        "klipper",
        {
            "fw_sha": "abc",
            "bin_sha256": _sha(b"bin"),
            "artifacts": sidecar_field({KIND_BIN: _sha(b"bin"), KIND_UF2: _sha(b"uf2")}),
        },
    )

    staged = build.staged(paths, "pico", KLIPPER)

    assert staged.first_of((KIND_UF2,)) == Artifact(
        KIND_UF2, paths.uf2_file("pico", "klipper"), _sha(b"uf2")
    )


def test_kconfig_stages_nothing_when_nothing_was_built(paths):
    assert build.staged(paths, "ebb36", KLIPPER).artifacts == ()


# --- cmake -------------------------------------------------------------------


def _cmake_record(paths, data: bytes, **extra) -> None:
    uf2 = paths.uf2_file("roadrunner", "roadrunner")
    stat = os.stat(uf2)
    _sidecar(
        paths,
        "roadrunner",
        "roadrunner",
        {
            "provider": "cmake",
            "sha": "subtree-sha",
            "version": "v1.2.3",
            "dirty": False,
            "cmake_target": "roadrunner_v1_i2c_rgb",
            "bin_sha256": _sha(data),
            "bin_size": stat.st_size,
            "bin_mtime": stat.st_mtime,
            **extra,
        },
    )


def test_cmake_stages_an_owned_uf2_with_provenance(paths):
    uf2 = _write(paths.uf2_file("roadrunner", "roadrunner"), b"image")
    _cmake_record(paths, b"image")

    staged = cmake.staged(paths, "roadrunner", ROADRUNNER)

    assert staged == Staged(
        fw="roadrunner",
        artifacts=(Artifact(KIND_UF2, uf2, _sha(b"image")),),
        fw_sha="subtree-sha",
        version="v1.2.3",
    )


def test_cmake_withholds_provenance_for_a_dirty_record(paths):
    _write(paths.uf2_file("roadrunner", "roadrunner"), b"image")
    _cmake_record(paths, b"image", dirty=True)

    staged = cmake.staged(paths, "roadrunner", ROADRUNNER)

    assert [(a.kind, a.sha256) for a in staged.artifacts] == [(KIND_UF2, None)]
    assert staged.fw_sha is None
    assert staged.version is None


def test_cmake_stages_nothing_without_a_uf2(paths):
    assert cmake.staged(paths, "roadrunner", ROADRUNNER) == Staged(fw="roadrunner")


def test_cmake_stages_a_listed_bin_with_its_verified_hash(paths):
    _write(paths.uf2_file("roadrunner", "roadrunner"), b"image")
    bin_path = _write(paths.bin_file("roadrunner", "roadrunner"), b"raw")
    _cmake_record(
        paths, b"image", artifacts=sidecar_field({KIND_UF2: _sha(b"image"), KIND_BIN: _sha(b"raw")})
    )

    staged = cmake.staged(paths, "roadrunner", ROADRUNNER)

    assert staged.first_of((KIND_BIN,)) == Artifact(KIND_BIN, bin_path, _sha(b"raw"))


def test_a_dirty_cmake_bin_is_staged_without_provenance(paths):
    """Review Focus 2: a dirty build is flashable - a dirty uf2 always has been
    - but nothing it recorded reaches the ledger."""
    _write(paths.uf2_file("roadrunner", "roadrunner"), b"image")
    _write(paths.bin_file("roadrunner", "roadrunner"), b"raw")
    _cmake_record(
        paths,
        b"image",
        dirty=True,
        artifacts=sidecar_field({KIND_UF2: _sha(b"image"), KIND_BIN: _sha(b"raw")}),
    )

    artifact = cmake.staged(paths, "roadrunner", ROADRUNNER).first_of((KIND_BIN,))

    assert artifact is not None
    assert artifact.sha256 is None


def test_an_old_cmake_sidecar_does_not_offer_a_bin(paths):
    _write(paths.uf2_file("roadrunner", "roadrunner"), b"image")
    _write(paths.bin_file("roadrunner", "roadrunner"), b"raw")
    _cmake_record(paths, b"image")

    staged = cmake.staged(paths, "roadrunner", ROADRUNNER)

    assert [a.kind for a in staged.artifacts] == [KIND_UF2]
    assert staged.artifacts[0].sha256 == _sha(b"image")


# --- platformio --------------------------------------------------------------


def _knomi(tmp_path):
    return types.SimpleNamespace(name="knomi", source=str(tmp_path / "knomi"), env="knomi_v2")


def test_an_unbuilt_platformio_env_is_still_staged(paths, tmp_path, monkeypatch):
    """`pio run -t upload` builds before it uploads, so refusing an unbuilt env
    would refuse every screen nobody had built by hand."""
    display = _knomi(tmp_path)
    monkeypatch.setattr(pio, "load", lambda paths: {"knomi": display})

    staged = pio.staged(paths, "knomi", KNOMI)

    assert staged == Staged(
        fw="knomi", artifacts=(Artifact(KIND_PIO_ENV, pio.firmware_bin(display), None),)
    )


def test_a_platformio_type_that_is_not_configured_stages_nothing(paths, monkeypatch):
    monkeypatch.setattr(pio, "load", lambda paths: {})

    assert pio.staged(paths, "knomi", KNOMI) == Staged(fw="knomi")


# --- dispatch ----------------------------------------------------------------


def test_providers_staged_asks_the_familys_builder(paths):
    _write(paths.uf2_file("roadrunner", "roadrunner"), b"image")
    _write(paths.bin_file("ebb36", "klipper"), b"built")

    assert [a.kind for a in providers.staged(paths, "roadrunner", ROADRUNNER).artifacts] == [KIND_UF2]
    assert [a.kind for a in providers.staged(paths, "ebb36", KLIPPER).artifacts] == [KIND_BIN]
