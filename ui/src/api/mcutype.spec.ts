import { describe, expect, it } from "vitest";
import { validateTypeName, TYPE_NAME_MAX } from "./mcutype";

describe("validateTypeName", () => {
  it("accepts a normal name", () => {
    expect(validateTypeName("bttebb36", [])).toBeNull();
  });

  it("rejects an empty name", () => {
    expect(validateTypeName("   ", [])).not.toBeNull();
  });

  it("rejects a name with disallowed characters", () => {
    expect(validateTypeName("a/b", [])).not.toBeNull();
    expect(validateTypeName("a b", [])).not.toBeNull();
  });

  it("rejects a name already taken", () => {
    expect(validateTypeName("bttebb36", ["bttebb36"])).not.toBeNull();
  });

  it("rejects a name over the length limit", () => {
    expect(validateTypeName("a".repeat(TYPE_NAME_MAX + 1), [])).not.toBeNull();
  });

  it("accepts a name at exactly the length limit", () => {
    expect(validateTypeName("a".repeat(TYPE_NAME_MAX), [])).toBeNull();
  });
});
