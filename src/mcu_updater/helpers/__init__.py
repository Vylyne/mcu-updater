"""Firmware-specific helper capabilities selected by static configuration."""

from __future__ import annotations

from .spec import (
    BootselHandoff,
    BootselRequester,
    DeviceInfoReader,
    DeviceLister,
    Helper,
    Identifier,
    ImageReporter,
    ListedDevice,
    Provisioner,
    Trackable,
    TrackVerdict,
)


def for_name(name: str, *, family: str) -> Helper | None:
    """Resolve a helper without importing implementations during package init."""
    from .registry import for_name as resolve

    return resolve(name, family=family)


def all_helpers() -> tuple[Helper, ...]:
    """Every registered helper, for a caller that names no family."""
    from .registry import all_helpers as every

    return every()


def bootsel_requester(helper: Helper | None) -> BootselRequester | None:
    """`helper`'s BOOTSEL request capability, or None when it has none."""
    return helper if isinstance(helper, BootselRequester) else None


def device_info_reader(helper: Helper | None) -> DeviceInfoReader | None:
    """The helper's device-info reader, or None when it has none."""
    return helper if isinstance(helper, DeviceInfoReader) else None


def device_lister(helper: Helper | None) -> DeviceLister | None:
    """The helper's device-listing capability, or None when it has none."""
    return helper if isinstance(helper, DeviceLister) else None


def image_reporter(helper: Helper | None) -> ImageReporter | None:
    """The helper's image reporter, or None when it has none."""
    return helper if isinstance(helper, ImageReporter) else None


def identifier(helper: Helper | None) -> Identifier | None:
    """The helper's identity capability, or None when it has none."""
    return helper if isinstance(helper, Identifier) else None


def provisioner(helper: Helper | None) -> Provisioner | None:
    """This helper's provisioning capability, or None if it has none.

    None is an ordinary answer, not a misconfiguration: most firmware has no
    identity to hand out.
    """
    return helper if isinstance(helper, Provisioner) else None


def trackable(helper: Helper | None) -> Trackable | None:
    """This helper's identity-durability judge, or None if all serials qualify."""
    return helper if isinstance(helper, Trackable) else None


__all__ = [
    "BootselHandoff",
    "BootselRequester",
    "DeviceInfoReader",
    "DeviceLister",
    "Helper",
    "Identifier",
    "ImageReporter",
    "ListedDevice",
    "Provisioner",
    "Trackable",
    "TrackVerdict",
    "all_helpers",
    "bootsel_requester",
    "device_info_reader",
    "device_lister",
    "for_name",
    "identifier",
    "image_reporter",
    "provisioner",
    "trackable",
]
