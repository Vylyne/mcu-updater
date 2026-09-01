import { afterEach, describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import TargetsView from "./TargetsView.vue";
import type { Target } from "../api/targets";
import { state } from "../store/agent";

afterEach(() => {
  state.ping = null;
  state.status = null;
});

function makeTarget(provider: Target["provider"], name: string): Target {
  return {
    provider,
    name,
    descriptor: "x",
    firmware: provider === "kconfig_make" ? "klipper" : null,
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

describe("TargetsView", () => {
  it("shows an empty state with no targets", () => {
    const wrapper = mount(TargetsView, { props: { targets: [] } });
    expect(wrapper.text()).toContain("No targets configured yet.");
  });

  it("treats undefined the same as an empty list", () => {
    const wrapper = mount(TargetsView, { props: { targets: undefined } });
    expect(wrapper.text()).toContain("No targets configured yet.");
  });

  it("renders one row per target, MCU and display alike, through one component", () => {
    const targets = [
      makeTarget("kconfig_make", "bttebb36"),
      makeTarget("platformio", "knomi"),
    ];
    const wrapper = mount(TargetsView, { props: { targets } });
    expect(wrapper.text()).toContain("bttebb36");
    expect(wrapper.text()).toContain("knomi");
    expect(wrapper.findAll("article.target-row")).toHaveLength(2);
  });

  it("hides the fleet toolbar without the matching capabilities", () => {
    const targets = [makeTarget("kconfig_make", "bttebb36")];
    const wrapper = mount(TargetsView, { props: { targets } });
    expect(
      wrapper.find('[title="Build everything that needs it"]').exists(),
    ).toBe(false);
    expect(
      wrapper.find('[title="Flash everything that needs it"]').exists(),
    ).toBe(false);
  });

  it("shows build/flash-all once the agent advertises them", () => {
    state.ping = { capabilities: ["fw.build_all", "fw.flash_all"] };
    const targets = [makeTarget("kconfig_make", "bttebb36")];
    const wrapper = mount(TargetsView, { props: { targets } });
    expect(
      wrapper.find('[title="Build everything that needs it"]').exists(),
    ).toBe(true);
    expect(
      wrapper.find('[title="Flash everything that needs it"]').exists(),
    ).toBe(true);
  });

  it("keeps the add-board wizard mounted after its menu closes", async () => {
    state.ping = { capabilities: ["fw.add_mcu.start"] };
    state.status = {
      targets: [makeTarget("kconfig_make", "carto_v4")],
    } as never;
    const wrapper = mount(TargetsView, {
      props: { targets: [makeTarget("kconfig_make", "carto_v4")] },
    });

    await wrapper.get('[aria-label="More actions"]').trigger("click");
    const addBoard = wrapper
      .get(".menu-list")
      .findAll("button")
      .find((button) => button.text().includes("Add new board"));
    expect(addBoard).toBeDefined();
    await addBoard!.trigger("click");

    expect(wrapper.find(".dialog-backdrop").exists()).toBe(true);
    expect(wrapper.find(".menu-list").exists()).toBe(false);
  });
});
