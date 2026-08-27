// Wire types for `fw.settings.get`/`fw.settings.set` - docs/agent-api.md's
// settings section. Mirrors settings.py's Settings dataclass field-for-field;
// see registry.py's SETTABLE for which of these a browser may actually change.

export interface UpdaterSettings {
  make_jobs: number;
  clean_before_build: boolean;
  reseed_on_build: boolean;
  stop_services: string[] | null;
  service_backend: string;
  dry_run: boolean;
  enable_flashing: boolean;
  allow_flash_while_printing: boolean;
  log_ring_size: number;
  platformio_bin: string;
  flashtool_path: string;
}

/** registry.py's `SETTABLE` - the only keys `fw.settings.set` accepts from a
 * browser. `stop_services`/`service_backend` describe how the host is wired
 * and are deliberately absent - see registry.py:26-35's own reasoning. Kept
 * here as a literal tuple, not re-derived, so tests/test_ui_contract.py's
 * fw.* scan and a human reading this file see the same list the agent
 * enforces. */
export const SETTABLE_KEYS = [
  "make_jobs",
  "clean_before_build",
  "reseed_on_build",
  "dry_run",
  "enable_flashing",
  "allow_flash_while_printing",
  "log_ring_size",
] as const satisfies readonly (keyof UpdaterSettings)[];

export type SettableKey = (typeof SETTABLE_KEYS)[number];

/** Changing either takes effect immediately in what `fw.ping` reports, but
 * NOT in what Moonraker will actually dispatch - `connection.register_remote_method`
 * runs once at handshake (agent/service.py's `_handshake`), so a flash method
 * flipped on here 404s with -32601 until the agent's next reconnect. See
 * docs/standalone-ui.md. */
export const RECONNECT_REQUIRED_KEYS: ReadonlySet<SettableKey> = new Set([
  "enable_flashing",
  "allow_flash_while_printing",
]);
