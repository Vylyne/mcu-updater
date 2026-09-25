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
  };
}

afterEach(() => {
  state.status = null;
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

    expect(start).toHaveBeenCalledWith("barepico", undefined);
  });
});
