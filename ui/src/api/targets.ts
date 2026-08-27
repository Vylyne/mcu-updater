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
  // fw.build/fw.flash/etc, {id, label, method, params, blocked, choices?} -
  // the Phase 5 action renderer's shape. Left untyped here since Phase 4
  // renders targets[] only and does not read into an action yet.
  actions: unknown[];
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
  profile: Record<string, unknown> | null;
  needs_flash: boolean | null;
  devices: TargetDevice[];
  actions: unknown[];
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
