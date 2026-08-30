import { describe, expect, it, vi, afterEach } from "vitest";
import { mount } from "@vue/test-utils";
import ActionButton from "./ActionButton.vue";
import type { Action } from "../api/targets";
import * as store from "../store/agent";

afterEach(() => {
  vi.restoreAllMocks();
});

const buildAction: Action = {
  id: "build",
  label: "Build",
  method: "fw.build",
  params: { name: "carto_v4", fw: "cartographer" },
  blocked: null,
};

describe("ActionButton", () => {
  it("disables the button and shows the message when blocked", () => {
    const action: Action = {
      ...buildAction,
      id: "flash",
      label: "Flash",
      method: "fw.flash_all",
      blocked: { code: "no_artifact", message: "Build it first." },
    };
    const wrapper = mount(ActionButton, { props: { action } });
    const button = wrapper.get("button");
    expect(button.attributes("disabled")).toBeDefined();
    // Icon variant (the default, matching a row's own icon-only actions)
    // carries the blocked message as the button's title rather than visible
    // text - see ActionButton.vue's `title="blockedMessage ?? action.label"`.
    expect(button.attributes("title")).toContain("Build it first.");
  });

  it("disables the button on transient busy state without touching blocked", () => {
    const wrapper = mount(ActionButton, {
      props: {
        action: buildAction,
        disabled: true,
        disabledReason: "build is already running",
      },
    });
    const button = wrapper.get("button");
    expect(button.attributes("disabled")).toBeDefined();
    expect(button.attributes("title")).toContain("build is already running");
  });

  it("invokes a non-destructive action directly, with no confirmation", async () => {
    const spy = vi.spyOn(store, "invokeAction").mockResolvedValue(true);
    const wrapper = mount(ActionButton, { props: { action: buildAction } });
    await wrapper.get("button").trigger("click");
    // run() always passes an extra-params object (used for the reseed
    // prompt's { reseed } on other actions) - {} here, not omitted.
    expect(spy).toHaveBeenCalledWith(buildAction, {});
    expect(wrapper.text()).not.toContain("Confirm");
  });

  it("requires confirmation before flashing, and names the real devices", async () => {
    const spy = vi.spyOn(store, "invokeAction").mockResolvedValue(true);
    const action: Action = {
      ...buildAction,
      id: "flash",
      label: "Flash",
      method: "fw.flash_all",
    };
    const wrapper = mount(ActionButton, {
      props: {
        action,
        previewDevices: [{ id: "abc-if00", name: "mcu EBBT0" }],
      },
    });
    await wrapper.get("button").trigger("click");
    expect(spy).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain("mcu EBBT0");

    await wrapper.get("button:not([disabled])").trigger("click");
  });

  it("lists multiple preview devices as separate rows rather than running them together", async () => {
    const action: Action = {
      ...buildAction,
      id: "flash",
      label: "Flash",
      method: "fw.flash_all",
    };
    const wrapper = mount(ActionButton, {
      props: {
        action,
        previewDevices: [
          { id: "abc-if00", name: "mcu T0_buffer" },
          { id: "def-if00", name: "mcu T1_buffer" },
        ],
      },
    });
    await wrapper.get("button").trigger("click");
    // Regression: these two used to render inline with no separator at all -
    // "mcu T0_buffermcu T1_buffer".
    const items = wrapper.findAll(".devices li").map((li) => li.text());
    expect(items).toEqual(["mcu T0_buffer", "mcu T1_buffer"]);
  });

  it("refuses to confirm a flash with no known preview devices", async () => {
    const action: Action = {
      ...buildAction,
      id: "flash",
      label: "Flash",
      method: "fw.flash_all",
    };
    const wrapper = mount(ActionButton, { props: { action } });
    await wrapper.get("button").trigger("click");
    expect(wrapper.text()).toContain("refusing to guess");
    const confirmButton = wrapper
      .findAll("button")
      .find((b) => b.text() === "Confirm");
    expect(confirmButton?.attributes("disabled")).toBeDefined();
  });

  it("fetches choices on open and sends the pick through params[param]", async () => {
    const fetchSpy = vi.spyOn(store, "fetchChoices").mockResolvedValue({
      available: [
        {
          name: "config.CartoV4USB",
          distinguishing: [{ label: "CAN bus speed" }],
        },
      ],
    });
    const invokeSpy = vi.spyOn(store, "invokeAction").mockResolvedValue(true);
    const action: Action = {
      ...buildAction,
      id: "profile",
      label: "Change profile",
      method: "fw.profile.apply",
      choices: {
        method: "fw.profile.list",
        params: { name: "carto_v4" },
        param: "profile",
      },
    };
    const wrapper = mount(ActionButton, { props: { action } });
    await wrapper.get("button").trigger("click");
    await Promise.resolve();
    await Promise.resolve();
    expect(fetchSpy).toHaveBeenCalledWith("fw.profile.list", {
      name: "carto_v4",
    });
    expect(wrapper.text()).toContain("config.CartoV4USB");
    expect(wrapper.text()).toContain("CAN bus speed");

    const optionButton = wrapper
      .findAll("button")
      .find((b) => b.text() === "config.CartoV4USB");
    await optionButton?.trigger("click");
    expect(invokeSpy).toHaveBeenCalledWith(action, {
      profile: "config.CartoV4USB",
    });
  });

  it("opens a kconfig session directly, with no confirmation", async () => {
    const spy = vi.spyOn(store, "openKconfig").mockResolvedValue(true);
    const action: Action = {
      ...buildAction,
      id: "configure:klipper",
      label: "Configure klipper",
      method: "fw.kconfig.open",
      params: { name: "carto_v4", fw: "klipper" },
    };
    const wrapper = mount(ActionButton, { props: { action } });
    await wrapper.get("button").trigger("click");
    expect(spy).toHaveBeenCalledWith("carto_v4", "klipper", false);
    expect(wrapper.text()).not.toContain("Confirm");
  });

  it("offers a scope override for a flash action, sending scope:all when checked", async () => {
    const spy = vi.spyOn(store, "invokeAction").mockResolvedValue(true);
    const action: Action = {
      ...buildAction,
      id: "flash",
      label: "Flash",
      method: "fw.flash_all",
      params: { name: "bttebb36", scope: "stale" },
    };
    const wrapper = mount(ActionButton, {
      props: {
        action,
        offersOverride: true,
        allPreviewDevices: [{ id: "230048-if00", name: "mcu EBBT0" }],
      },
    });
    await wrapper.get("button").trigger("click");
    // Stale preview is empty (none passed), so Confirm starts disabled and
    // the override switch is the only way out.
    let confirmButton = wrapper
      .findAll("button")
      .find((b) => b.text() === "Confirm");
    expect(confirmButton?.attributes("disabled")).toBeDefined();

    await wrapper.get('input[type="checkbox"]').setValue(true);
    expect(wrapper.text()).toContain("mcu EBBT0");
    confirmButton = wrapper
      .findAll("button")
      .find((b) => b.text() === "Confirm");
    expect(confirmButton?.attributes("disabled")).toBeUndefined();

    await confirmButton?.trigger("click");
    expect(spy).toHaveBeenCalledWith(action, { scope: "all" });
  });

  it("never shows the override switch when offersOverride is not set", async () => {
    const action: Action = {
      ...buildAction,
      id: "flash",
      label: "Flash",
      method: "fw.flash_all",
    };
    const wrapper = mount(ActionButton, { props: { action } });
    await wrapper.get("button").trigger("click");
    expect(wrapper.find('input[type="checkbox"]').exists()).toBe(false);
  });

  it("offers a force takeover on a kconfig session conflict", async () => {
    const spy = vi.spyOn(store, "openKconfig").mockResolvedValue(false);
    store.state.error = {
      code: "kconfig_session_conflict",
      message: "another session has unsaved changes",
    };
    const action: Action = {
      ...buildAction,
      id: "configure:klipper",
      label: "Configure klipper",
      method: "fw.kconfig.open",
      params: { name: "carto_v4", fw: "klipper" },
    };
    const wrapper = mount(ActionButton, { props: { action } });
    await wrapper.get("button").trigger("click");
    expect(wrapper.text()).toContain("Another session has unsaved changes");

    await wrapper
      .findAll("button")
      .find((b) => b.text() === "Take over anyway")
      ?.trigger("click");
    expect(spy).toHaveBeenLastCalledWith("carto_v4", "klipper", true);
    store.state.error = null;
  });
});
