// Plain reactive() singleton, not a store library - the panel's Vuex/Pinia
// weight bought nothing here, and this repo stays dependency-frugal by
// default. Components import `state` directly and call the exported actions.

import { reactive } from "vue";
import {
  MoonrakerClient,
  type ConnectionState,
  type WebSocketFactory,
} from "../api/moonraker";
import {
  AGENT_NAME,
  SUPPORTED_API_VERSION,
  UI_CLIENT_VERSION,
  callAgent,
  normalizeAgentError,
  type NormalizedAgentError,
} from "../api/agent";
import {
  parseAgentEventParams,
  routeAgentEvent,
  type LogBatch,
  type LogCursor,
} from "../api/events";

export interface EventLogEntry {
  at: number;
  event: string;
}

export interface AgentStoreState {
  connection: ConnectionState;
  /** null until server.extensions.list has answered at least once. */
  agentAvailable: boolean | null;
  ping: Record<string, unknown> | null;
  status: Record<string, unknown> | null;
  job: unknown | null;
  bus: unknown[];
  log: { job_id: string; lines: { i: number; s: string; t: string }[] } | null;
  events: EventLogEntry[];
  error: NormalizedAgentError | null;
  /** Set when the agent's api_version exceeds SUPPORTED_API_VERSION - the
   * gate from docs/agent-api.md's fw.ping section. Non-null means "refuse to
   * render the printer state", not just "show a warning". */
  unsupportedApiVersion: number | null;
}

export const state: AgentStoreState = reactive({
  connection: "closed",
  agentAvailable: null,
  ping: null,
  status: null,
  job: null,
  bus: [],
  log: null,
  events: [],
  error: null,
  unsupportedApiVersion: null,
});

const MAX_EVENT_LOG = 50;
const logCursor: LogCursor = { current: 0 };

let client: MoonrakerClient | null = null;

function pushEventLog(event: string): void {
  state.events.push({ at: Date.now(), event });
  if (state.events.length > MAX_EVENT_LOG) state.events.shift();
}

export function hasCapability(name: string): boolean {
  const caps = state.ping?.capabilities as string[] | undefined;
  return Array.isArray(caps) && caps.includes(name);
}

async function refreshStatus(): Promise<void> {
  if (client === null) return;
  try {
    state.status = await callAgent<Record<string, unknown>>(
      client,
      "fw.status",
    );
    state.error = null;
  } catch (error) {
    state.error = error as NormalizedAgentError;
  }
}

async function ping(): Promise<void> {
  if (client === null) return;
  try {
    const result = await callAgent<Record<string, unknown>>(client, "fw.ping");
    state.ping = result;
    const apiVersion = result.api_version as number;
    if (apiVersion > SUPPORTED_API_VERSION) {
      state.unsupportedApiVersion = apiVersion;
      return;
    }
    state.unsupportedApiVersion = null;
    // A fresh ping means a fresh connection to reason about the log from -
    // any job still running comes back as its own `job`/`log` events per
    // docs/agent-api.md's "A job outlives the connection" note.
    logCursor.current = 0;
    state.log = null;
    await refreshStatus();
  } catch (error) {
    state.error = error as NormalizedAgentError;
  }
}

/** identify -> server.extensions.list -> fw.ping -> fw.status, run once per
 * connection (including every reconnect) - see docs/agent-api.md's
 * "Availability detection". */
async function afterConnect(): Promise<void> {
  if (client === null) return;
  state.error = null;
  try {
    await client.call("server.connection.identify", {
      client_name: "mcu-updater-ui",
      version: UI_CLIENT_VERSION,
      type: "web",
      url: "https://github.com/Vylyne/mcu-updater",
    });
  } catch (error) {
    state.error = normalizeAgentError(error);
    return;
  }

  let found = false;
  try {
    const list = await client.call<{ agents?: { name?: string }[] }>(
      "server.extensions.list",
      {},
    );
    found = (list.agents ?? []).some((agent) => agent.name === AGENT_NAME);
  } catch (error) {
    state.error = normalizeAgentError(error);
  }
  state.agentAvailable = found;
  if (found) await ping();
}

function resetOnDisconnect(): void {
  state.agentAvailable = null;
  state.ping = null;
  state.unsupportedApiVersion = null;
}

async function resyncLog(jobId: string): Promise<void> {
  if (client === null) return;
  try {
    const result = await callAgent<{
      log: { i: number; s: string; t: string }[];
      log_next: number;
    }>(client, "fw.job.get", {
      job_id: jobId,
      log_from: logCursor.current,
    });
    state.log = { job_id: jobId, lines: result.log };
    logCursor.current = result.log_next;
  } catch (error) {
    state.error = error as NormalizedAgentError;
  }
}

function appendLog(batch: LogBatch): void {
  if (state.log === null || state.log.job_id !== batch.job_id) {
    state.log = { job_id: batch.job_id, lines: [] };
  }
  state.log.lines.push(...batch.lines);
}

function handleNotification(method: string, params: unknown): void {
  if (method !== "notify_agent_event") return;
  const envelope = parseAgentEventParams(params);
  if (envelope === null) return;
  pushEventLog(envelope.event);
  routeAgentEvent(envelope, logCursor, {
    onState: (status) => {
      state.status = status as Record<string, unknown>;
    },
    onBus: (devices) => {
      state.bus = devices;
    },
    onJob: (job) => {
      state.job = job;
    },
    onLog: (batch, isGap) => {
      if (isGap) {
        void resyncLog(batch.job_id);
        return;
      }
      appendLog(batch);
    },
    onConnected: () => {
      void ping();
    },
    onDisconnected: () => {
      state.agentAvailable = false;
    },
  });
}

/** One target row's full detail, on demand - the row itself never needs
 * this, only a caller opening it. Returns null on any failure, having
 * already routed it through `state.error` the same way every other call
 * here does. */
export async function fetchTargetDetail(
  name: string,
  provider: string,
): Promise<Record<string, unknown> | null> {
  if (client === null) return null;
  try {
    const result = await callAgent<{
      provider: string;
      target: Record<string, unknown>;
    }>(client, "fw.target.get", { name, provider });
    return result.target;
  } catch (error) {
    state.error = error as NormalizedAgentError;
    return null;
  }
}

export function connect(url: string, wsFactory?: WebSocketFactory): void {
  client?.close();
  client = new MoonrakerClient(url, wsFactory);
  client.onStateChange((connectionState) => {
    state.connection = connectionState;
    if (connectionState === "open") void afterConnect();
    else resetOnDisconnect();
  });
  client.onNotification(handleNotification);
  client.connect();
}

export function disconnect(): void {
  client?.close();
  client = null;
  resetOnDisconnect();
}
