// The single funnel every `fw.*` call goes through, mirroring
// mainsail/src/store/server/fwUpdater/actions.ts's `request` action - one
// place to hold the timeout and the error-shape translation, so no component
// writes the server.extensions.request envelope by hand.
//
// docs/agent-api.md is the source of truth for both constants below;
// tests/test_ui_contract.py asserts SUPPORTED_API_VERSION against
// mcu_updater.API_VERSION so the two cannot drift silently.

import type { MoonrakerClient, RpcError } from "./moonraker";

export const AGENT_NAME = "mcu_updater";
export const SUPPORTED_API_VERSION = 4;

/** The identify `version` field is informational on the Moonraker side, not
 * parsed - it does not need to track ui/package.json. */
export const UI_CLIENT_VERSION = "0.1.0";

export interface NormalizedAgentError {
  code: string;
  message: string;
  data?: unknown;
}

/**
 * Turn a rejected agent call into { code, message, data }.
 *
 * A refusal from the agent arrives as a JSON-RPC error whose `data` is the
 * UpdaterError's to_dict(), i.e. { code, message, data } - see
 * docs/agent-api.md's "Errors" section. That is nested one level down from
 * what MoonrakerClient.call rejects with. A call that never reached the
 * agent at all (not_connected, timeout) already carries its code at the top
 * level, from MoonrakerClient itself, and is passed through unchanged.
 */
export function normalizeAgentError(error: unknown): NormalizedAgentError {
  if (error === null || error === undefined) {
    return { code: "not_connected", message: "Not connected to Moonraker." };
  }

  const err = error as RpcError;
  if (typeof err.code === "string") {
    // Already one of MoonrakerClient's own synthesized errors.
    return { code: err.code, message: err.message ?? "Unknown error." };
  }

  const nested = err.data as
    { code?: string; message?: string; data?: unknown } | undefined;
  return {
    code: nested?.code ?? "agent_error",
    message:
      nested?.message ??
      err.message ??
      "The firmware agent refused the request.",
    data: nested?.data,
  };
}

export async function callAgent<T = unknown>(
  client: MoonrakerClient,
  method: string,
  args: Record<string, unknown> = {},
  timeoutMs = 15000,
): Promise<T> {
  try {
    return await client.call<T>(
      "server.extensions.request",
      { agent: AGENT_NAME, method, arguments: args },
      timeoutMs,
    );
  } catch (error) {
    throw normalizeAgentError(error);
  }
}
