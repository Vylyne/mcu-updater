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
});
