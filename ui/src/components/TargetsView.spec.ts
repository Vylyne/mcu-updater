import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import TargetsView from "./TargetsView.vue";
import type { Target } from "../api/targets";

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
    expect(wrapper.findAll("article.target")).toHaveLength(2);
  });
});
