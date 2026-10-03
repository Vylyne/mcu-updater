import { afterEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import TargetRow from "./TargetRow.vue";
import UiIcon from "./UiIcon.vue";
import type { Action, Target } from "../api/targets";
import * as store from "../store/agent";
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
  source: null,
  extras: [],
  devices_note: null,
};

const platformioTarget: Target = {
  provider: "platformio",
  name: "knomi",
  descriptor: "esp32dev",
  firmware: "knomi_serial",
  artifact: {
    state: "stale",
    tone: "warn",
    label: "Needs a build",
    reason: "source_changed",
  },
  profile: null,
  needs_flash: null,
  actions: [],
  devices: [],
  source: { path: "/home/pi/knomi_serial", version: "d34db33", dirty: false },
  extras: [
    {
      seam: "helper",
      name: "knomi_serial",
      key: "module_version",
      label: "Module",
      value: "0.5.0",
    },
  ],
  devices_note: "Nothing is declared under [fake_dev ...].",
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
    // Header actions render icon-only (see ActionButton.vue's default
    // variant), so a flash action is found by its title, not its text.
    const flashButton = wrapper
      .findAll("button")
      .find((b) => b.attributes("title") === "Flash");
    expect(flashButton).toBeTruthy();
    expect(flashButton?.attributes("disabled")).toBeUndefined();
  });

  it("disables row actions while a job is running, without touching blocked", () => {
    state.job = runningBuild;
    const target: Target = { ...mcuTarget, actions: [flashAction] };
    const wrapper = mount(TargetRow, { props: { target } });
    // Icon actions carry their reason as a title (a tooltip on hover, same
    // as the target row's action hint), not as visible text.
    const flashButton = wrapper
      .findAll("button")
      .find((b) => b.attributes("title") === "build is already running");
    expect(flashButton?.attributes("disabled")).toBeDefined();
  });

  it("renders an MCU target's name, provider and device", () => {
    const wrapper = mount(TargetRow, { props: { target: mcuTarget } });
    expect(wrapper.text()).toContain("bttebb36");
    expect(wrapper.text()).toContain("kconfig_make");
    expect(wrapper.text()).toContain("Up to date");
    expect(wrapper.text()).toContain("mcu EBBT0");
    // The verdict span carries its own :data-tone and would pass this even
    // if the leading status icon lost its tone (see 4068a92) - anchor to the
    // icon itself rather than to any [data-tone='ok'] match in the row.
    const deviceIcon = wrapper.findComponent(UiIcon);
    expect(deviceIcon.find("svg").attributes("data-tone")).toBe("ok");
  });

  it("says what the agent says when a type lists no devices", () => {
    const wrapper = mount(TargetRow, { props: { target: platformioTarget } });
    expect(wrapper.text()).toContain(
      "Nothing is declared under [fake_dev ...].",
    );
  });

  it("does not invent its own empty-row wording", () => {
    // The sentence is the agent's, so a new builder or helper never needs a
    // UI release to explain an empty row.
    const target: Target = {
      ...mcuTarget,
      devices: [],
      devices_note: "Something only the agent knows.",
    };
    const wrapper = mount(TargetRow, { props: { target } });
    expect(wrapper.text()).toContain("Something only the agent knows.");
    expect(wrapper.text()).not.toContain("No serial devices are tracked");
  });

  it("renders every extra as label and value, without knowing any of them", () => {
    const target: Target = {
      ...mcuTarget,
      extras: [
        ...platformioTarget.extras,
        {
          seam: "builder",
          name: "cmake",
          key: "anything",
          label: "Board rev",
          value: 3,
        },
      ],
    };
    const wrapper = mount(TargetRow, { props: { target } });
    expect(wrapper.text()).toContain("Module 0.5.0");
    expect(wrapper.text()).toContain("Board rev 3");
    expect(wrapper.findAll("[data-extra]")).toHaveLength(2);
  });

  it("leaves out an extra with no value rather than showing a bare label", () => {
    const target: Target = {
      ...mcuTarget,
      extras: [
        {
          seam: "helper",
          name: "any",
          key: "module_version",
          label: "Module",
          value: null,
        },
        {
          seam: "builder",
          name: "cmake",
          key: "anything",
          label: "Board rev",
          value: 3,
        },
      ],
    };
    const wrapper = mount(TargetRow, { props: { target } });
    expect(wrapper.findAll("[data-extra]")).toHaveLength(1);
    expect(wrapper.text()).not.toContain("Module");
  });

  it("renders a row with no extras without an empty caption", () => {
    const wrapper = mount(TargetRow, { props: { target: mcuTarget } });
    expect(wrapper.findAll("[data-extra]")).toHaveLength(0);
  });

  it("offers a scope override on a flash whose stale preview is empty", async () => {
    // mcuTarget's device already has needs_flash: false, so the "stale"
    // preview is empty even though a device is present - exactly the
    // "nothing to do" gap the override switch exists for.
    const target: Target = { ...mcuTarget, actions: [flashAction] };
    const wrapper = mount(TargetRow, { props: { target } });
    const flashButton = wrapper
      .findAll("button")
      .find((b) => b.attributes("title") === "Flash");
    await flashButton!.trigger("click");

    let confirmButton = wrapper
      .findAll("button")
      .find((b) => b.text() === "Flash bttebb36");
    expect(confirmButton?.attributes("disabled")).toBeDefined();

    await wrapper.get('input[type="checkbox"]').setValue(true);
    expect(wrapper.text()).toContain("mcu EBBT0");
    confirmButton = wrapper
      .findAll("button")
      .find((b) => b.text() === "Flash bttebb36");
    expect(confirmButton?.attributes("disabled")).toBeUndefined();
  });

  it("confirms the overflow menu's build-and-flash before running it", async () => {
    const spy = vi.spyOn(store, "invokeAction").mockResolvedValue(true);
    const updateAction: Action = {
      id: "update",
      label: "Build and flash",
      method: "fw.update_all",
      params: { name: "bttebb36", scope: "stale" },
      blocked: null,
    };
    const target: Target = {
      ...mcuTarget,
      actions: [flashAction, updateAction],
    };
    const wrapper = mount(TargetRow, { props: { target } });
    await wrapper.get('[aria-label="More actions"]').trigger("click");
    await wrapper
      .findAll("button")
      .find((b) => b.text() === "Build and flash")!
      .trigger("click");

    expect(spy).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain("Build and flash bttebb36");
    // The menu's instance gets the switch too, not only the header's.
    expect(wrapper.find('input[type="checkbox"]').exists()).toBe(true);
    spy.mockRestore();
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
