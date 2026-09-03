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
  is_mcu: boolean;
  ignored: boolean;
}

/** Whether a `BusDevice` is a Roadrunner - read straight off the same 8
 * fields every other discovery source uses, never a Roadrunner-specific wire
 * field (there isn't one, deliberately - see this file's `BusDevice` note
 * and docs/decisions.md's "a union of every source's facts would be a fourth
 * vocabulary to keep in step"). The generic by-id scanner splits a
 * Roadrunner's `usb-Vylyne_Roadrunner_<serial>-if00` descriptor into exactly
 * `fw: "Vylyne"`, `chipset: "Roadrunner"`, with `serial` overwritten by the
 * raw USB hardware serial descriptor, which for Roadrunner *is* its
 * canonical identity string. */
export function isRoadrunnerDevice(
  device: Pick<BusDevice, "fw" | "chipset">,
): boolean {
  return device.fw === "Vylyne" && device.chipset === "Roadrunner";
}

// Bench-validated in this plan's Task 3: `RR-UNPROVISIONED-<16 uppercase
// hex>` before provisioning, `RR-<26 uppercase Crockford-base32 chars>`
// after - Crockford base32 excludes I/L/O/U, hence the split character
// classes rather than a plain [0-9A-Z] run.
const ROADRUNNER_UNPROVISIONED_RE = /^RR-UNPROVISIONED-[0-9A-F]{16}$/;
const ROADRUNNER_PROVISIONED_RE = /^RR-[0-9A-HJKMNP-TV-Z]{26}$/;
const ROADRUNNER_UNPROVISIONED_PREFIX = "RR-UNPROVISIONED-";

export type RoadrunnerIdentityState = "unprovisioned" | "provisioned" | null;

/** A Roadrunner's provisioning state, read straight out of its serial
 * string. `BusDevice.state` is a red herring here - it falls back to
 * `fw.toLowerCase()` for a source with no state vocabulary of its own, i.e.
 * literally `"vylyne"`, and carries no Roadrunner-specific meaning. `null`
 * means the serial matches neither shape, the same refusal
 * `fw.roadrunner.provision`/`.clear` would give it server-side. */
export function roadrunnerIdentityState(
  serial: string,
): RoadrunnerIdentityState {
  if (ROADRUNNER_UNPROVISIONED_RE.test(serial)) return "unprovisioned";
  if (ROADRUNNER_PROVISIONED_RE.test(serial)) return "provisioned";
  return null;
}

/** The 16 trailing hex characters of an unprovisioned serial - the
 * "diagnostic UID" a provision confirmation names alongside the serial
 * itself. Purely a label lifted back out of a string the agent already
 * returned, never sent anywhere on its own. `null` for anything else,
 * provisioned included - there is no separate diagnostic identity once a
 * board is provisioned. */
export function roadrunnerDiagnosticUid(serial: string): string | null {
  return serial.startsWith(ROADRUNNER_UNPROVISIONED_PREFIX)
    ? serial.slice(ROADRUNNER_UNPROVISIONED_PREFIX.length)
    : null;
}

/** The name shown in a device row - trims an unprovisioned board's 16-hex
 * flash-UID suffix, since it's already shown in full via the by-id path
 * underneath and spelled out explicitly as the diagnostic UID in the
 * provision confirmation dialog. A provisioned Roadrunner's serial has no
 * such suffix and is returned unchanged, as is every non-Roadrunner
 * device's serial. */
export function roadrunnerDisplaySerial(serial: string): string {
  return serial.startsWith(ROADRUNNER_UNPROVISIONED_PREFIX)
    ? ROADRUNNER_UNPROVISIONED_PREFIX.slice(0, -1)
    : serial;
}

/** `fw.status`'s `locked_by` - non-null while a CLI build or flash holds the
 * host lock. Distinct from `state.job`: this is activity this UI did not
 * start and cannot cancel, only wait out. */
export interface Lock {
  pid: number;
  label: string;
  since: number;
}
