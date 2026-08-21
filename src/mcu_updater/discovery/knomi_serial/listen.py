"""Asking the displays themselves - the broadcast listen pass.

The authoritative source, and the only one that can be taken **at flash
time**. Each display broadcasts its id every couple of seconds unprompted, so
this opens the candidate ports, listens, and reads what answered - no
request, no protocol of its own, and no cooperation from a device that might
be busy.

That timing is the point. The Klipper query and the watcher's map both
describe where displays *were*; this describes where they are with esptool
about to write. Their own docs are explicit that identity must be resolved at
flash time rather than from a remembered path, and a remembered path is what
every other source is.

**Requires the ports to be free.** pyserial's exclusive open is an advisory
flock that Klipper's sections, the watcher and esptool all take, so this has
to run after both are stopped. A port somebody still holds is reported as busy
rather than guessed at, which means it is simply absent here.
"""

from __future__ import annotations

import json
import os
import shutil
from typing import TYPE_CHECKING

from ...build import Reporter, null_reporter, run_streamed
from ...errors import ConfigError, SourceTreeMissingError, ToolMissingError, UpdaterError
from ..spec import STATE_KLIPPER, Sighting
from .watcher import WatcherDevice

if TYPE_CHECKING:
    from ...flashers.spec import Bench
    from ...paths import Paths
    from ...providers.pio import PioType
    from ...settings import Settings

#: Interpreters to try, in order. knomi-serial declares `python3-serial` as a
#: system dependency, so a plain `python3` is the one guaranteed to import
#: pyserial; ours is tried first only because on most hosts it is the same
#: binary and saves a process.
DISCOVER_PYTHON_CANDIDATES = ("python3",)

#: Emitted immediately before the JSON so a stray warning on stdout - a
#: deprecation notice, a udev grumble - cannot be mistaken for the answer.
_DISCOVER_MARKER = "__mcu_updater_discover__"

_DISCOVER_SNIPPET = f"""
import json, os, sys
sys.path.insert(0, os.path.join(sys.argv[1], "klippy_extras"))
import knomi_serial as k
kwargs = {{}} if sys.argv[2] == "-" else {{"listen": float(sys.argv[2])}}
found = k.discover_reports(**kwargs)
# Only the three fields we use, so whatever else a report carries cannot make
# this unserialisable.
out = {{
    str(i): {{"port": f.get("port"), "fw": f.get("fw"), "var": f.get("var")}}
    for i, f in found.items()
}}
print("{_DISCOVER_MARKER}" + json.dumps(out))
"""


def source_dir(display: PioType) -> str:
    """Where this display family's source tree lives, validated.

    Shared with `providers/pio.py`'s `build()`/`upload()`, which import it
    back from here - the same shape `flashers/flash.py` already uses for
    `dfu_selector`/`dfu_devices` moved out to `discovery/dfu.py` in Step 24.
    """
    path = os.path.expanduser(display.source)
    if not path:
        raise ConfigError(
            f"'{display.name}' has no source tree configured. Set 'source:' on its "
            f"firmware family.",
            type=display.name,
        )
    if not os.path.isdir(path):
        raise SourceTreeMissingError(
            f"source directory {path} not found for '{display.name}'.",
            fw=display.env,
            path=path,
        )
    return path


def discover(
    paths: Paths,
    settings: Settings,
    display: PioType,
    *,
    listen: float | None = None,
    reporter: Reporter = null_reporter,
) -> dict[str, WatcherDevice]:
    """Ask every display which it is, by listening on the free ports.

    The authoritative source, and the only one that can be taken **at flash
    time**. Each display broadcasts its id every couple of seconds unprompted,
    so this opens the candidate ports, listens, and reads what answered - no
    request, no protocol of its own, and no cooperation from a device that
    might be busy.

    That timing is the point. The Klipper query and the watcher's map are both
    read before the ports are released, so both describe where displays *were*;
    this describes where they are with esptool about to write. Their own docs
    are explicit that identity must be resolved at flash time rather than from
    a remembered path, and a remembered path is what every other source is.

    **Requires the ports to be free.** pyserial's exclusive open is an advisory
    flock that Klipper's sections, the watcher and esptool all take, so this has
    to run after both are stopped. A port somebody still holds is reported as
    busy rather than guessed at, which means it is simply absent here.

    A display announces itself every two seconds, so one is usually heard in
    about one. The six-second default is headroom rather than latency, and it
    covers every port at once instead of each in turn. Left at their default:
    listening too briefly does not flash the wrong screen, it *misses* one, and
    a screen silently skipped is a worse answer than six seconds.
    """
    source = source_dir(display)
    argv_listen = "-" if listen is None else str(listen)

    last: str | None = None
    for candidate in DISCOVER_PYTHON_CANDIDATES:
        found = shutil.which(candidate)
        if not found:
            last = f"{candidate} is not on PATH"
            continue

        transcript: list[str] = []

        def capture(stream: str, line: str, _t: list = transcript) -> None:
            _t.append(line)
            reporter(stream, line)

        reporter("info", f"Listening for displays on the free ports ({source})...")
        rc = run_streamed(
            [found, "-c", _DISCOVER_SNIPPET, source, argv_listen],
            cwd=source,
            reporter=capture,
            dry_run=False,
        )
        text = "\n".join(transcript)
        if rc == 0:
            for line in transcript:
                if line.startswith(_DISCOVER_MARKER):
                    return _parse_discovered(line[len(_DISCOVER_MARKER) :])
            last = "the discovery helper printed no result"
        elif "No module named 'serial'" in text or "No module named serial" in text:
            last = f"{candidate} cannot import pyserial"
        else:
            last = f"{candidate} exited {rc}"

    raise ToolMissingError(
        f"could not ask the displays which they are: {last}. That needs pyserial "
        f"and the knomi-serial tree at {source} - on Debian, "
        f"'sudo apt install python3-serial'.",
        tool="discover",
        path=source,
    )


def _parse_discovered(payload: str) -> dict[str, WatcherDevice]:
    try:
        data = json.loads(payload)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}

    out: dict[str, WatcherDevice] = {}
    for raw_id, entry in data.items():
        if not isinstance(entry, dict):
            continue
        port = entry.get("port")
        if not raw_id or not port:
            continue
        device_id = str(raw_id).lower()
        out[device_id] = WatcherDevice(
            device_id=device_id,
            port=str(port),
            firmware_version=entry.get("fw"),
            build_variant=entry.get("var"),
            # It answered. That is what present means, and here it is not a
            # guess from a stat - the display spoke.
            present=True,
        )
    return out


def _as_sighting(display: PioType, device: WatcherDevice) -> Sighting:
    return Sighting(
        id=device.device_id,
        address=device.port,
        # A display that answered is running its application, not sitting in
        # a bootloader - STATE_KLIPPER directly, per Step 23's bootloader-
        # predicate rule. `device.firmware_version` is a *version string*
        # ("1.2.3"), not a firmware family name, so it cannot be fed to
        # `state_for_firmware` - that only works by accident, falling through
        # to the same default this states explicitly.
        state=STATE_KLIPPER,
        source=Listen.name,
        # `family` is what a caller needing per-family grouping (esptool's
        # discover(), which is called once per family) matches on - Sighting
        # itself carries no family field by design.
        detail={
            "fw": device.firmware_version,
            "var": device.build_variant,
            "family": display.name,
        },
    )


class Listen:
    """The broadcast listen pass, as a `discovery.spec.Source`.

    Scans every configured display family, not just the ones a caller happens
    to be flashing - the same breadth `discovery.byid.scan` gives the bus
    sources. A screen that answers is `ANSWERED`: it spoke, just now, with the
    ports free to ask it directly, which is the strongest confidence this
    package has.
    """

    name = "listen"
    label = "knomi broadcast listen"
    #: A display source only ever reports "it answered, running its
    #: application" - it has no bootloader state of its own to report.
    states: tuple[str, ...] = (STATE_KLIPPER,)
    #: Opens real serial ports and fights Klipper and the watcher for them if
    #: either still holds one - see the module docstring.
    needs_ports_free = True

    def sight(self, bench: Bench) -> list[Sighting]:
        from ...providers.pio import load as load_pio_types

        out: list[Sighting] = []
        for display in load_pio_types(bench.paths).values():
            try:
                found = discover(bench.paths, bench.settings, display)
            except UpdaterError:
                # Never fatal - see discover()'s own docstring. A family this
                # source cannot ask is simply absent from the result, the same
                # as a by-id scan finding nothing on the bus.
                continue
            out.extend(_as_sighting(display, device) for device in found.values())
        return out
