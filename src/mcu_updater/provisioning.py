"""Giving a board that appeared on the bus its durable identity, unasked."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .build import Reporter, null_reporter
from .errors import BusyError, UpdaterError
from .paths import Paths


def auto_provision(
    paths: Paths,
    devices: Mapping[str, Any],
    *,
    may_provision: bool = False,
    reporter: Reporter = null_reporter,
) -> bool:
    """Provision helper-recognised boards for every opted-in family.

    `devices` is one watcher sweep keyed by serial. The values are deliberately
    unused: the family's Trackable capability owns the identity judgement and
    its Provisioner confirms the board over the wire before writing.

    `may_provision` is the deployment-wide hardware-write policy. It defaults
    closed, unlike operator-requested tracking: this path writes to a board
    nobody named, so every caller must opt in to the irreversible write. A
    policy refusal returns False because another poll cannot change it.

    True asks the watcher to retry on its next poll. Only a held operation lock
    is transient in that way; other updater errors are reported and dropped.
    """
    from . import helpers, typelist
    from .lock import exclusive

    retry = False
    attempted_serials: set[str] = set()
    try:
        _entries, families = typelist.read_config(paths)
    except UpdaterError as exc:
        reporter("warn", f"could not read the firmware families: {exc}")
        return False

    for family in families.values():
        if not family.auto_provision:
            continue
        try:
            helper = helpers.for_name(family.helper, family=family.name)
        except UpdaterError as exc:
            # Config loading refuses this too; keep a live agent safe if its
            # file is edited after startup.
            reporter("warn", f"[firmware {family.name}]: {exc}")
            continue
        judge = helpers.trackable(helper)
        prov = helpers.provisioner(helper)
        if judge is None or prov is None:
            continue
        for serial in sorted(devices):
            try:
                verdict = judge.is_trackable(serial)
            except Exception as exc:  # noqa: BLE001 - never kill the watcher
                reporter("warn", f"[firmware {family.name}]: {exc}")
                break
            if verdict.ok:
                continue
            if verdict.remedy != "provision":
                continue
            if not may_provision:
                continue
            if serial in attempted_serials:
                continue
            attempted_serials.add(serial)
            try:
                # Selection, irreversible write and re-enumeration handoff are
                # one operation and must not interleave with a flash.
                with exclusive(paths, f"auto-provision {serial}"):
                    provisioned = prov.provision(paths, serial)
            except BusyError:
                retry = True
                continue
            except UpdaterError as exc:
                reporter("warn", f"{serial}: {exc}")
                continue
            reporter("info", f"Provisioned {serial} as {provisioned}.")
    return retry


__all__ = ["auto_provision"]
