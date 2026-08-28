import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import SummaryChips from "./SummaryChips.vue";
import type { Target, TargetDevice } from "../api/targets";

function device(overrides: Partial<TargetDevice> = {}): TargetDevice {
  return {
    id: "d",
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

function target(overrides: Partial<Target> = {}): Target {
  return {
    provider: "kconfig_make",
    name: "t",
    descriptor: "x",
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

describe("SummaryChips", () => {
  it("says all up to date with nothing stale", () => {
    const wrapper = mount(SummaryChips, { props: { targets: [target()] } });
    expect(wrapper.text()).toContain("All up to date");
  });

  it("counts stale over targets, not types - a display counts too", () => {
    const stale = target({
      provider: "platformio",
      artifact: { state: "stale", tone: "attention", label: "x", reason: null },
    });
    const wrapper = mount(SummaryChips, {
      props: { targets: [target(), stale] },
    });
    expect(wrapper.text()).toContain("1/2 need a rebuild");
  });

  it("claims all flashed only when every device answered", () => {
    const t = target({ devices: [device({ needs_flash: false })] });
    const wrapper = mount(SummaryChips, { props: { targets: [t] } });
    expect(wrapper.text()).toContain("All flashed");
  });

  it("does not claim all flashed when a device's status is unknown", () => {
    const t = target({ devices: [device({ needs_flash: null })] });
    const wrapper = mount(SummaryChips, { props: { targets: [t] } });
    expect(wrapper.text()).not.toContain("All flashed");
  });

  it("shows the offline count separately from needs-flash", () => {
    const t = target({
      devices: [
        device({ present: false }),
        device({ id: "d2", needs_flash: true }),
      ],
    });
    const wrapper = mount(SummaryChips, { props: { targets: [t] } });
    expect(wrapper.text()).toContain("1 offline");
    expect(wrapper.text()).toContain("1 need flashing");
  });
});
