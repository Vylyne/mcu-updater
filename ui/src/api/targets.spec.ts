import { describe, expect, it } from "vitest";
import { targetKey } from "./targets";

describe("targetKey", () => {
  it("qualifies the name with the provider", () => {
    // An MCU type and a display live in different config files, so nothing
    // stops them sharing a name - see docs/agent-api.md's targets section.
    expect(targetKey({ provider: "kconfig_make", name: "carto_v4" })).toBe(
      "kconfig_make:carto_v4",
    );
    expect(targetKey({ provider: "platformio", name: "carto_v4" })).toBe(
      "platformio:carto_v4",
    );
  });
});
