"""The agent's JSON-RPC surface.

Every method takes one object and returns one object, and **every method returns
in well under a second**. That is a hard rule, not a guideline: Moonraker awaits
our reply with no timeout, so anything slow would hold a front end's HTTP request
open. Long-running work (build, flash) returns a job id immediately instead -
those arrive in a later phase.

The shapes here are the contract with the Mainsail panel. They are documented in
``docs/agent-api.md`` and version-gated by ``fw.ping``'s ``api_version``.

Split by surface into one mixin per file - ``status.py``, ``registry.py``,
``build.py``, ``flash.py``, ``profiles.py``, ``bulk.py`` - because the class had
grown past 3,800 lines with no seam in it at all. ``Api`` below is the
composition; nothing here is more than that. Each mixin freely calls ``self.``
methods defined in the others - they share one instance's state (``self.paths``,
``self._call``, ``self.runner``, ...) exactly as they did as one class.
"""

from __future__ import annotations

from .build import BuildMixin
from .bulk import BulkMixin
from .flash import FlashMixin
from .profiles import ProfilesMixin
from .registry import RegistryMixin
from .status import StatusMixin, _running_sha  # noqa: F401 - re-exported for tests


class Api(StatusMixin, RegistryMixin, BuildMixin, FlashMixin, ProfilesMixin, BulkMixin):
    """Read-only view of the tool's state, exposed over JSON-RPC."""
