import { describe, expect, it } from "vitest";
import {
  bulkBuildTargets,
  bulkFlashTargets,
  bulkHasWork,
  devicesToFlash,
} from "./bulk";
import type { Action, Target, TargetDevice } from "./targets";

function device(overrides: Partial<TargetDevice> = {}): TargetDevice {
  return {
    id: "dev-1",
    name: null,
    present: true,
    state: "klipper",
    path: null,
    version: null,
    confidence: null,
    needs_flash: false,
    tone: "ok",
    label: "Running",
    reason: null,
    actions: [],
    ...overrides,
  };
}

function action(overrides: Partial<Action> = {}): Action {
  return {
    id: "build",
    label: "Build",
    method: "fw.build",
    params: {},
    blocked: null,
    ...overrides,
  };
}

function target(overrides: Partial<Target> = {}): Target {
  return {
    provider: "kconfig_make",
    name: "bttebb36",
    descriptor: "stm32g0b1xx",
    firmware: "klipper",
    artifact: {
      state: "current",
      tone: "ok",
      label: "Up to date",
      reason: null,
    },
    profile: null,
    needs_flash: false,
    devices: [],
    actions: [],
    ...overrides,
  };
}

describe("devicesToFlash", () => {
  it("excludes offline devices under scope all - overrides judgement, not physics", () => {
    const t = target({
      actions: [action({ id: "flash", method: "fw.flash_all" })],
      devices: [device({ present: false, needs_flash: true })],
    });
    expect(devicesToFlash(t, "all")).toHaveLength(0);
  });

  it("under stale scope, keeps only devices needing a flash", () => {
    const t = target({
      actions: [action({ id: "flash", method: "fw.flash_all" })],
      devices: [
        device({ id: "a", needs_flash: true }),
        device({ id: "b", needs_flash: false }),
      ],
    });
    expect(devicesToFlash(t, "stale").map((d) => d.id)).toEqual(["a"]);
  });

  it("under scope all, includes every present device regardless of needs_flash", () => {
    const t = target({
      actions: [action({ id: "flash", method: "fw.flash_all" })],
      devices: [device({ id: "a", needs_flash: false, present: true })],
    });
    expect(devicesToFlash(t, "all").map((d) => d.id)).toEqual(["a"]);
  });

  it("is empty when there is no flash action at all", () => {
    const t = target({ devices: [device({ needs_flash: true })] });
    expect(devicesToFlash(t, "all")).toHaveLength(0);
  });

  it("is empty when the flash action is blocked on no_artifact - nothing built to write", () => {
    const t = target({
      actions: [
        action({
          id: "flash",
          method: "fw.flash_all",
          blocked: { code: "no_artifact", message: "Build it first." },
        }),
      ],
      devices: [device({ needs_flash: true })],
    });
    expect(devicesToFlash(t, "all")).toHaveLength(0);
  });
});

describe("bulkBuildTargets", () => {
  it("keeps a target whose build action is unblocked and stale", () => {
    const t = target({
      actions: [action({ id: "build" })],
      artifact: {
        state: "stale",
        tone: "attention",
        label: "Needs a build",
        reason: null,
      },
    });
    expect(bulkBuildTargets([t], "stale")).toEqual([t]);
  });

  it("drops a current target under scope stale", () => {
    const t = target({ actions: [action({ id: "build" })] });
    expect(bulkBuildTargets([t], "stale")).toHaveLength(0);
  });

  it("keeps a current target under scope all", () => {
    const t = target({ actions: [action({ id: "build" })] });
    expect(bulkBuildTargets([t], "all")).toEqual([t]);
  });

  it("drops a target whose build action is blocked", () => {
    const t = target({
      actions: [
        action({
          id: "build",
          blocked: { code: "no_config", message: "run menuconfig" },
        }),
      ],
    });
    expect(bulkBuildTargets([t], "all")).toHaveLength(0);
  });

  it("drops a target with no build action at all (a display with no build)", () => {
    const t = target({ provider: "platformio", actions: [] });
    expect(bulkBuildTargets([t], "all")).toHaveLength(0);
  });
});

describe("bulkFlashTargets", () => {
  it("flattens devices across targets with their type name attached", () => {
    const t1 = target({
      name: "a",
      actions: [action({ id: "flash", method: "fw.flash_all" })],
      devices: [device({ id: "d1", needs_flash: true })],
    });
    const t2 = target({
      name: "b",
      actions: [action({ id: "flash", method: "fw.flash_all" })],
      devices: [device({ id: "d2", needs_flash: true })],
    });
    expect(
      bulkFlashTargets([t1, t2], "stale").map((e) => `${e.type}:${e.id}`),
    ).toEqual(["a:d1", "b:d2"]);
  });
});

describe("bulkHasWork", () => {
  it("build_all has work only from the build list", () => {
    const t = target({
      actions: [action({ id: "build" })],
      artifact: { state: "stale", tone: "attention", label: "x", reason: null },
    });
    expect(bulkHasWork([t], "build_all", "stale")).toBe(true);
    expect(bulkHasWork([target()], "build_all", "stale")).toBe(false);
  });

  it("flash_all ignores the build list entirely", () => {
    const t = target({
      actions: [action({ id: "build" })],
      artifact: { state: "stale", tone: "attention", label: "x", reason: null },
    });
    expect(bulkHasWork([t], "flash_all", "stale")).toBe(false);
  });

  it("update_all has work from the flash list alone - the normal case right after a rebuild", () => {
    const t = target({
      actions: [
        action({ id: "build" }),
        action({ id: "flash", method: "fw.flash_all" }),
      ],
      devices: [device({ needs_flash: true })],
    });
    // Artifact is current (nothing to build) but a device still needs flashing.
    expect(bulkHasWork([t], "update_all", "stale")).toBe(true);
  });
});
