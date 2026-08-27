import { describe, expect, it } from "vitest";
import { cancelIsImmediate } from "./jobs";

describe("cancelIsImmediate", () => {
  it("is immediate for builds - the whole make process group gets killed", () => {
    expect(cancelIsImmediate("build")).toBe(true);
    expect(cancelIsImmediate("build_all")).toBe(true);
  });

  it("is deferred for anything that writes to a board or screen", () => {
    // docs/agent-api.md's "Cancellation is not uniform": interrupting a
    // flashtool write leaves a board half-written, so these only honour a
    // cancel between devices.
    expect(cancelIsImmediate("flash")).toBe(false);
    expect(cancelIsImmediate("flash_all")).toBe(false);
    expect(cancelIsImmediate("update_all")).toBe(false);
  });

  it("is deferred for add_mcu - a bootloader write has no checkpoint either", () => {
    expect(cancelIsImmediate("add_mcu")).toBe(false);
  });
});
