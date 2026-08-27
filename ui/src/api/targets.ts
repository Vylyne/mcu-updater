// Wire types for `targets[]` (fw.status) and fw.target.get's `target`, hand-
// mirrored from docs/agent-api.md the same way the Mainsail panel's types.ts
// is - see tests/test_agent_methods.py for the Python half of that contract.

export type Provider = "kconfig_make" | "platformio";

export type Tone = "ok" | "unknown" | "attention";

export interface ArtifactSummary {
  state: string;
  tone: Tone;
  label: string;
  reason: string | null;
}

/** Same shape a failed call's `error` carries - docs/agent-api.md's "A
 * requirement is only visible as `blocked`" note. `null` means go. */
export interface Blocked {
  code: string;
  message: string;
  data?: unknown;
}

/** "This action takes an option, fetch them when you open it." Call `method`
 * with `params` to get the list; put what the user picks into `params[param]`
 * before invoking the action itself. */
export interface Choices {
  method: string;
  params: Record<string, unknown>;
  param: string;
}

export interface Action {
  id: string;
  label: string;
  method: string;
  params: Record<string, unknown>;
  blocked: Blocked | null;
  choices?: Choices;
}

export type ProfileReason = null | "unmanaged" | "customised" | "seed_moved";

export interface ProfileChange {
  symbol: string;
  was: string | null;
  now: string | null;
  line: string;
}

/** Mirrors the fork's `FwProfileVerdict`
 * (mainsail/src/store/server/fwUpdater/types.ts) - the third verdict a row
 * can carry: do the inputs still say what the profile said. `managed: false`
 * (every type predating profiles) means no chip at all, not a chip saying
 * "unmanaged" on every row. */
export interface ProfileVerdict {
  state: string;
  tone: Tone;
  label: string;
  reason: ProfileReason;
  managed: boolean;
  profile: string | null;
  custom: boolean;
  parent: string | null;
  changes?: ProfileChange[];
}

export interface TargetDevice {
  id: string;
  name: string | null;
  present: boolean;
  state: string;
  path: string | null;
  version: string | null;
  confidence: string | null;
  needs_flash: boolean | null;
  tone: Tone;
  label: string;
  reason: string | null;
  actions: Action[];
}

/** One `targets[]` row - `TypeStatus` and `DisplayStatus` in one shape.
 * `extra` is present only on a display; an MCU row carries none of its
 * fields at all, deliberately (docs/agent-api.md's "targets" section). */
export interface Target {
  provider: Provider;
  name: string;
  descriptor: string;
  firmware: string | null;
  artifact: ArtifactSummary;
  profile: ProfileVerdict | null;
  needs_flash: boolean | null;
  devices: TargetDevice[];
  actions: Action[];
  extra?: {
    module_version: string | null;
    source_version: string | null;
    source_dirty: boolean | null;
    klipper_section: string;
    reachable: boolean;
  };
}

/** The compound key a target needs: nothing stops an MCU type and a display
 * sharing a `name` across their separate config files. */
export function targetKey(target: Pick<Target, "provider" | "name">): string {
  return `${target.provider}:${target.name}`;
}

/** One entry of `fw.status`'s `bus` - docs/agent-api.md's "BusDevice".
 * `tracked_by` is `null` for a device on the bus that no MCU type has
 * claimed yet - the "new board, want to track it?" case. */
export interface BusDevice {
  fw: string;
  chipset: string | null;
  serial: string;
  path: string;
  state: string;
  tracked_by: string | null;
}
