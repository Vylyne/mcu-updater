import { afterEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import KconfigDialog from "./KconfigDialog.vue";
import { state } from "../store/agent";
import * as store from "../store/agent";
import type { KconfigState } from "../api/kconfig";

const menu: KconfigState = {
  session: "sess-1",
  revision: 0,
  type: "carto_v4",
  fw: "klipper",
  dirty: false,
  breadcrumb: [{ id: "root", prompt: "Configuration" }],
  nodes: [
    {
      id: "menu:board",
      kind: "menu",
      name: null,
      prompt: "Board",
      depth: 0,
      value: null,
      visible: true,
      assignable: [],
      options: null,
      value_label: null,
      editable: false,
      range: null,
      has_help: false,
      is_menuconfig: false,
      enterable: false,
    },
  ],
  search: null,
  help: null,
};

afterEach(() => {
  vi.restoreAllMocks();
  state.kconfig = null;
});

describe("KconfigDialog", () => {
  it("renders nothing without an open session", () => {
    const wrapper = mount(KconfigDialog);
    expect(wrapper.find(".dialog-backdrop").exists()).toBe(false);
  });

  it("renders the breadcrumb and current menu's nodes", () => {
    state.kconfig = menu;
    const wrapper = mount(KconfigDialog);
    expect(wrapper.text()).toContain("carto_v4 / klipper");
    expect(wrapper.text()).toContain("Configuration");
    expect(wrapper.text()).toContain("Board");
  });

  it("enters a menu row through the store", async () => {
    state.kconfig = menu;
    const spy = vi.spyOn(store, "kconfigEnter").mockResolvedValue(true);
    const wrapper = mount(KconfigDialog);
    await wrapper.get("button.kconfig-enter").trigger("click");
    expect(spy).toHaveBeenCalledWith("menu:board");
  });

  it("disables Save and Discard while there are no unsaved changes", () => {
    state.kconfig = menu;
    const wrapper = mount(KconfigDialog);
    const save = wrapper
      .findAll(".dialog-actions button")
      .find((b) => b.text() === "Save");
    const discard = wrapper
      .findAll(".dialog-actions button")
      .find((b) => b.text() === "Discard");
    expect(save?.attributes("disabled")).toBeDefined();
    expect(discard?.attributes("disabled")).toBeDefined();
  });

  it("closes immediately when there is nothing unsaved", async () => {
    state.kconfig = menu;
    const spy = vi.spyOn(store, "closeKconfig");
    const wrapper = mount(KconfigDialog);
    await wrapper.get('[aria-label="Close"]').trigger("click");
    expect(spy).toHaveBeenCalled();
  });

  it("asks for confirmation before closing with unsaved changes", async () => {
    state.kconfig = { ...menu, dirty: true };
    const spy = vi.spyOn(store, "closeKconfig");
    const wrapper = mount(KconfigDialog);
    await wrapper.get('[aria-label="Close"]').trigger("click");
    expect(spy).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain("Discard unsaved changes");

    // Two "Discard" buttons exist: the confirm-overlay's (discards the
    // *close*, i.e. closeKconfig) and the main footer's (discards the
    // *edits*, i.e. kconfigReset - still open afterwards). The confirm
    // overlay is nested inside the outer dialog's body, so it renders
    // before the outer footer in document order - the first match is the
    // one this test means to click.
    const discardButtons = wrapper
      .findAll("button")
      .filter((b) => b.text() === "Discard");
    await discardButtons[0]?.trigger("click");
    expect(spy).toHaveBeenCalled();
  });

  it("shows the help overlay from state.kconfig.help", () => {
    state.kconfig = {
      ...menu,
      help: {
        id: "CONFIG_FOO",
        prompt: "Enable foo",
        help: "Does foo things.",
      },
    };
    const wrapper = mount(KconfigDialog);
    expect(wrapper.text()).toContain("Does foo things.");
  });
});
