import { afterEach, describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import TargetRow from "./TargetRow.vue";
import type { Action, Target } from "../api/targets";
import { state } from "../store/agent";
import type { Job } from "../api/jobs";

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

const flashAction: Action = {
  id: "flash",
  label: "Flash",
  method: "fw.flash_all",
  params: { name: "bttebb36", scope: "stale" },
  blocked: null,
};

const runningBuild: Job = {
  id: "job-1",
  kind: "build",
  params: {},
  state: "running",
  created: 0,
  started: 0,
  finished: null,
  duration: null,
  progress: null,
  result: null,
  error: null,
  cancel_requested: false,
  log_next: 0,
  log_dropped: 0,
};

describe("TargetRow", () => {
  afterEach(() => {
    state.job = null;
  });

  it("renders target-level actions and previews the devices a flash would write", () => {
    const target: Target = { ...mcuTarget, actions: [flashAction] };
    const wrapper = mount(TargetRow, { props: { target } });
    const flashButton = wrapper
      .findAll("button")
      .find((b) => b.text() === "Flash");
    expect(flashButton).toBeTruthy();
    expect(flashButton?.attributes("disabled")).toBeUndefined();
  });

  it("disables row actions while a job is running, without touching blocked", () => {
    state.job = runningBuild;
    const target: Target = { ...mcuTarget, actions: [flashAction] };
    const wrapper = mount(TargetRow, { props: { target } });
    const actionButtons = wrapper
      .findAll("button")
      .filter((b) => b.text() === "Flash");
    expect(actionButtons[0]?.attributes("disabled")).toBeDefined();
    expect(wrapper.text()).toContain("build is already running");
  });

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
