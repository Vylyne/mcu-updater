"""The cmake provider: config parsing, subtree-scoped provenance, staging.

Mirrors `test_pio.py`, which is the closest existing suite. The parts that are
deliberately *not* copied from it - subtree-scoped git, an artifact path the
provider owns - have their own tests saying why.
"""

from __future__ import annotations

import pytest

from mcu_updater.errors import ConfigError
from mcu_updater.providers import cmake


def write_config(paths, text: str) -> None:
    with open(paths.main_config, "w", encoding="utf-8") as fh:
        fh.write(text)


ROADRUNNER_CFG = """
[firmware roadrunner]
source: {source}
builder: cmake
cmake_args: -DROADRUNNER_FIRMWARE_VERSION=${{git_describe}}

[type roadrunner]
chipset: rp2040
firmware: roadrunner
cmake_target: roadrunner_v1_i2c_rgb
serials:
    RR-ABCDEFGHIJKLMNOPQRSTUVWXYZ
"""


def test_a_cmake_type_loads_with_its_target_and_args(paths, tmp_path):
    write_config(paths, ROADRUNNER_CFG.format(source=tmp_path))
    types = cmake.load(paths)
    assert set(types) == {"roadrunner"}
    rr = types["roadrunner"]
    assert rr.cmake_target == "roadrunner_v1_i2c_rgb"
    assert rr.firmware == "roadrunner"
    assert rr.source == str(tmp_path)
    assert rr.cmake_args == "-DROADRUNNER_FIRMWARE_VERSION=${git_describe}"


def test_a_kconfig_type_is_not_ours(paths):
    """Provider is derived from the declared family's builder, exactly as
    pio.load() derives its own."""
    write_config(
        paths,
        "[type bttebb36]\nchipset: stm32g0b1xx\nfirmware: klipper, katapult\nserials:\n",
    )
    assert cmake.load(paths) == {}


def test_a_cmake_type_with_no_target_is_refused(paths, tmp_path):
    """The same refusal pio.load() gives a PlatformIO type with no env:. A
    default would mean guessing which of six images belongs on the board."""
    write_config(
        paths,
        f"[firmware roadrunner]\nsource: {tmp_path}\nbuilder: cmake\n\n"
        "[type roadrunner]\nchipset: rp2040\nfirmware: roadrunner\nserials:\n",
    )
    with pytest.raises(ConfigError) as exc:
        cmake.load(paths)
    assert "roadrunner" in str(exc.value)
    assert "cmake_target" in str(exc.value)


def test_a_missing_config_file_is_not_an_error(paths):
    """No config means no cmake types, which is what every install has today."""
    assert cmake.load(paths) == {}
