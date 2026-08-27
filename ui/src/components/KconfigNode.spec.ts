import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import KconfigNode from "./KconfigNode.vue";
import type { KconfigNode as KconfigNodeType } from "../api/kconfig";

const menuNode: KconfigNodeType = {
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
};

const boolNode: KconfigNodeType = {
  id: "CONFIG_FOO",
  kind: "bool",
  name: "CONFIG_FOO",
  prompt: "Enable foo",
  depth: 1,
  value: "n",
  visible: true,
  assignable: ["y", "n"],
  options: null,
  value_label: null,
  editable: true,
  range: null,
  has_help: true,
  is_menuconfig: false,
  enterable: false,
};

const choiceNode: KconfigNodeType = {
  id: "CONFIG_MACH",
  kind: "choice",
  name: "CONFIG_MACH",
  prompt: "Micro-controller Architecture",
  depth: 1,
  value: "MACH_STM32",
  visible: true,
  assignable: ["MACH_STM32", "MACH_RP2040"],
  options: [
    { value: "MACH_STM32", label: "STMicroelectronics STM32" },
    { value: "MACH_RP2040", label: "Raspberry Pi RP2040" },
  ],
  value_label: "STMicroelectronics STM32",
  editable: true,
  range: null,
  has_help: false,
  is_menuconfig: false,
  enterable: false,
};

describe("KconfigNode", () => {
  it("renders a menu as an enter button", async () => {
    const wrapper = mount(KconfigNode, { props: { node: menuNode } });
    expect(wrapper.text()).toContain("Board");
    await wrapper.get("button").trigger("click");
    expect(wrapper.emitted("enter")?.[0]).toEqual([menuNode]);
  });

  it("emits set with y/n from a bool checkbox", async () => {
    const wrapper = mount(KconfigNode, { props: { node: boolNode } });
    const checkbox = wrapper.get('input[type="checkbox"]');
    await checkbox.setValue(true);
    expect(wrapper.emitted("set")?.[0]).toEqual([boolNode, "y"]);
  });

  it("disables the control when the node is not editable, without hiding it", () => {
    const wrapper = mount(KconfigNode, {
      props: { node: { ...boolNode, editable: false } },
    });
    expect(
      wrapper.get('input[type="checkbox"]').attributes("disabled"),
    ).toBeDefined();
    expect(wrapper.text()).toContain("🔒");
  });

  it("emits help without touching the value", async () => {
    const wrapper = mount(KconfigNode, { props: { node: boolNode } });
    await wrapper.get(".kconfig-help-btn").trigger("click");
    expect(wrapper.emitted("help")?.[0]).toEqual([boolNode]);
    expect(wrapper.emitted("set")).toBeUndefined();
  });

  it("sends the option's symbol name, not its prompt, from a choice", async () => {
    const wrapper = mount(KconfigNode, { props: { node: choiceNode } });
    await wrapper.get("select").setValue("MACH_RP2040");
    expect(wrapper.emitted("set")?.[0]).toEqual([choiceNode, "MACH_RP2040"]);
  });

  it("shows the range on a populated numeric field, not just as a placeholder", () => {
    const rangedNode: KconfigNodeType = {
      ...boolNode,
      id: "CONFIG_TIMEOUT",
      kind: "int",
      value: "30",
      range: { min: "1", max: "64" },
    };
    const wrapper = mount(KconfigNode, { props: { node: rangedNode } });
    // A placeholder only renders on an empty field, and this one has a
    // value - the hint must be its own visible element.
    expect(wrapper.text()).toContain("1..64");
  });
});
