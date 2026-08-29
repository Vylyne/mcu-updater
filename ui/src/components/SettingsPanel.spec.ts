import { afterEach, describe, expect, it, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import SettingsPanel from "./SettingsPanel.vue";
import { state } from "../store/agent";
import * as store from "../store/agent";
import type { UpdaterSettings } from "../api/settings";

const settings: UpdaterSettings = {
  make_jobs: 0,
  clean_before_build: true,
  reseed_on_build: true,
  stop_services: null,
  service_backend: "moonraker",
  dry_run: false,
  enable_flashing: false,
  allow_flash_while_printing: false,
  log_ring_size: 2000,
  platformio_bin: "",
  flashtool_path: "",
  ui_accent_color: "",
};

afterEach(() => {
  vi.restoreAllMocks();
  state.status = null;
});

describe("SettingsPanel accent colour", () => {
  it("shows the theme default swatch when no colour is saved", () => {
    state.status = { settings: { ...settings } };
    const wrapper = mount(SettingsPanel);
    const input = wrapper.get('input[type="color"]')
      .element as HTMLInputElement;
    expect(input.value).toBe("#2196f3");
    expect(
      wrapper
        .findAll("button")
        .find((b) => b.text() === "Reset to default")
        ?.attributes("disabled"),
    ).toBeDefined();
  });

  it("prefills the swatch from a saved colour and enables Reset", () => {
    state.status = { settings: { ...settings, ui_accent_color: "#ff9800" } };
    const wrapper = mount(SettingsPanel);
    const input = wrapper.get('input[type="color"]')
      .element as HTMLInputElement;
    expect(input.value).toBe("#ff9800");
    expect(
      wrapper
        .findAll("button")
        .find((b) => b.text() === "Reset to default")
        ?.attributes("disabled"),
    ).toBeUndefined();
  });

  it("sends the picked colour in the save patch", async () => {
    state.status = { settings: { ...settings } };
    const spy = vi
      .spyOn(store, "updateSettings")
      .mockResolvedValue({ ok: true, changed: ["ui_accent_color"] });
    const wrapper = mount(SettingsPanel);
    await wrapper.get('input[type="color"]').setValue("#ff9800");
    await wrapper
      .findAll("button")
      .find((b) => b.text() === "Save")
      ?.trigger("click");
    await flushPromises();
    expect(spy).toHaveBeenCalledWith({ ui_accent_color: "#ff9800" });
  });

  it("resets a saved colour back to empty (the theme default) on Reset", async () => {
    state.status = { settings: { ...settings, ui_accent_color: "#ff9800" } };
    const wrapper = mount(SettingsPanel);
    await wrapper
      .findAll("button")
      .find((b) => b.text() === "Reset to default")
      ?.trigger("click");
    const input = wrapper.get('input[type="color"]')
      .element as HTMLInputElement;
    // The swatch still shows *a* colour - <input type="color"> cannot render
    // "unset" - but the pending edit itself is the empty string that means
    // "use the theme default".
    expect(input.value).toBe("#2196f3");
    expect(
      wrapper
        .findAll("button")
        .find((b) => b.text() === "Save")
        ?.attributes("disabled"),
    ).toBeUndefined();
  });
});
