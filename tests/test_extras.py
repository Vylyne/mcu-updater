"""`targets[].extras`: facts a seam contributes to a row, as one flat list.

The UI renders `label value` and never branches on `key`, `seam` or `name`, so
a new builder, flasher or helper adds entries rather than a new wire type.
"""

from __future__ import annotations

import pytest

from mcu_updater.extras import SEAMS, Extra, ordered


def test_an_extra_serialises_to_the_documented_keys():
    extra = Extra("helper", "knomi_serial", "module_version", "Module", "0.5.0")
    assert extra.to_json() == {
        "seam": "helper",
        "name": "knomi_serial",
        "key": "module_version",
        "label": "Module",
        "value": "0.5.0",
    }


def test_an_unknown_seam_is_refused():
    with pytest.raises(ValueError, match="seam"):
        Extra("provider", "x", "k", "K", 1)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [{"a": 1}, [1], (1,), object()])
def test_a_value_that_is_not_a_json_scalar_is_refused(value):
    with pytest.raises(TypeError, match="scalar"):
        Extra("builder", "cmake", "k", "K", value)


@pytest.mark.parametrize("value", ["s", 1, 1.5, True, None])
def test_every_json_scalar_is_accepted(value):
    assert Extra("builder", "cmake", "k", "K", value).value == value


def test_extras_are_ordered_by_seam_then_as_each_seam_returned_them():
    helper_b = Extra("helper", "h", "b", "B", 1)
    builder = Extra("builder", "cmake", "a", "A", 1)
    helper_a = Extra("helper", "h", "a", "A", 1)
    flasher = Extra("flasher", "bootsel", "a", "A", 1)

    keys = [(e["seam"], e["key"]) for e in ordered([helper_b, builder, helper_a, flasher])]

    assert keys == [("builder", "a"), ("flasher", "a"), ("helper", "b"), ("helper", "a")]


def test_the_seams_are_the_three_the_spec_names():
    assert SEAMS == ("builder", "flasher", "helper")
