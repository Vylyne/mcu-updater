import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import App from "./App.vue";

describe("App", () => {
  it("hides the debug harness by default, at a full page as much as embedded", () => {
    const wrapper = mount(App);
    expect(wrapper.text()).not.toContain("mcu-updater");
    expect(wrapper.text()).not.toContain("Connection");
    expect(wrapper.text()).not.toContain("fw.ping");
    expect(wrapper.text()).not.toContain("fw.status (raw)");
    expect(wrapper.text()).not.toContain("Events");
    expect(wrapper.text()).toContain("Firmware");
    // Not embedded: none of the iframe layout classes are stamped.
    expect(wrapper.get("main").classes()).not.toContain("embed");
    expect(
      document.documentElement.classList.contains("mcu-updater-embed"),
    ).toBe(false);
    // Unmount synchronously, before any reconnect timer this environment's
    // missing WebSocket/fetch implementations scheduled gets a chance to fire.
    wrapper.unmount();
  });

  it("shows the debug harness under ?debug=1, without claiming to be embedded", () => {
    history.pushState(null, "", "/?debug=1");
    const wrapper = mount(App);
    expect(wrapper.text()).toContain("mcu-updater");
    expect(wrapper.text()).toContain("Connection");
    expect(wrapper.text()).toContain("fw.ping");
    expect(wrapper.text()).toContain("fw.status (raw)");
    expect(wrapper.text()).toContain("Events");
    expect(wrapper.get("main").classes()).not.toContain("embed");
    expect(
      document.documentElement.classList.contains("mcu-updater-embed"),
    ).toBe(false);
    wrapper.unmount();
    history.pushState(null, "", "/");
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

  it("embed and debug are independent flags", () => {
    history.pushState(null, "", "/?embed=1&debug=1");
    const wrapper = mount(App);
    // The layout box still applies...
    expect(wrapper.get("main").classes()).toContain("embed");
    expect(
      document.documentElement.classList.contains("mcu-updater-embed"),
    ).toBe(true);
    // ...and the debug panels still show, proving neither flag implies or
    // excludes the other.
    expect(wrapper.text()).toContain("mcu-updater");
    expect(wrapper.text()).toContain("Connection");
    wrapper.unmount();
    document.documentElement.classList.remove("mcu-updater-embed");
    document.body.classList.remove("mcu-updater-embed");
    history.pushState(null, "", "/");
  });
});
