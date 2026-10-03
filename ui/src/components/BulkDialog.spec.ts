import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import BulkDialog from "./BulkDialog.vue";
import type { Target } from "../api/targets";

const target: Target = {
  provider: "kconfig_make",
  name: "bttebb36",
  descriptor: "stm32g0b1xx",
  firmware: "klipper",
  artifact: { state: "current", tone: "ok", label: "Up to date", reason: null },
  profile: null,
  needs_flash: false,
  actions: [
    {
      id: "flash",
      label: "Flash",
      method: "fw.flash_all",
      params: { name: "bttebb36", scope: "stale" },
      blocked: null,
    },
  ],
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

describe("BulkDialog", () => {
  it("says there is nothing to do, and offers the override as the way out", async () => {
    const wrapper = mount(BulkDialog, {
      props: { operation: "flash_all", targets: [target] },
    });

    expect(wrapper.get(".dialog h2").text()).toBe("Flash everything");
    expect(wrapper.text()).toContain(
      "Flash every device that needs it. This stops Klipper once for the whole batch.",
    );
    expect(wrapper.text()).toContain("Nothing for this to do right now.");
    const primary = () =>
      wrapper.findAll("button").find((b) => b.text() === "Flash everything");
    expect(primary()?.attributes("disabled")).toBeDefined();

    await wrapper.get('input[type="checkbox"]').setValue(true);

    expect(wrapper.text()).toContain("Ignores the recorded provenance");
    expect(wrapper.text()).toContain("Will flash:");
    const row = wrapper.get(".detail-block > div");
    expect(row.get("strong").text()).toBe("mcu EBBT0");
    expect(row.text()).toContain("bttebb36 · 230048-if00");
    expect(wrapper.get(".alert--warning").text()).toBe(
      "This stops Klipper and writes to hardware. Do not interrupt it once started.",
    );
    expect(primary()?.attributes("disabled")).toBeUndefined();
  });

  it("writes nothing to a board for a build, so it carries no warning", () => {
    const wrapper = mount(BulkDialog, {
      props: { operation: "build_all", targets: [target] },
    });

    expect(wrapper.get(".dialog h2").text()).toBe("Build everything");
    expect(wrapper.find(".alert--warning").exists()).toBe(false);
  });

  it("calls the update's flash list a floor, not a forecast", () => {
    const wrapper = mount(BulkDialog, {
      props: { operation: "update_all", targets: [target] },
    });

    expect(wrapper.text()).toContain("a floor, not a forecast");
  });
});
