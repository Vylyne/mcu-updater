import { afterEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import BusPanel from "./BusPanel.vue";
import * as store from "../store/agent";
import { state } from "../store/agent";
import type { BusDevice, Target } from "../api/targets";
import { mdiLan } from "../icons";

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

// Roadrunner is identified from the plain 8 BusDevice fields, per the by-id
// scanner's generic split of `usb-Vylyne_Roadrunner_<serial>-if00` - see
// api/targets.ts's isRoadrunnerDevice. `state` deliberately carries no
// Roadrunner-specific meaning (it falls back to fw.toLowerCase()).
const unprovisionedRoadrunner: BusDevice = {
  fw: "Vylyne",
  chipset: "Roadrunner",
  serial: "RR-UNPROVISIONED-0123456789ABCDEF",
  path: "/dev/serial/by-id/usb-Vylyne_Roadrunner_RR-UNPROVISIONED-0123456789ABCDEF-if00",
  state: "vylyne",
  tracked_by: null,
  is_mcu: true,
  ignored: false,
};

const provisionedRoadrunner: BusDevice = {
  ...unprovisionedRoadrunner,
  serial: "RR-0123456789ABCDEFGHJKMNPQRS",
  path: "/dev/serial/by-id/usb-Vylyne_Roadrunner_RR-0123456789ABCDEFGHJKMNPQRS-if00",
};

const roadrunnerCapabilities = [
  "fw.roadrunner.provision",
  "fw.roadrunner.clear",
];

const fullCapabilities = [
  "fw.serial.add",
  "fw.type.add",
  "fw.type.update",
  "fw.type.remove",
];

afterEach(() => {
  vi.restoreAllMocks();
  state.bus = [];
  state.canbus = null;
  state.canbusError = null;
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

  it("places CAN application metadata to the right of UUID, interface, and state", () => {
    state.canbus = {
      interfaces: [],
      devices: [
        {
          uuid: "abc123",
          interface: "can1",
          application: "Katapult",
          state: "katapult",
          tracked_by: null,
          ignored: false,
        },
      ],
      failures: [],
      count: 1,
      message: null,
    };
    const wrapper = mount(BusPanel);
    expect(wrapper.text()).toContain("abc123");
    const row = wrapper
      .findAll("ul.devices > li")
      .find((candidate) => candidate.text().includes("abc123"))!;
    expect(row.get(".device-identity").text()).toContain("CAN can1 · katapult");
    expect(row.get(".device-identity").text()).not.toContain("Katapult");
    expect(row.get(".device-firmware").text()).toBe("Katapult");
    expect(row.find('[title="Ignore"]').exists()).toBe(false);
  });

  it("uses a network icon for an untracked CAN device", () => {
    state.canbus = {
      interfaces: [],
      devices: [
        {
          uuid: "abc123",
          interface: "can1",
          application: "Katapult",
          state: "katapult",
          tracked_by: null,
          ignored: false,
        },
      ],
      failures: [],
      count: 1,
      message: null,
    };
    const wrapper = mount(BusPanel);
    const row = wrapper
      .findAll("ul.devices > li")
      .find((candidate) => candidate.text().includes("abc123"))!;

    expect(row.get("svg path").attributes("d")).toBe(mdiLan);
  });

  it("ignores a CAN UUID when its × button is clicked", async () => {
    state.canbus = {
      interfaces: [],
      devices: [
        {
          uuid: "abc123",
          interface: "can1",
          application: "Klipper",
          state: "klipper",
          tracked_by: null,
          ignored: false,
        },
      ],
      failures: [],
      count: 1,
      message: null,
    };
    state.ping = { capabilities: ["fw.canbus.ignore"] };
    const spy = vi.spyOn(store, "ignoreCanbus").mockResolvedValue(true);
    const wrapper = mount(BusPanel);
    const row = wrapper
      .findAll("ul.devices > li")
      .find((candidate) => candidate.text().includes("abc123"))!;

    await row.get('[title="Ignore"]').trigger("click");

    expect(spy).toHaveBeenCalledWith("abc123");
  });

  it("moves every ignored sighting of a CAN UUID into the ignored disclosure", () => {
    state.bus = [ignoredDevice];
    state.canbus = {
      interfaces: [],
      devices: [
        {
          uuid: "abc123",
          interface: "can0",
          application: "Klipper",
          state: "klipper",
          tracked_by: null,
          ignored: true,
        },
        {
          uuid: "abc123",
          interface: "can1",
          application: "Katapult",
          state: "katapult",
          tracked_by: null,
          ignored: true,
        },
      ],
      failures: [],
      count: 2,
      message: null,
    };
    const wrapper = mount(BusPanel);

    expect(wrapper.text()).toContain("Ignored (3)");
    const lists = wrapper.findAll("ul.devices");
    expect(lists[0].text()).not.toContain("abc123");
    expect(lists[1].findAll("li")).toHaveLength(3);
    expect(lists[1].text()).toContain("CAN can0 · klipper");
    expect(lists[1].text()).toContain("CAN can1 · katapult");
  });

  it("uses one UUID busy guard across duplicate CAN interface sightings", async () => {
    state.canbus = {
      interfaces: [],
      devices: [
        {
          uuid: "abc123",
          interface: "can0",
          application: "Klipper",
          state: "klipper",
          tracked_by: null,
          ignored: false,
        },
        {
          uuid: "abc123",
          interface: "can1",
          application: "Katapult",
          state: "katapult",
          tracked_by: null,
          ignored: false,
        },
      ],
      failures: [],
      count: 2,
      message: null,
    };
    state.ping = { capabilities: ["fw.canbus.ignore"] };
    const spy = vi.spyOn(store, "ignoreCanbus").mockResolvedValue(true);
    const wrapper = mount(BusPanel);
    const buttons = wrapper.findAll('[title="Ignore"]');

    const first = buttons[0].trigger("click");
    const second = buttons[1].trigger("click");
    await Promise.all([first, second]);

    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy).toHaveBeenCalledWith("abc123");
  });

  it("does not offer CAN restore without the unignore capability", () => {
    state.canbus = {
      interfaces: [],
      devices: [
        {
          uuid: "abc123",
          interface: "can1",
          application: "Katapult",
          state: "katapult",
          tracked_by: null,
          ignored: true,
        },
      ],
      failures: [],
      count: 1,
      message: null,
    };
    const wrapper = mount(BusPanel);

    expect(wrapper.text()).toContain("Ignored (1)");
    expect(wrapper.find('[title="Restore"]').exists()).toBe(false);
  });

  it("restores an ignored CAN UUID from the disclosure", async () => {
    state.canbus = {
      interfaces: [],
      devices: [
        {
          uuid: "abc123",
          interface: "can1",
          application: "Katapult",
          state: "katapult",
          tracked_by: null,
          ignored: true,
        },
      ],
      failures: [],
      count: 1,
      message: null,
    };
    state.ping = { capabilities: ["fw.canbus.unignore"] };
    const spy = vi.spyOn(store, "unignoreCanbus").mockResolvedValue(true);
    const wrapper = mount(BusPanel);

    await wrapper.get('.ignored-devices [title="Restore"]').trigger("click");

    expect(spy).toHaveBeenCalledWith("abc123");
  });

  it("styles the ignored disclosure summary as subdued caption text", () => {
    state.bus = [ignoredDevice];
    const wrapper = mount(BusPanel);

    const summary = wrapper.get(".ignored-devices summary");
    expect(summary.classes()).toContain("text-caption");
    expect(summary.classes()).toContain("text--disabled");
  });

  it("adopts a CAN device with fw.canbus.add", async () => {
    state.canbus = {
      interfaces: [],
      devices: [
        {
          uuid: "abc123",
          interface: "can1",
          application: "Klipper",
          state: "klipper",
          tracked_by: null,
          ignored: false,
        },
      ],
      failures: [],
      count: 1,
      message: null,
    };
    state.status = { targets: [makeTarget("bttebb36")] };
    state.ping = { capabilities: fullCapabilities.concat("fw.canbus.add") };
    const spy = vi.spyOn(store, "adoptCanbus").mockResolvedValue(true);
    const wrapper = mount(BusPanel);
    await wrapper.get('[title="Track this CAN device…"]').trigger("click");
    await wrapper.get(".menu-item").trigger("click");
    expect(spy).toHaveBeenCalledWith("bttebb36", "abc123");
  });

  it("offers New type from this… in the CAN + menu when canManageTypes", async () => {
    state.canbus = {
      interfaces: [],
      devices: [
        {
          uuid: "abc123",
          interface: "can1",
          application: "Klipper",
          state: "klipper",
          tracked_by: null,
          ignored: false,
        },
      ],
      failures: [],
      count: 1,
      message: null,
    };
    state.status = { targets: [makeTarget("bttebb36")] };
    state.ping = { capabilities: fullCapabilities.concat("fw.canbus.add") };
    const wrapper = mount(BusPanel);
    await wrapper.get('[title="Track this CAN device…"]').trigger("click");
    const items = wrapper.findAll(".menu-item").map((item) => item.text());
    expect(items).toContain("New type from this…");
  });

  it("still shows the CAN + button for New type when no type exists to adopt into", () => {
    state.canbus = {
      interfaces: [],
      devices: [
        {
          uuid: "abc123",
          interface: "can1",
          application: "Klipper",
          state: "klipper",
          tracked_by: null,
          ignored: false,
        },
      ],
      failures: [],
      count: 1,
      message: null,
    };
    // No targets at all - showCanAdoptItems is false, but canManageTypes
    // alone used to hide the whole `+` button, gating "New type from this…"
    // behind a type that couldn't exist yet.
    state.status = { targets: [] };
    state.ping = { capabilities: fullCapabilities.concat("fw.canbus.add") };
    const wrapper = mount(BusPanel);
    expect(wrapper.find('[title="Track this CAN device…"]').exists()).toBe(
      true,
    );
  });

  it("adopts a newly created type's CAN uuid via fw.canbus.add", async () => {
    state.canbus = {
      interfaces: [],
      devices: [
        {
          uuid: "abc123",
          interface: "can1",
          application: "Klipper",
          state: "klipper",
          tracked_by: null,
          ignored: false,
        },
      ],
      failures: [],
      count: 1,
      message: null,
    };
    state.status = { targets: [] };
    state.ping = { capabilities: fullCapabilities.concat("fw.canbus.add") };
    const addSpy = vi.spyOn(store, "addType").mockResolvedValue({
      ok: true,
      warnings: [],
    });
    const wrapper = mount(BusPanel);
    await wrapper.get('[title="Track this CAN device…"]').trigger("click");
    await wrapper.get(".menu-item").trigger("click");

    expect(wrapper.text()).toContain("Create a new type");
    expect(wrapper.text()).toContain("abc123");

    await wrapper.get("input[maxlength]").setValue("newtype");
    await wrapper.findAll("label input").at(1)!.setValue("stm32g0b1xx");
    await wrapper.get(".btn-primary").trigger("click");

    expect(addSpy).toHaveBeenCalledWith(
      expect.objectContaining({ name: "newtype", canbusUuid: "abc123" }),
    );
  });

  it("keeps duplicate UUID sightings separate by interface", async () => {
    state.canbus = {
      interfaces: [],
      devices: [
        {
          uuid: "abc123",
          interface: "can0",
          application: "Klipper",
          state: "klipper",
          tracked_by: null,
          ignored: false,
        },
        {
          uuid: "abc123",
          interface: "can1",
          application: "Katapult",
          state: "katapult",
          tracked_by: null,
          ignored: false,
        },
      ],
      failures: [],
      count: 2,
      message: null,
    };
    state.status = { targets: [makeTarget("bttebb36")] };
    state.ping = { capabilities: ["fw.canbus.add"] };
    const wrapper = mount(BusPanel);
    expect(wrapper.findAll("ul.devices > li")).toHaveLength(2);
    const buttons = wrapper.findAll('[title="Track this CAN device…"]');
    await buttons[1].trigger("click");
    expect(wrapper.findAll(".menu-list")).toHaveLength(1);
    expect(wrapper.findAll(".menu-list")[0].text()).toContain("bttebb36");
  });

  it("shows a non-actionable CAN scan warning", () => {
    state.canbusError = { code: "timeout", message: "CAN failed" };
    state.ping = { capabilities: ["fw.canbus.add"] };
    const wrapper = mount(BusPanel);
    expect(wrapper.text()).toContain("CAN scan failed: CAN failed");
    expect(wrapper.find('[title="Track this CAN device…"]').exists()).toBe(
      false,
    );
  });

  describe("Roadrunner actions", () => {
    it("shows Provision Roadrunner for an untracked unprovisioned board, and nothing for Clear identity", () => {
      state.bus = [unprovisionedRoadrunner];
      state.ping = { capabilities: roadrunnerCapabilities };
      const wrapper = mount(BusPanel);

      expect(wrapper.text()).toContain("Provision Roadrunner");
      expect(wrapper.text()).not.toContain("Clear identity");
    });

    it("trims the flash-UID suffix from an unprovisioned board's displayed name, keeping the full string in the path below and the dialog", () => {
      // The 16 trailing hex characters are still shown in full via the by-id
      // path underneath, and named explicitly as the diagnostic UID in the
      // provision confirmation dialog - trimming the row's own name label
      // loses nothing, it just stops repeating the same long string twice
      // right next to the Provision pill.
      state.bus = [unprovisionedRoadrunner];
      state.ping = { capabilities: roadrunnerCapabilities };
      const wrapper = mount(BusPanel);

      const nameLabel = wrapper.get(".device-name-row .text--secondary");
      expect(nameLabel.text()).toBe("RR-UNPROVISIONED");
      expect(wrapper.text()).toContain(unprovisionedRoadrunner.path);
    });

    it("offers Clear identity behind its overflow menu for an untracked provisioned board, not as a standing pill", async () => {
      state.bus = [provisionedRoadrunner];
      state.ping = { capabilities: roadrunnerCapabilities };
      const wrapper = mount(BusPanel);

      expect(wrapper.text()).not.toContain("Provision Roadrunner");
      // Not visible until the overflow menu is opened - a destructive action
      // does not get a permanently-visible pill next to the device name.
      expect(wrapper.text()).not.toContain("Clear identity");
      expect(wrapper.find('[aria-label="Roadrunner actions"]').exists()).toBe(
        true,
      );

      await wrapper.get('[aria-label="Roadrunner actions"]').trigger("click");

      expect(wrapper.text()).toContain("Clear identity");
    });

    it("hides both actions without the matching capability", () => {
      state.bus = [unprovisionedRoadrunner, provisionedRoadrunner];
      state.ping = { capabilities: [] };
      const wrapper = mount(BusPanel);

      expect(wrapper.text()).not.toContain("Provision Roadrunner");
      expect(wrapper.text()).not.toContain("Clear identity");
    });

    it("disables the generic 'track this device' affordance for an unprovisioned Roadrunner, but offers it enabled for a provisioned-untracked one", () => {
      // An unprovisioned Roadrunner's serial is RR-UNPROVISIONED-<flash-uid>;
      // adopting it through the generic flow would call fw.serial.add with
      // that string and persist the RP2040 flash UID into printer.cfg, which
      // this plan's constraints forbid. A provisioned board carries no such
      // diagnostic identity, so the generic flow remains open for it - see
      // docs/roadrunner-provisioning-design.md's "provisioned boards remain
      // untracked until separately configured". The button itself stays
      // present-but-disabled rather than omitted, so the row's icon column
      // still lines up with every other row's.
      state.bus = [unprovisionedRoadrunner, provisionedRoadrunner];
      state.status = { targets: [makeTarget("bttebb36")] };
      state.ping = {
        capabilities: [...roadrunnerCapabilities, ...fullCapabilities],
      };
      const wrapper = mount(BusPanel);

      const rows = wrapper.findAll("li");
      const unprovisionedRow = rows.find((row) =>
        row.text().includes(unprovisionedRoadrunner.serial),
      );
      const provisionedRow = rows.find((row) =>
        row.text().includes(provisionedRoadrunner.serial),
      );

      expect(
        unprovisionedRow!.find('[title="Track this device…"]').exists(),
      ).toBe(false);
      const disabledTrack = unprovisionedRow!.find(
        '[title="Provision this Roadrunner before it can be tracked"]',
      );
      expect(disabledTrack.exists()).toBe(true);
      expect(disabledTrack.attributes("disabled")).toBeDefined();

      const enabledTrack = provisionedRow!.find('[title="Track this device…"]');
      expect(enabledTrack.exists()).toBe(true);
      expect(enabledTrack.attributes("disabled")).toBeUndefined();
    });

    it("offers neither action for a Vylyne/Roadrunner serial matching neither known shape", () => {
      state.bus = [{ ...unprovisionedRoadrunner, serial: "RR-GARBAGE" }];
      state.ping = { capabilities: roadrunnerCapabilities };
      const wrapper = mount(BusPanel);

      expect(wrapper.text()).not.toContain("Provision Roadrunner");
      expect(wrapper.text()).not.toContain("Clear identity");
    });

    it("does not offer Roadrunner actions from the ignored disclosure", () => {
      state.bus = [{ ...unprovisionedRoadrunner, ignored: true }];
      state.ping = { capabilities: roadrunnerCapabilities };
      const wrapper = mount(BusPanel);

      expect(wrapper.text()).not.toContain("Provision Roadrunner");
    });

    it("Provision Roadrunner opens a confirmation naming the serial and diagnostic UID, without calling the API until confirmed", async () => {
      state.bus = [unprovisionedRoadrunner];
      state.ping = { capabilities: roadrunnerCapabilities };
      const spy = vi
        .spyOn(store, "provisionRoadrunner")
        .mockResolvedValue(true);
      const wrapper = mount(BusPanel);

      expect(wrapper.find(".dialog-backdrop").exists()).toBe(false);
      await wrapper.get("button.roadrunner-provision").trigger("click");

      expect(spy).not.toHaveBeenCalled();
      const dialog = wrapper.get(".dialog-backdrop");
      expect(dialog.text()).toContain(unprovisionedRoadrunner.serial);
      // The diagnostic UID (the 16 trailing hex chars) has to be named on
      // its own, not merely present as a substring of the full serial the
      // row already always shows - so this counts both occurrences: once
      // inside the serial line, once as its own labelled mention.
      const uid = "0123456789ABCDEF";
      const occurrences = dialog.text().split(uid).length - 1;
      expect(occurrences).toBeGreaterThanOrEqual(2);
      expect(dialog.text()).toContain("diagnostic UID");
    });

    it("confirming Provision invokes fw.roadrunner.provision once and refreshes afterward", async () => {
      state.bus = [unprovisionedRoadrunner];
      state.ping = { capabilities: roadrunnerCapabilities };
      const spy = vi
        .spyOn(store, "provisionRoadrunner")
        .mockResolvedValue(true);
      const wrapper = mount(BusPanel);

      await wrapper.get("button.roadrunner-provision").trigger("click");
      await wrapper.get(".dialog-actions button.btn-primary").trigger("click");

      expect(spy).toHaveBeenCalledTimes(1);
      expect(spy).toHaveBeenCalledWith(unprovisionedRoadrunner.serial);
      // provisionRoadrunner itself is responsible for the refreshStatus()
      // call (mirroring adoptSerial/ignoreSerial) - store/agent.spec.ts
      // asserts that refresh happens; this only asserts the panel called it.
    });

    it("Clear identity, from the overflow menu, opens its own confirmation naming only the serial and closes the menu", async () => {
      state.bus = [provisionedRoadrunner];
      state.ping = { capabilities: roadrunnerCapabilities };
      const spy = vi.spyOn(store, "clearRoadrunner").mockResolvedValue(true);
      const wrapper = mount(BusPanel);

      expect(wrapper.find(".dialog-backdrop").exists()).toBe(false);
      await wrapper.get('[aria-label="Roadrunner actions"]').trigger("click");
      await wrapper.get(".menu-item").trigger("click");

      expect(spy).not.toHaveBeenCalled();
      expect(wrapper.find(".menu-list").exists()).toBe(false);
      const dialog = wrapper.get(".dialog-backdrop");
      expect(dialog.text()).toContain(provisionedRoadrunner.serial);
      // A provisioned board has no diagnostic UID (it was single-use, tied
      // to the unprovisioned identity) - the clear dialog must not invent
      // one.
      expect(dialog.text()).not.toContain("diagnostic UID");
    });

    it("confirming Clear invokes fw.roadrunner.clear once with the serial", async () => {
      state.bus = [provisionedRoadrunner];
      state.ping = { capabilities: roadrunnerCapabilities };
      const spy = vi.spyOn(store, "clearRoadrunner").mockResolvedValue(true);
      const wrapper = mount(BusPanel);

      await wrapper.get('[aria-label="Roadrunner actions"]').trigger("click");
      await wrapper.get(".menu-item").trigger("click");
      await wrapper.get(".dialog-actions button.btn-danger").trigger("click");

      expect(spy).toHaveBeenCalledTimes(1);
      expect(spy).toHaveBeenCalledWith(provisionedRoadrunner.serial);
    });

    it("cancelling the confirmation dialog never calls the API", async () => {
      state.bus = [unprovisionedRoadrunner];
      state.ping = { capabilities: roadrunnerCapabilities };
      const spy = vi
        .spyOn(store, "provisionRoadrunner")
        .mockResolvedValue(true);
      const wrapper = mount(BusPanel);

      await wrapper.get("button.roadrunner-provision").trigger("click");
      await wrapper
        .get(".dialog-actions button:not(.btn-primary)")
        .trigger("click");

      expect(spy).not.toHaveBeenCalled();
      expect(wrapper.find(".dialog-backdrop").exists()).toBe(false);
    });

    it("does not double-fire Provision on two rapid confirm clicks", async () => {
      state.bus = [unprovisionedRoadrunner];
      state.ping = { capabilities: roadrunnerCapabilities };
      const spy = vi
        .spyOn(store, "provisionRoadrunner")
        .mockResolvedValue(true);
      const wrapper = mount(BusPanel);

      await wrapper.get("button.roadrunner-provision").trigger("click");
      const confirmBtn = wrapper.get(".dialog-actions button.btn-primary");
      const first = confirmBtn.trigger("click");
      const second = confirmBtn.trigger("click");
      await Promise.all([first, second]);

      expect(spy).toHaveBeenCalledTimes(1);
    });
  });
});
