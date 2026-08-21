"""The shape every mixin needs from the composed ``Api``.

Each of ``status.py`` / ``registry.py`` / ``build.py`` / ``flash.py`` /
``profiles.py`` / ``bulk.py`` defines one mixin, and every mixin freely calls
``self.`` attributes and methods the *other* mixins define - they are one
instance's state and behaviour, split across files for size rather than for
any real separation. A bare mixin has nothing to check that against, so mypy
sees `"FlashMixin" has no attribute "paths"` on every cross-file call.

This ``Protocol`` is that missing shape, ``TYPE_CHECKING``-only: nothing here
runs, and ``Api`` in ``__init__.py`` never mentions it. Each mixin inherits it
under ``if TYPE_CHECKING`` alone, so mypy resolves ``self.foo`` through it
while runtime MRO is exactly the six mixins, unchanged.

Kept to what is actually called cross-file (verified by grepping every
``self.NAME`` against what each mixin itself defines) - not the full 100-some
method API, which would make this drift into a second copy of the class to
maintain by hand for no benefit anything here uses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional, Protocol

from ... import flashers
from ...config import Registry
from ...jobs import JobRunner
from ...paths import Paths
from ...settings import Settings
from ...states import DeviceStatus


class _Api(Protocol):
    # -- instance state, set in StatusMixin.__init__ -----------------------
    paths: Paths
    runner: Optional[Any]
    _call: Optional[Any]
    _log: Optional[Any]
    _on_change: Optional[Any]
    _kconfig_sessions: Optional[Any]
    _object_names: Optional[list[str]]
    _object_names_at: float

    # -- DFU reason codes and pairing TTL, status.py --------------------
    DFU_NO_TOOL: str
    DFU_PERMISSION_DENIED: str
    DFU_NONE: str
    DFU_AMBIGUOUS: str
    PAIRING_TTL: float

    # -- status.py -----------------------------------------------------
    def settings(self) -> Settings: ...
    def registry(self) -> Registry: ...
    def _fw_names(self) -> tuple[str, ...]: ...
    def artifact(self, mcu_type: str, fw: str) -> dict[str, Any]: ...
    def pio_status(self) -> list[dict[str, Any]]: ...
    def pio_types(self) -> dict: ...
    def device_list(self, args: dict) -> dict[str, Any]: ...
    def mcu_info(self) -> dict[str, dict[str, str]]: ...
    def flash_state(
        self,
        serial: str,
        info: dict[str, dict[str, str]],
        fw_head: Optional[str],
        *,
        state: Optional[str] = None,
        artifact_sha: Optional[str] = None,
        flashlog: Optional[Any] = None,
    ) -> dict[str, Any]: ...
    @staticmethod
    def _screen_device_status(screen: dict[str, Any]) -> DeviceStatus: ...
    def _log_reporter(self, stream: str, line: str) -> None: ...
    def _printer_activity(self) -> dict[str, Optional[str]]: ...
    def _await_klippy_ready(
        self,
        reporter: Any,
        *,
        timeout: Optional[float] = None,
        after_restart: Optional[float] = None,
    ) -> Optional[str]: ...
    def _call_for_service(self, method: str, params: Any) -> Any: ...

    # -- registry.py -----------------------------------------------------
    @staticmethod
    def _require_str(args: dict, key: str) -> str: ...
    def _changed(self) -> None: ...

    # -- build.py ----------------------------------------------------------
    def _require_runner(self) -> JobRunner: ...
    def _provider_of(self, name: str) -> str: ...
    def kconfig_available(
        self, families: Optional[dict[str, Any]] = None
    ) -> dict[str, bool]: ...

    # -- bulk.py -------------------------------------------------------
    def _do_flash_all(
        self, ctx: Any, targets: list[flashers.FlashTarget]
    ) -> dict[str, Any]: ...


#: What every mixin actually inherits from at runtime: nothing. `_Api` only
#: exists for mypy, and a bare Protocol isn't a valid base at runtime either
#: way, so each mixin does `class FooMixin(_Base):` rather than repeating this
#: conditional six times.
if TYPE_CHECKING:
    _Base = _Api
else:
    _Base = object
