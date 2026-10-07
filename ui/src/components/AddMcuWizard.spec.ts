import { afterEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import AddMcuWizard from "./AddMcuWizard.vue";
import * as store from "../store/agent";
import { state } from "../store/agent";
import type { Target } from "../api/targets";

function mcuTarget(name: string, descriptor: string): Target {
  return {
    provider: "kconfig_make",
    name,
    descriptor,
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
    source: null,
    extras: [],
    devices_note: "No serial devices are tracked for this type yet.",
  };
}

function row(
  provider: Target["provider"],
  name: string,
  firstInstall: Target["first_install"],
): Target {
  return {
    ...mcuTarget(name, "rp2040"),
    provider,
    first_install: firstInstall,
  };
}

function newAgent(): void {
  state.ping = {
    capabilities: ["fw.add_mcu.scan", "fw.add_mcu.start"],
  } as never;
}

afterEach(() => {
  state.status = null;
  state.ping = null;
  vi.restoreAllMocks();
});

describe("AddMcuWizard", () => {
  it.each([
    ["barepico", "rp2040"],
    ["bttebb36", "stm32g0b1xx"],
  ])(
    "labels the install button for whatever the type's first image is (%s)",
    async (name, chipset) => {
      // A Target carries no bootloader flag, so the button cannot say which
      // image - a type with no Katapult gets its Klipper build, not Katapult.
      state.status = { targets: [mcuTarget(name, chipset)] } as never;
      vi.spyOn(store, "scanBareBoard").mockResolvedValue({ ready: true });
      const wrapper = mount(AddMcuWizard, { props: { open: true } });

      await wrapper.get("select").setValue(name);
      const scan = wrapper
        .findAll("button")
        .find((button) => button.text() === "Scan");
      expect(scan).toBeDefined();
      await scan!.trigger("click");
      await flushPromises();

      const labels = wrapper.findAll("button").map((button) => button.text());
      expect(labels).toContain("Install firmware");
      expect(labels).not.toContain("Install Katapult");
    },
  );

  it("starts the add for the chosen type", async () => {
    state.status = { targets: [mcuTarget("barepico", "rp2040")] } as never;
    vi.spyOn(store, "scanBareBoard").mockResolvedValue({ ready: true });
    const start = vi.spyOn(store, "startAddMcu").mockResolvedValue(true);
    const wrapper = mount(AddMcuWizard, { props: { open: true } });

    await wrapper.get("select").setValue("barepico");
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "Scan")!
      .trigger("click");
    await flushPromises();
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "Install firmware")!
      .trigger("click");
    await flushPromises();

    expect(start).toHaveBeenCalledWith("barepico", undefined, undefined);
  });

  it("lists a cmake type and scans it through fw.add_mcu.scan", async () => {
    newAgent();
    state.status = {
      targets: [
        row("cmake", "roadrunner", {
          fw: "roadrunner",
          flasher: "bootsel",
          reason: null,
        }),
      ],
    } as never;
    const scanNew = vi
      .spyOn(store, "scanNewBoard")
      .mockResolvedValue({ ready: true, flasher: "bootsel" });
    const legacy = vi.spyOn(store, "scanBareBoard");
    const start = vi.spyOn(store, "startAddMcu").mockResolvedValue(true);
    const wrapper = mount(AddMcuWizard, { props: { open: true } });

    await wrapper.get("select").setValue("roadrunner");
    await wrapper
      .findAll("button")
      .find((b) => b.text() === "Scan")!
      .trigger("click");
    await flushPromises();
    await wrapper
      .findAll("button")
      .find((b) => b.text() === "Install roadrunner")!
      .trigger("click");
    await flushPromises();

    expect(scanNew).toHaveBeenCalledWith("roadrunner");
    expect(legacy).not.toHaveBeenCalled();
    expect(start).toHaveBeenCalledWith("roadrunner", undefined, undefined);
  });

  it("warns on a ready scan that found more boards than it can write", async () => {
    newAgent();
    state.status = {
      targets: [
        row("cmake", "roadrunner", {
          fw: "roadrunner",
          flasher: "bootsel",
          reason: null,
        }),
      ],
    } as never;
    vi.spyOn(store, "scanNewBoard").mockResolvedValue({
      ready: true,
      count: 2,
      flasher: "bootsel",
      message: "2 RP2040s are in BOOTSEL but only one volume is mounted",
    });
    const wrapper = mount(AddMcuWizard, { props: { open: true } });

    await wrapper.get("select").setValue("roadrunner");
    await wrapper
      .findAll("button")
      .find((b) => b.text() === "Scan")!
      .trigger("click");
    await flushPromises();

    expect(wrapper.text()).not.toContain("one board found");
    expect(wrapper.get(".alert--warning").text()).toContain(
      "2 RP2040s are in BOOTSEL",
    );
    const install = wrapper
      .findAll("button")
      .find((b) => b.text() === "Install roadrunner");
    expect(install?.attributes("disabled")).toBeUndefined();
  });

  it("shows why a type with no flasher cannot be set up", async () => {
    newAgent();
    const reason =
      "nothing on [firmware knomi_serial]'s flashers: (platformio) can scan for a new board";
    state.status = {
      targets: [
        row("platformio", "knomi", {
          fw: "knomi_serial",
          flasher: null,
          reason,
        }),
      ],
    } as never;
    const wrapper = mount(AddMcuWizard, { props: { open: true } });

    await wrapper.get("select").setValue("knomi");

    expect(wrapper.text()).toContain(reason);
    expect(wrapper.findAll("button").map((b) => b.text())).not.toContain(
      "Scan",
    );
  });

  it("offers the serial pick only for an ambiguous dfu_util scan", async () => {
    newAgent();
    state.status = {
      targets: [
        row("kconfig_make", "ebb", {
          fw: "katapult",
          flasher: "dfu_util",
          reason: null,
        }),
      ],
    } as never;
    vi.spyOn(store, "scanNewBoard").mockResolvedValue({
      ready: false,
      reason: "ambiguous",
      flasher: "dfu_util",
      devices: [
        { serial: "A", path: "1-1" },
        { serial: "B", path: "1-2" },
      ],
    });
    const wrapper = mount(AddMcuWizard, { props: { open: true } });

    await wrapper.get("select").setValue("ebb");
    await wrapper
      .findAll("button")
      .find((b) => b.text() === "Scan")!
      .trigger("click");
    await flushPromises();

    expect(wrapper.findAll("select")).toHaveLength(2);
  });

  const knomi = row("platformio", "knomi", {
    fw: "knomi_serial",
    flasher: "platformio",
    reason: null,
    hint: "Plug the device in. Nothing has to be held or jumpered.",
  });
  const port = (n: number, socket: string, owner?: string) => ({
    tty: `/dev/ttyUSB${n}`,
    port: socket,
    interface: `${socket}:1.0`,
    tracked_by: owner ? "knomi" : null,
    label: owner ?? null,
  });

  async function scanned(result: Record<string, unknown>) {
    newAgent();
    state.status = { targets: [knomi] } as never;
    vi.spyOn(store, "scanNewBoard").mockResolvedValue(result);
    const wrapper = mount(AddMcuWizard, { props: { open: true } });
    await wrapper.get("select").setValue("knomi");
    const before = wrapper.text();
    await wrapper
      .findAll("button")
      .find((b) => b.text() === "Scan")!
      .trigger("click");
    await flushPromises();
    return { wrapper, before };
  }

  it("says how to get the board ready in the flasher's own words", async () => {
    const { before } = await scanned({ ready: true, pick: "interface" });

    expect(before).toContain("Nothing has to be held or jumpered. Then scan.");
    expect(before).not.toContain("boot jumper, or hold BOOT");
  });

  it("offers a pick by the key the scan declares, and sends it", async () => {
    const start = vi.spyOn(store, "startAddMcu").mockResolvedValue(true);
    const { wrapper } = await scanned({
      ready: false,
      reason: "ambiguous",
      message: "2 unconfigured ports match.",
      pick: "interface",
      devices: [
        port(0, "3-1.6.5", "t0_knomi"),
        port(2, "3-1.6.7"),
        port(3, "3-1.6.8"),
      ],
    });

    const pick = wrapper.get("select.pick");
    const options = pick.findAll("option").map((o) => o.text());
    expect(options).toEqual([
      "Choose a device…",
      "/dev/ttyUSB0 · USB 3-1.6.5 - already configured (t0_knomi, knomi)",
      "/dev/ttyUSB2 · USB 3-1.6.7",
      "/dev/ttyUSB3 · USB 3-1.6.8",
    ]);
    const install = () =>
      wrapper
        .findAll("button")
        .find((b) => b.text() === "Install knomi_serial")!;
    // Nothing is preselected: a configured port must never be the default.
    expect(install().attributes("disabled")).toBeDefined();

    await pick.setValue("3-1.6.8:1.0");
    expect(install().attributes("disabled")).toBeUndefined();
    await install().trigger("click");
    await flushPromises();

    expect(start).toHaveBeenCalledWith("knomi", undefined, "3-1.6.8:1.0");
  });

  it.each(["all_tracked", "unfiltered"])(
    "offers the pick for a %s scan too",
    async (reason) => {
      const { wrapper } = await scanned({
        ready: false,
        reason,
        pick: "interface",
        devices: [port(0, "3-1.6.5", "t0_knomi")],
      });

      expect(wrapper.find("select.pick").exists()).toBe(true);
    },
  );

  it("offers no pick when the scan settled on one device", async () => {
    const start = vi.spyOn(store, "startAddMcu").mockResolvedValue(true);
    const { wrapper } = await scanned({
      ready: true,
      pick: "interface",
      devices: [port(0, "3-1.6.5", "t0_knomi"), port(2, "3-1.6.7")],
    });

    expect(wrapper.find("select.pick").exists()).toBe(false);
    await wrapper
      .findAll("button")
      .find((b) => b.text() === "Install knomi_serial")!
      .trigger("click");
    await flushPromises();
    expect(start).toHaveBeenCalledWith("knomi", undefined, undefined);
  });

  it("offers no pick when there is nothing to pick from", async () => {
    const { wrapper } = await scanned({
      ready: false,
      reason: "none",
      message:
        "No serial port with a USB id knomi's board declares is attached.",
      pick: "interface",
      devices: [],
    });

    expect(wrapper.find("select.pick").exists()).toBe(false);
    expect(wrapper.text()).toContain("No serial port with a USB id");
  });

  it("falls back to the kconfig-only flow against an older agent", async () => {
    // No fw.add_mcu.scan capability, no first_install on the rows: the
    // release order guarantees this pairing for a window.
    state.status = {
      targets: [
        mcuTarget("barepico", "rp2040"),
        {
          ...mcuTarget("roadrunner", "roadrunner_v1_i2c_rgb"),
          provider: "cmake",
        },
      ],
    } as never;
    const legacy = vi
      .spyOn(store, "scanBareBoard")
      .mockResolvedValue({ ready: true });
    const wrapper = mount(AddMcuWizard, { props: { open: true } });

    const options = wrapper.findAll("option").map((o) => o.text());
    expect(options.some((o) => o.startsWith("roadrunner"))).toBe(false);

    await wrapper.get("select").setValue("barepico");
    await wrapper
      .findAll("button")
      .find((b) => b.text() === "Scan")!
      .trigger("click");
    await flushPromises();

    expect(legacy).toHaveBeenCalledWith("bootsel");
    expect(wrapper.findAll("button").map((b) => b.text())).toContain(
      "Install firmware",
    );
  });
});
