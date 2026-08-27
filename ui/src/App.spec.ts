import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import App from "./App.vue";

describe("App", () => {
  it("mounts", () => {
    const wrapper = mount(App);
    expect(wrapper.text()).toContain("mcu-updater");
    // Unmount synchronously, before any reconnect timer this environment's
    // missing WebSocket/fetch implementations scheduled gets a chance to fire.
    wrapper.unmount();
  });

  it("drops the app chrome under ?embed=1 but keeps the functional surface", () => {
    history.pushState(null, "", "/?embed=1");
    const wrapper = mount(App);
    expect(wrapper.text()).not.toContain("mcu-updater");
    expect(wrapper.text()).not.toContain("Connection");
    expect(wrapper.text()).not.toContain("fw.ping");
    expect(wrapper.text()).not.toContain("fw.status (raw)");
    expect(wrapper.text()).not.toContain("Events");
    expect(wrapper.text()).toContain("Firmware");
    expect(wrapper.get("main").classes()).toContain("embed");
    expect(
      document.documentElement.classList.contains("mcu-updater-embed"),
    ).toBe(true);
    wrapper.unmount();
    document.documentElement.classList.remove("mcu-updater-embed");
    document.body.classList.remove("mcu-updater-embed");
    history.pushState(null, "", "/");
  });
});
