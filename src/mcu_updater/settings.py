"""Tool settings: the ``[updater]`` section of ``mcu-updater.cfg``.

Shares a file with the MCU registry. They were separate while the registry was
JSON - a settings key inside a dict keyed by board name would have been ugly and
liable to collide with a board called "settings" - but ``.cfg`` sections namespace
cleanly, so there is one file to find and one file to edit.

Reads and writes go through :mod:`cfgdoc`, which means editing a setting from the
panel does not throw away the ``[mcu ...]`` sections, the comments, or anything
else in the file.

Everything has a default, so the section is optional and may be partial.
"""

from __future__ import annotations

import dataclasses
import os
from typing import Any

from .cfgdoc import CfgDocument, parse_bool
from .errors import ConfigError

SECTION = "updater"


@dataclasses.dataclass
class Settings:
    #: 0 means "pass no -j flag at all", which is what the original script did.
    #: Opt in explicitly rather than silently changing everyone's build.
    make_jobs: int = 0

    #: `make clean` before every build. Keep this on: skipping it after a
    #: .config change is exactly how you get a stale-object mismatch and flash
    #: a subtly wrong binary.
    clean_before_build: bool = True

    #: Take the vendor's updated answers before compiling, when the profile a
    #: config was seeded from has changed since. On by default, because the
    #: alternative is building last month's config from a tree that has moved on
    #: and never being told. It only ever applies where the saved config still
    #: matches what the profile wrote - a config you have edited is your own and
    #: is left alone. One setting rather than a flag per entry point, so the
    #: panel, the CLI and a fleet build cannot disagree about what a build does.
    reseed_on_build: bool = True

    #: Units to stop before a write that needs one, most granular
    #: `[type ...]`/`[firmware ...]` override winning over this. `None` means
    #: every install got before this key existed: the per-provider built-in
    #: default, `("klipper",)` for a plain board. See `stop_services.py`.
    #:
    #: KIAUH multi-instance setups that used to write `service: klipper-1`
    #: now write `stop_services: klipper-1` here - see `load_settings`.
    stop_services: list[str] | None = None

    #: "moonraker" | "systemd" | "null". Only the agent honours "moonraker";
    #: the CLI always uses systemd since it has no Moonraker connection.
    service_backend: str = "moonraker"

    #: Echo commands and fake their output instead of running them.
    dry_run: bool = False

    #: Agent-only safety gate. The CLI ignores this entirely - it has always been
    #: able to flash and that did not change. Defaults off so installing an
    #: update never silently grants a browser the ability to reflash the printer.
    enable_flashing: bool = False

    #: Bypass the "is the printer busy?" check. Almost never what you want.
    allow_flash_while_printing: bool = False

    #: Per-job log ring buffer size, in lines.
    log_ring_size: int = 2000

    #: PlatformIO launcher, if it is somewhere `pio` on PATH and the standard
    #: ~/.platformio/penv/bin/pio will not find it.
    platformio_bin: str = ""

    #: Katapult's flashtool.py, if it is not at the ~/katapult/scripts/flashtool.py
    #: convention - a fork checked out elsewhere, say. `~` expands against this
    #: printer's home the same way a [firmware] source: does.
    flashtool_path: str = ""

    #: A UI-only cosmetic preference, not a behaviour one - the agent never reads
    #: this itself, it only stores and serves it back so every browser pointed at
    #: this printer agrees on the accent colour rather than each localStorage
    #: disagreeing. Empty string means "use the UI's own default", since a
    #: `<input type="color">` can never itself produce an empty value to mean
    #: that. `ui_` prefixed rather than a nested section: every other setting
    #: here is a flat top-level field, and SETTABLE/`_coerce_setting` assume one.
    ui_accent_color: str = ""

    @property
    def resolved_jobs(self) -> int:
        """make_jobs, or a sensible auto value if it was set to a negative."""
        if self.make_jobs < 0:
            return os.cpu_count() or 1
        return self.make_jobs

    def make_flags(self) -> list[str]:
        n = self.resolved_jobs
        return [f"-j{n}"] if n > 0 else []


BOOL_FIELDS = {
    "clean_before_build",
    "reseed_on_build",
    "dry_run",
    "enable_flashing",
    "allow_flash_while_printing",
}

#: Kept as a private alias: this module read `_BOOL_FIELDS` throughout before the
#: agent needed the same list to coerce a browser's values, and two hand-copied
#: lists of the same thing is how one grows a field the other does not.
_BOOL_FIELDS = BOOL_FIELDS
_INT_FIELDS = {"make_jobs", "log_ring_size"}
#: Public for the same reason as `BOOL_FIELDS`: registry.py's `_coerce_setting`
#: needs this same list to validate a browser's string-typed values.
STR_FIELDS = {"service_backend", "platformio_bin", "flashtool_path", "ui_accent_color"}
_STR_FIELDS = STR_FIELDS
_LIST_FIELDS = {"stop_services"}
_BACKENDS = ("moonraker", "systemd", "null")

#: Retired in favour of `stop_services`, but still read: a bare
#: `service: klipper-1` becomes a one-element `stop_services` list, so a
#: KIAUH multi-instance cfg does not silently stop the wrong unit the moment
#: this version is installed. Ignored (not "unknown key") whenever an
#: explicit `stop_services:` is also present - the new key always wins.
_LEGACY_SERVICE_KEY = "service"


def _read(path: str) -> CfgDocument:
    if not os.path.exists(path):
        return CfgDocument()
    try:
        with open(path, encoding="utf-8") as fh:
            return CfgDocument(fh.read())
    except OSError as exc:
        raise ConfigError(f"could not read {path}: {exc}", path=path) from exc


def load_settings(path: str) -> Settings:
    """Read the [updater] section. A missing file or section yields defaults.

    A *malformed value* still raises. Silently ignoring `dry_run = maybe` means
    the user's dry run quietly does not apply, which is the kind of surprise that
    ends up flashing a board.
    """
    s = Settings()
    doc = _read(path)

    # Appending a second [updater] block rather than editing the existing one is
    # an easy mistake, and first-wins would mean `enable_flashing: true` silently
    # doing nothing. Klipper refuses duplicate sections; so do we.
    if SECTION in doc.duplicate_sections:
        raise ConfigError(
            f"{path} has more than one [{SECTION}] section. Only the first is read, so "
            f"the settings in the later one would be silently ignored - merge them.",
            path=path,
        )

    if not doc.has_section(SECTION):
        return s

    options = doc.options(SECTION)
    has_stop_services = any(k.replace("-", "_") == "stop_services" for k in options)

    for key in options:
        name = key.replace("-", "_")
        if name == _LEGACY_SERVICE_KEY:
            continue  # handled below, once, after the explicit key is known
        raw = doc.get(SECTION, key)
        if raw is None:
            continue
        try:
            if name in _BOOL_FIELDS:
                value = parse_bool(raw, default=None)
                if value is None:
                    raise ValueError(f"expected a boolean, got {raw!r}")
                setattr(s, name, value)
            elif name in _INT_FIELDS:
                setattr(s, name, int(raw.strip()))
            elif name in _STR_FIELDS:
                text = raw.strip()
                if name == "ui_accent_color" and text:
                    text = f"#{text}"
                setattr(s, name, text)
            elif name in _LIST_FIELDS:
                setattr(s, name, doc.get_csv(SECTION, key))
            # Unknown keys are ignored rather than fatal: a newer version of the
            # tool may have written a setting this version does not know yet.
        except ValueError as exc:
            raise ConfigError(
                f"bad value for '{key}' in [{SECTION}] of {path}: {exc}", path=path, key=key
            ) from exc

    if not has_stop_services:
        legacy = doc.get(SECTION, _LEGACY_SERVICE_KEY)
        if legacy is not None and legacy.strip():
            s.stop_services = [legacy.strip()]

    if s.service_backend not in _BACKENDS:
        raise ConfigError(
            f"service_backend must be one of {'/'.join(_BACKENDS)}, got '{s.service_backend}'",
            path=path,
            key="service_backend",
        )
    return s


def save_settings(path: str, settings: Settings) -> None:
    """Write the [updater] section, leaving the rest of the file alone.

    Load-modify-write against what is on disk rather than a cached document, so
    this cannot clobber [mcu ...] sections written in the meantime.
    """
    doc = _read(path)
    for field in dataclasses.fields(settings):
        if field.name == "stop_services":
            continue  # trichotomy: handled below, `None` must stay absent
        value: Any = getattr(settings, field.name)
        if isinstance(value, bool):
            value = "true" if value else "false"
        elif field.name == "ui_accent_color" and value:
            # A value starting with '#' is an inline comment to this module's
            # own parser (mirroring Klipper's configparser, see the module
            # docstring) - written as `#2196f3` it would come back empty on
            # the very next load. Stored bare; `load_settings` adds the '#'
            # back on the way in.
            value = value.lstrip("#")
        doc.set(SECTION, field.name, value)

    if settings.stop_services is None:
        doc.remove_option(SECTION, "stop_services")
    else:
        doc.set(SECTION, "stop_services", ", ".join(settings.stop_services))
    # The legacy key is never round-tripped back out: reading it already
    # folded its meaning into `stop_services` above, so leaving it on disk
    # too would be two keys disagreeing the moment one of them is next edited.
    doc.remove_option(SECTION, _LEGACY_SERVICE_KEY)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(doc.render())
    os.replace(tmp, path)
