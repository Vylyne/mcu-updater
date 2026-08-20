"""Klipper/Katapult firmware management.

Layered deliberately so the same logic serves both the interactive CLI and a
long-running Moonraker agent:

  errors/paths/settings/config/devices  - pure, no side effects beyond the FS
  lock/build/flash/service              - do real work, report via a callback
  providers                             - the build systems, behind one protocol
  cli/tui                               - the ONLY place input()/sys.exit()/
                                          print() are allowed to appear

Anything importable by the agent must never block on a terminal.
"""

from __future__ import annotations

__version__ = "0.9.0"

# Bumped only on a breaking change to the agent's JSON-RPC surface. The Mainsail
# panel refuses to render if it sees an API version it doesn't know.
#
# 2: fields were *removed*. `screens[].mac`/`flashed_at`/`moved_from`/`moved_at`
#    and `targets[].extra.moved` went with the identity tracking, and a panel
#    written against 1 reads `extra?.moved.length` - where the `?.` guards
#    `extra` and not `moved`, so an absent key is a TypeError rather than a
#    missing warning. Additions do not need a bump; removals do, which is what
#    this number is for and what the removal should have carried at the time.
# 3: `fw.display.list` and `fw.display.build` are gone (use `fw.device.list`
#    and `fw.build`, which already did the same work), and `targets[].kind`
#    is gone too - `targets[].provider` ("kconfig_make" | "platformio")
#    already said the same thing without a second vocabulary to keep in step.
#    A panel switching on `kind` gets a KeyError, not a wrong answer.
API_VERSION = 3

AGENT_NAME = "mcu_updater"

__all__ = ["__version__", "API_VERSION", "AGENT_NAME"]
