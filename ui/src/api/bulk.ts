// Pure selection logic for fw.build_all/flash_all/update_all, mirroring the
// fork's server/fwUpdater getters (bulkBuildTargets/bulkFlashTargets/
// bulkHasWork) - kept here rather than in the store so it is testable without
// mounting, the same reason api/jobs.ts's cancelIsImmediate is a plain
// function. No store import: every function takes targets[] as an argument.
//
// docs/agent-api.md's "Bulk operations" section is the source of truth. Two
// rules recorded there, and each is a bug if missed:
//
// - Read the actions' own `blocked`, never re-derive from `artifact.state` -
//   that is what keeps this preview and the agent's own batch in step.
// - `scope: "all"` overrides the *judgement*, never the physics: an offline
//   device is excluded under both scopes, because a flash needs the device on
//   the bus regardless of what the provenance says.
import type { Target, TargetDevice } from "./targets";

export type BulkOperation = "build_all" | "flash_all" | "update_all";
export type BulkScope = "stale" | "all";

/** Devices one target's flash would actually touch. Shared by TargetRow's
 * single-target preview (ActionButton's confirmation) and bulkFlashTargets
 * below, so the two can never drift apart. */
export function devicesToFlash(
  target: Pick<Target, "actions" | "devices">,
  scope: BulkScope,
): TargetDevice[] {
  const flash = target.actions.find((a) => a.id === "flash");
  // No flash action, or the agent already said there is nothing built to
  // write - agent-api.md's `no_artifact` on the flash action, read here
  // rather than re-derived from an artifact key.
  if (!flash || flash.blocked?.code === "no_artifact") return [];
  return target.devices.filter(
    (device) =>
      device.present && (scope === "all" || device.needs_flash === true),
  );
}

/** What a fleet build would touch - a preview only, never authoritative: the
 * agent re-decides when the call actually arrives. */
export function bulkBuildTargets(
  targets: Target[],
  scope: BulkScope,
): Target[] {
  return targets.filter((target) => {
    const build = target.actions.find((a) => a.id === "build");
    if (!build || build.blocked) return false;
    return scope === "all" || target.artifact.state !== "current";
  });
}

export interface BulkFlashEntry {
  type: string;
  id: string;
  name: string | null;
}

/** What a fleet flash would touch, flattened across every target - boards and
 * screens alike, since fw.flash_all selects both under one Klipper stop. */
export function bulkFlashTargets(
  targets: Target[],
  scope: BulkScope,
): BulkFlashEntry[] {
  return targets.flatMap((target) =>
    devicesToFlash(target, scope).map((device) => ({
      type: target.name,
      id: device.id,
      name: device.name,
    })),
  );
}

/**
 * Is there anything for this operation to do?
 *
 * Not "is the preview list empty" - for update_all, needing only the flash is
 * the normal case right after a rebuild: every artifact is current, so
 * nothing is stale to build, while the boards are still running last week's
 * binary. Gating on the build list alone would report "nothing to do" to a
 * fleet that is entirely behind.
 */
export function bulkHasWork(
  targets: Target[],
  operation: BulkOperation,
  scope: BulkScope,
): boolean {
  if (
    operation !== "flash_all" &&
    bulkBuildTargets(targets, scope).length > 0
  ) {
    return true;
  }
  if (operation === "build_all") return false;
  return bulkFlashTargets(targets, scope).length > 0;
}
