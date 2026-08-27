import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import TargetRow from "./TargetRow.vue";
import type { Target } from "../api/targets";

const mcuTarget: Target = {
  provider: "kconfig_make",
  name: "bttebb36",
  descriptor: "stm32g0b1xx",
  firmware: "klipper",
  artifact: { state: "current", tone: "ok", label: "Up to date", reason: null },
  profile: null,
  needs_flash: false,
  actions: [],
  devices: [
    {
      id: "230048-if00",
      name: "mcu EBBT0",
      present: true,
      state: "klipper",
      path: "/dev/serial/by-id/230048-if00",
      version: "v0.13.0",
      confidence: null,
      needs_flash: false,
      tone: "ok",
      label: "Running",
      reason: null,
      actions: [],
    },
  ],
};

const displayTarget: Target = {
  provider: "platformio",
  name: "knomi",
  descriptor: "esp32dev",
  firmware: null,
  artifact: {
    state: "stale",
    tone: "attention",
    label: "Needs a build",
    reason: "source_changed",
  },
  profile: null,
  needs_flash: null,
  actions: [],
  devices: [],
  extra: {
    module_version: "0.5.0",
    source_version: "d34db33",
    source_dirty: false,
    klipper_section: "knomi_serial",
    reachable: true,
  },
};

describe("TargetRow", () => {
  it("renders an MCU target's name, provider and device", () => {
    const wrapper = mount(TargetRow, { props: { target: mcuTarget } });
    expect(wrapper.text()).toContain("bttebb36");
    expect(wrapper.text()).toContain("kconfig_make");
    expect(wrapper.text()).toContain("Up to date");
    expect(wrapper.text()).toContain("mcu EBBT0");
    expect(wrapper.find("[data-tone='ok']").exists()).toBe(true);
  });

  it("shows the display-specific hint when there are no devices", () => {
    const wrapper = mount(TargetRow, { props: { target: displayTarget } });
    expect(wrapper.text()).toContain("knomi_serial");
    expect(wrapper.text()).not.toContain("No serial devices");
  });

  it("says Klipper was unreachable rather than implying a confirmed empty list", () => {
    // docs/agent-api.md's fw.device.list section: "no displays configured"
    // and "we could not ask Klipper" must not look the same.
    const target: Target = {
      ...displayTarget,
      extra: { ...displayTarget.extra!, reachable: false },
    };
    const wrapper = mount(TargetRow, { props: { target } });
    expect(wrapper.text()).toContain("Could not reach Klipper");
    expect(wrapper.text()).not.toContain("No screens found");
  });

  it("shows the MCU-generic hint when there are no devices and no extra", () => {
    const target: Target = { ...mcuTarget, devices: [] };
    const wrapper = mount(TargetRow, { props: { target } });
    expect(wrapper.text()).toContain("No serial devices are tracked");
  });

  it("toggles the detail panel without a connected client", async () => {
    const wrapper = mount(TargetRow, { props: { target: mcuTarget } });
    const button = wrapper.get("button");
    expect(button.text()).toBe("Show detail");
    await button.trigger("click");
    expect(button.text()).toBe("Hide detail");
    await button.trigger("click");
    expect(button.text()).toBe("Show detail");
  });
});
