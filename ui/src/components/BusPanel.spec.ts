import { afterEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import BusPanel from "./BusPanel.vue";
import * as store from "../store/agent";
import { state } from "../store/agent";
import type { BusDevice, Target } from "../api/targets";

function makeTarget(name: string): Target {
  return {
    provider: "kconfig_make",
    name,
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
    actions: [],
    devices: [],
  };
}

const mcuDevice: BusDevice = {
  fw: "unknown",
  chipset: "stm32g0b1xx",
  serial: "usb-Klipper_stm32g0b1xx_1234-if00",
  path: "/dev/serial/by-id/usb-Klipper_stm32g0b1xx_1234-if00",
  state: "unknown",
  tracked_by: null,
  is_mcu: true,
  ignored: false,
};

const nonMcuDevice: BusDevice = {
  ...mcuDevice,
  fw: "CH340 serial",
  serial: "usb-CH340_5678",
  path: "/dev/serial/by-id/usb-CH340_5678",
  is_mcu: false,
};

const ignoredDevice: BusDevice = {
  ...mcuDevice,
  serial: "usb-Klipper_stm32g0b1xx_9999-if00",
  path: "/dev/serial/by-id/usb-Klipper_stm32g0b1xx_9999-if00",
  ignored: true,
};

const fullCapabilities = [
  "fw.serial.add",
  "fw.type.add",
  "fw.type.update",
  "fw.type.remove",
];

afterEach(() => {
  vi.restoreAllMocks();
  state.bus = [];
  state.status = null;
  state.ping = null;
});

describe("BusPanel", () => {
  it("opens the + menu with one item per MCU type, plus New type when canManageTypes", async () => {
    state.bus = [mcuDevice];
    state.status = { targets: [makeTarget("bttebb36"), makeTarget("other")] };
    state.ping = { capabilities: fullCapabilities };
    const wrapper = mount(BusPanel);

    expect(wrapper.find(".menu-list").exists()).toBe(false);
    await wrapper.get('[title="Track this device…"]').trigger("click");

    const items = wrapper.findAll(".menu-item").map((item) => item.text());
    expect(items).toContain("bttebb36");
    expect(items).toContain("other");
    expect(items).toContain("New type from this…");
  });

  it("adopts under the type clicked in the + menu", async () => {
    state.bus = [mcuDevice];
    state.status = { targets: [makeTarget("bttebb36")] };
    state.ping = { capabilities: fullCapabilities };
    const spy = vi.spyOn(store, "adoptSerial").mockResolvedValue(true);
    const wrapper = mount(BusPanel);

    await wrapper.get('[title="Track this device…"]').trigger("click");
    const item = wrapper
      .findAll(".menu-item")
      .find((b) => b.text() === "bttebb36");
    await item!.trigger("click");

    expect(spy).toHaveBeenCalledWith("bttebb36", mcuDevice.serial);
  });

  it("omits the + button entirely when is_mcu is false, but keeps ×", () => {
    state.bus = [nonMcuDevice];
    state.status = { targets: [makeTarget("bttebb36")] };
    state.ping = { capabilities: fullCapabilities };
    const wrapper = mount(BusPanel);

    expect(wrapper.find('[title="Track this device…"]').exists()).toBe(false);
    expect(wrapper.find('[title="Ignore"]').exists()).toBe(true);
  });

  it("ignores a device when × is clicked", async () => {
    state.bus = [mcuDevice];
    const spy = vi.spyOn(store, "ignoreSerial").mockResolvedValue(true);
    const wrapper = mount(BusPanel);

    await wrapper.get('[title="Ignore"]').trigger("click");

    expect(spy).toHaveBeenCalledWith(mcuDevice.serial);
  });

  it("moves an ignored device out of the untracked list and into the disclosure", () => {
    state.bus = [mcuDevice, ignoredDevice];
    const wrapper = mount(BusPanel);

    expect(wrapper.text()).toContain("Ignored (1)");
    const lists = wrapper.findAll("ul.devices");
    expect(lists).toHaveLength(2);
    expect(lists[0].text()).not.toContain(ignoredDevice.path);
    expect(lists[1].text()).toContain(ignoredDevice.path);
  });

  it("does not double-fire on two rapid clicks with no await between them", async () => {
    state.bus = [mcuDevice];
    const spy = vi.spyOn(store, "ignoreSerial").mockResolvedValue(true);
    const wrapper = mount(BusPanel);
    const btn = wrapper.get('[title="Ignore"]');

    // No await between these two - simulates two clicks landing before
    // Vue's next patch has a chance to reflect busy[serial] in the DOM's
    // `disabled` attribute, which is exactly the case a synchronous
    // re-entrancy guard (not just `:disabled`) has to cover.
    const first = btn.trigger("click");
    const second = btn.trigger("click");
    await Promise.all([first, second]);

    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("restores an ignored device via the disclosure's undo button", async () => {
    state.bus = [ignoredDevice];
    const spy = vi.spyOn(store, "unignoreSerial").mockResolvedValue(true);
    const wrapper = mount(BusPanel);

    await wrapper.get('[title="Restore"]').trigger("click");

    expect(spy).toHaveBeenCalledWith(ignoredDevice.serial);
  });

  it("still renders the panel when untracked is empty but ignored has entries", () => {
    state.bus = [ignoredDevice];
    const wrapper = mount(BusPanel);

    expect(wrapper.find(".panel").exists()).toBe(true);
    expect(wrapper.text()).toContain("Ignored (1)");
  });

  it("hides the panel entirely when the bus has nothing untracked or ignored", () => {
    state.bus = [];
    const wrapper = mount(BusPanel);

    expect(wrapper.find(".panel").exists()).toBe(false);
  });
});
