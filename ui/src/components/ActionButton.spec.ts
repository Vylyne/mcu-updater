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
    const items = wrapper
      .findAll(".detail-block > div")
      .map((row) => row.get("strong").text());
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
      .find((b) => b.text() === "Flash carto_v4");
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
        previewDevices: [],
        allPreviewDevices: [{ id: "230048-if00", name: "mcu EBBT0" }],
      },
    });
    await wrapper.get("button").trigger("click");
    // Nothing looks stale, so the confirm starts disabled - saying so the way
    // the fleet dialog does - and the override switch is the only way out.
    expect(wrapper.text()).toContain("Nothing for this to do right now.");
    let confirmButton = wrapper
      .findAll("button")
      .find((b) => b.text() === "Flash bttebb36");
    expect(confirmButton?.attributes("disabled")).toBeDefined();

    await wrapper.get('input[type="checkbox"]').setValue(true);
    expect(wrapper.text()).toContain("mcu EBBT0");
    confirmButton = wrapper
      .findAll("button")
      .find((b) => b.text() === "Flash bttebb36");
    expect(confirmButton?.attributes("disabled")).toBeUndefined();

    await confirmButton?.trigger("click");
    expect(spy).toHaveBeenCalledWith(action, { scope: "all" });
  });

  it("lays a type's flash out the way the fleet-wide flash is laid out", async () => {
    const action: Action = {
      ...buildAction,
      id: "flash",
      label: "Flash",
      method: "fw.flash_all",
      params: { name: "roadrunner", scope: "stale" },
    };
    const wrapper = mount(ActionButton, {
      props: {
        action,
        offersOverride: true,
        previewDevices: [{ id: "RR-5K3D", name: null }],
        allPreviewDevices: [{ id: "RR-5K3D", name: null }],
      },
    });
    await wrapper.get("button").trigger("click");

    expect(wrapper.get(".dialog h2").text()).toBe("Flash roadrunner");
    expect(wrapper.text()).toContain(
      "Flash every device of roadrunner that needs it. This stops Klipper once for the whole batch.",
    );
    expect(wrapper.text()).toContain(
      "Only what the recorded provenance says needs doing.",
    );
    expect(wrapper.text()).toContain("Will flash:");
    expect(wrapper.text()).toContain("This is a preview only");
    expect(wrapper.get(".alert--warning").text()).toBe(
      "This stops Klipper and writes to hardware. Do not interrupt it once started.",
    );
    // A cmake device has no name: its id is said once, not twice.
    expect(wrapper.get(".detail-block > div").text()).toBe("RR-5K3D");
    const primary = wrapper
      .findAll("button")
      .find((b) => b.text() === "Flash roadrunner");
    expect(primary?.attributes("disabled")).toBeUndefined();
  });

  it("titles a single device's flash with the device, with no scope switch", async () => {
    const action: Action = {
      ...buildAction,
      id: "flash",
      label: "Flash",
      method: "fw.flash",
      params: { name: "bttebb36", serial: "230048" },
    };
    const wrapper = mount(ActionButton, {
      props: {
        action,
        previewDevices: [{ id: "230048-if00", name: "mcu EBBT0" }],
      },
    });
    await wrapper.get("button").trigger("click");

    expect(wrapper.get(".dialog h2").text()).toBe("Flash mcu EBBT0");
    expect(wrapper.find('input[type="checkbox"]').exists()).toBe(false);
    expect(wrapper.text()).not.toContain("every device");
    expect(wrapper.get(".detail-block > div").text()).toContain("230048-if00");
    expect(wrapper.find(".alert--warning").exists()).toBe(true);
    expect(
      wrapper.findAll("button").some((b) => b.text() === "Flash mcu EBBT0"),
    ).toBe(true);
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
