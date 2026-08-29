import { afterEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import TypeDialog from "./TypeDialog.vue";
import * as store from "../store/agent";
import type { Family } from "../api/mcutype";

const families: Family[] = [
  {
    name: "klipper",
    source: "~/klipper",
    artifact: "out/klipper.bin",
    builder: "kconfig_make",
    bootloader: false,
    present: true,
    configurable: true,
    builtin: true,
  },
  {
    name: "katapult",
    source: "~/katapult",
    artifact: "out/katapult.bin",
    builder: "kconfig_make",
    bootloader: true,
    present: true,
    configurable: true,
    builtin: true,
  },
];

const detail = {
  chipset: "stm32g0b1xx",
  firmware: "klipper",
  katapult_installed: true,
  klipper: {
    extra_args: "-j2",
    extra_repos: ["/home/pi/buffer_manager"],
    makefile_patches: [{ file: "src/Makefile", line: "src-y += buffer.c" }],
  },
  katapult: {
    extra_args: "",
    extra_repos: [],
    makefile_patches: [],
  },
};

function textareaFor(wrapper: VueWrapper, labelText: string) {
  const label = wrapper
    .findAll("label")
    .find((l) => l.text().includes(labelText));
  if (!label) throw new Error(`no label found containing ${labelText}`);
  return label.find("textarea");
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("TypeDialog", () => {
  it("prefills extra repos and makefile patches from fw.target.get", async () => {
    vi.spyOn(store, "fetchTargetDetail").mockResolvedValue(detail);
    const wrapper = mount(TypeDialog, {
      props: { typeName: "bttebb36", existingNames: [], families },
    });
    await flushPromises();

    expect(textareaFor(wrapper, "Klipper extra repos").element.value).toBe(
      "/home/pi/buffer_manager",
    );
    expect(textareaFor(wrapper, "Klipper makefile patches").element.value).toBe(
      "src/Makefile -> src-y += buffer.c",
    );
    expect(textareaFor(wrapper, "Katapult extra repos").element.value).toBe("");
  });

  it("parses the textareas into the wire shape on save", async () => {
    vi.spyOn(store, "fetchTargetDetail").mockResolvedValue(detail);
    const updateSpy = vi
      .spyOn(store, "updateType")
      .mockResolvedValue({ ok: true, warnings: [] });
    const wrapper = mount(TypeDialog, {
      props: { typeName: "bttebb36", existingNames: [], families },
    });
    await flushPromises();

    await textareaFor(wrapper, "Klipper extra repos").setValue(
      "/home/pi/buffer_manager\n/home/pi/other",
    );
    await textareaFor(wrapper, "Klipper makefile patches").setValue(
      "src/Makefile -> src-y += buffer.c",
    );

    const save = wrapper.findAll("button").find((b) => b.text() === "Save");
    await save!.trigger("click");

    expect(updateSpy).toHaveBeenCalledWith(
      "bttebb36",
      expect.objectContaining({
        klipper_extra_repos: ["/home/pi/buffer_manager", "/home/pi/other"],
        klipper_makefile_patches: [
          { file: "src/Makefile", line: "src-y += buffer.c" },
        ],
      }),
    );
  });

  it("sends empty arrays for a new type when the advanced fields are untouched", async () => {
    const addSpy = vi
      .spyOn(store, "addType")
      .mockResolvedValue({ ok: true, warnings: [] });
    const wrapper = mount(TypeDialog, {
      props: { typeName: null, existingNames: [], families },
    });
    await flushPromises();

    await wrapper.get("input[maxlength]").setValue("newtype");
    await wrapper
      .findAll("input")
      .find((i) => i.attributes("placeholder"))!
      .setValue("stm32g0b1xx");

    const create = wrapper.findAll("button").find((b) => b.text() === "Create");
    await create!.trigger("click");

    expect(addSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        applicationExtraRepos: [],
        applicationMakefilePatches: [],
        katapultExtraRepos: [],
        katapultMakefilePatches: [],
      }),
    );
  });

  it("keeps the dialog open and shows a warning the agent returned after saving", async () => {
    vi.spyOn(store, "fetchTargetDetail").mockResolvedValue(detail);
    vi.spyOn(store, "updateType").mockResolvedValue({
      ok: true,
      warnings: [
        "/home/pi/buffer_manager has no git HEAD yet - staleness won't fire for it until it does.",
      ],
    });
    const wrapper = mount(TypeDialog, {
      props: { typeName: "bttebb36", existingNames: [], families },
    });
    await flushPromises();

    const save = wrapper.findAll("button").find((b) => b.text() === "Save");
    await save!.trigger("click");

    expect(wrapper.text()).toContain("has no git HEAD yet");
    expect(wrapper.emitted("close")).toBeUndefined();
  });

  it("closes once saved cleanly with no warnings", async () => {
    vi.spyOn(store, "fetchTargetDetail").mockResolvedValue(detail);
    vi.spyOn(store, "updateType").mockResolvedValue({ ok: true, warnings: [] });
    const wrapper = mount(TypeDialog, {
      props: { typeName: "bttebb36", existingNames: [], families },
    });
    await flushPromises();

    const save = wrapper.findAll("button").find((b) => b.text() === "Save");
    await save!.trigger("click");

    expect(wrapper.emitted("close")).toBeTruthy();
  });
});
