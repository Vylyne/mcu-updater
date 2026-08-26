import { describe, expect, it, vi } from "vitest";
import { callAgent, normalizeAgentError } from "./agent";
import type { MoonrakerClient } from "./moonraker";

describe("normalizeAgentError", () => {
  it("reports not_connected for a null/undefined rejection", () => {
    expect(normalizeAgentError(undefined)).toEqual({
      code: "not_connected",
      message: "Not connected to Moonraker.",
    });
    expect(normalizeAgentError(null)).toEqual({
      code: "not_connected",
      message: "Not connected to Moonraker.",
    });
  });

  it("passes through MoonrakerClient's own synthesized errors unchanged", () => {
    expect(
      normalizeAgentError({
        code: "timeout",
        message: "fw.status did not respond in time.",
      }),
    ).toEqual({
      code: "timeout",
      message: "fw.status did not respond in time.",
    });
  });

  it("unwraps the nested UpdaterError shape from a real agent refusal", () => {
    const error = {
      code: -32000,
      message: "MCU type 'nope' does not exist.",
      data: {
        code: "unknown_type",
        message: "MCU type 'nope' does not exist.",
        data: { known: ["bttebb36"] },
      },
    };
    expect(normalizeAgentError(error)).toEqual({
      code: "unknown_type",
      message: "MCU type 'nope' does not exist.",
      data: { known: ["bttebb36"] },
    });
  });

  it("degrades to agent_error when data.code is missing, rather than dropping the failure", () => {
    const error = { code: -32000, message: "something went wrong" };
    expect(normalizeAgentError(error)).toEqual({
      code: "agent_error",
      message: "something went wrong",
      data: undefined,
    });
  });
});

describe("callAgent", () => {
  it("wraps the call in the server.extensions.request envelope", async () => {
    const call = vi.fn().mockResolvedValue({ ok: true });
    const client = { call } as unknown as MoonrakerClient;

    const result = await callAgent(client, "fw.status", {});

    expect(call).toHaveBeenCalledWith(
      "server.extensions.request",
      { agent: "mcu_updater", method: "fw.status", arguments: {} },
      15000,
    );
    expect(result).toEqual({ ok: true });
  });

  it("normalizes a rejection before it reaches the caller", async () => {
    const call = vi.fn().mockRejectedValue({
      code: -32000,
      message: "refused",
      data: { code: "flashing_disabled", message: "Flashing is disabled." },
    });
    const client = { call } as unknown as MoonrakerClient;

    await expect(callAgent(client, "fw.flash", {})).rejects.toEqual({
      code: "flashing_disabled",
      message: "Flashing is disabled.",
      data: undefined,
    });
  });
});
