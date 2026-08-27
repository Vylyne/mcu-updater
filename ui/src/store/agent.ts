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
import type { Action } from "../api/targets";
import type { Job } from "../api/jobs";
import type {
  KconfigHelp,
  KconfigMenu,
  KconfigSearchResult,
  KconfigState,
} from "../api/kconfig";

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
  job: Job | null;
  bus: unknown[];
  log: { job_id: string; lines: { i: number; s: string; t: string }[] } | null;
  /** Set when the last resync learned the ring buffer had already evicted
   * lines we asked for, or dropped some - docs/agent-api.md's "The log, and
   * its sequence numbers": tell the user rather than renumber silently. */
  logOmitted: boolean;
  events: EventLogEntry[];
  /** The one open fw.kconfig.* session, or null. A "Configure {family}"
   * action (fw.kconfig.open) opens it; docs/decisions.md's targets[]
   * projection never embeds this - it is fetched on demand the same way
   * fw.target.get's detail is. */
  kconfig: KconfigState | null;
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
  logOmitted: false,
  events: [],
  kconfig: null,
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
    const status = await callAgent<Record<string, unknown>>(
      client,
      "fw.status",
    );
    state.status = status;
    // Seeds the untracked-device list from the initial load - the `bus`
    // notify_agent_event (onBus below) only fires on a later change, so
    // without this state.bus stays [] until something is unplugged/replugged.
    state.bus = (status.bus as unknown[] | undefined) ?? state.bus;
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
    state.logOmitted = false;
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
  // routeAgentEvent has not yet advanced the cursor when this runs (it does
  // so synchronously, after this fire-and-forget call is dispatched), so
  // `logCursor.current` here is still "how many lines of this job we have
  // already rendered" - the boundary to keep, not to discard.
  const cursorBeforeGap = logCursor.current;
  try {
    const result = await callAgent<{
      log: { i: number; s: string; t: string }[];
      log_from: number;
      log_next: number;
      log_dropped: number;
    }>(client, "fw.job.get", {
      job_id: jobId,
      log_from: cursorBeforeGap,
    });
    const kept =
      state.log !== null && state.log.job_id === jobId
        ? state.log.lines.filter((line) => line.i < cursorBeforeGap)
        : [];
    // `log_from` can be higher than asked for - the log is a ring buffer and
    // a long build evicts its own beginning. Surface that rather than
    // silently renumbering, per docs/agent-api.md.
    state.logOmitted =
      result.log_from > cursorBeforeGap || result.log_dropped > 0;
    state.log = { job_id: jobId, lines: [...kept, ...result.log] };
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
      state.job = job as Job | null;
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

/** Run an action from a `targets[]`/device `actions[]` entry. `extraParams`
 * carries a `choices` pick, merged under `choices.param` by the caller before
 * this is called - this function never knows an action has choices, only
 * that `params` is whatever was decided. Returns false (and routes the
 * failure into `state.error`) rather than throwing, so a component can
 * `if (!(await invokeAction(...))) return` without its own try/catch. */
export async function invokeAction(
  action: Action,
  extraParams: Record<string, unknown> = {},
): Promise<boolean> {
  if (client === null) return false;
  try {
    await callAgent(client, action.method, {
      ...action.params,
      ...extraParams,
    });
    state.error = null;
    return true;
  } catch (error) {
    state.error = error as NormalizedAgentError;
    return false;
  }
}

/** Fetches the option list for an action's `choices`, e.g.
 * `fw.profile.list`'s `available`. The renderer stays generic over the
 * result - see docs/agent-api.md's "An action may carry `choices`". */
export async function fetchChoices(
  choicesMethod: string,
  choicesParams: Record<string, unknown>,
): Promise<Record<string, unknown> | null> {
  if (client === null) return null;
  try {
    const result = await callAgent<Record<string, unknown>>(
      client,
      choicesMethod,
      choicesParams,
    );
    state.error = null;
    return result;
  } catch (error) {
    state.error = error as NormalizedAgentError;
    return null;
  }
}

/** Cancel whatever job is currently running. The wording of what this
 * actually does belongs to the caller (jobs.ts's cancelIsImmediate, keyed on
 * job.kind so it still reads correctly after a page reload) - this just
 * makes the call. */
export async function cancelJob(): Promise<boolean> {
  if (client === null || state.job === null) return false;
  try {
    await callAgent(client, "fw.job.cancel", { job_id: state.job.id });
    state.error = null;
    return true;
  } catch (error) {
    state.error = error as NormalizedAgentError;
    return false;
  }
}

/** Change tool settings. `patch` carries only the SETTABLE keys being
 * changed - registry.py's `settings_set` refuses anything else. The reply's
 * `settings` is the full current set, so it replaces `state.status.settings`
 * outright rather than merging. Returns `changed` for a caller that wants to
 * say what actually moved (e.g. flagging a reconnect-required key). */
export async function updateSettings(
  patch: Record<string, unknown>,
): Promise<{ ok: boolean; changed: string[] }> {
  if (client === null) return { ok: false, changed: [] };
  try {
    const result = await callAgent<{
      settings: Record<string, unknown>;
      changed: string[];
    }>(client, "fw.settings.set", { settings: patch });
    if (state.status) state.status.settings = result.settings;
    state.error = null;
    return { ok: true, changed: result.changed };
  } catch (error) {
    state.error = error as NormalizedAgentError;
    return { ok: false, changed: [] };
  }
}

/** Track a physical board on the bus under an existing type - the
 * "new board, want to track it?" case docs/agent-api.md's BusDevice section
 * describes for a `tracked_by: null` entry, and also how an add_mcu job's
 * `candidates[]` get adopted afterwards. */
export async function adoptSerial(
  name: string,
  serial: string,
): Promise<boolean> {
  if (client === null) return false;
  try {
    await callAgent(client, "fw.serial.add", { name, serial });
    state.error = null;
    return true;
  } catch (error) {
    state.error = error as NormalizedAgentError;
    return false;
  }
}

/** Read-only report of what is sitting in DFU/BOOTSEL right now - the first
 * step of the add-a-bare-board flow, docs/agent-api.md's "Setting up a
 * brand-new board". Returns null (routed into state.error) on failure rather
 * than throwing, matching every other store call. */
export async function scanBareBoard(
  mechanism: "dfu" | "bootsel",
): Promise<Record<string, unknown> | null> {
  if (client === null) return null;
  try {
    const result = await callAgent<Record<string, unknown>>(
      client,
      mechanism === "dfu" ? "fw.dfu.scan" : "fw.bootsel.scan",
    );
    state.error = null;
    return result;
  } catch (error) {
    state.error = error as NormalizedAgentError;
    return null;
  }
}

/** Write Katapult to a bare board over DFU/BOOTSEL. Returns immediately with
 * a job - the running/succeeded/failed state, including the eventual
 * `candidates`/`already_tracked` result, arrives the normal way through the
 * `job` notify_agent_event JobPanel already renders, since job submission
 * emits its own state (agent/jobs.py's `on_job_change`). */
export async function startAddMcu(
  name: string,
  dfuSerial?: string,
): Promise<boolean> {
  if (client === null) return false;
  try {
    await callAgent(client, "fw.add_mcu.start", {
      name,
      ...(dfuSerial ? { dfu_serial: dfuSerial } : {}),
    });
    state.error = null;
    return true;
  } catch (error) {
    state.error = error as NormalizedAgentError;
    return false;
  }
}

function applyKconfigMenu(menu: KconfigMenu): void {
  // A menu-changing reply (open/enter/up/set/reset) replaces the screen
  // outright - search and help are stale the moment the tree underneath
  // them can have moved.
  state.kconfig = { ...menu, search: null, help: null };
}

/** Open a configuration session for one family. `force: true` takes over a
 * session another tab left with unsaved changes - docs/agent-api.md's
 * kconfig_session_conflict. Returns false (and routes the refusal into
 * state.error, whose `data.session`/`data.type`/`data.fw` name the
 * conflicting session) rather than throwing. */
export async function openKconfig(
  name: string,
  fw: string,
  force = false,
): Promise<boolean> {
  if (client === null) return false;
  // Opening a second session while one is already held locally would
  // orphan the first on the agent - never closed, and dirty edits in it
  // would then raise kconfig_session_conflict against a session this UI
  // itself can no longer reach.
  if (state.kconfig !== null) closeKconfig();
  try {
    const menu = await callAgent<KconfigMenu>(client, "fw.kconfig.open", {
      name,
      fw,
      force,
    });
    applyKconfigMenu(menu);
    state.error = null;
    return true;
  } catch (error) {
    state.error = error as NormalizedAgentError;
    return false;
  }
}

async function kconfigCall(
  method: string,
  extra: Record<string, unknown> = {},
): Promise<boolean> {
  if (client === null || state.kconfig === null) return false;
  try {
    const menu = await callAgent<KconfigMenu>(client, method, {
      session: state.kconfig.session,
      ...extra,
    });
    applyKconfigMenu(menu);
    state.error = null;
    return true;
  } catch (error) {
    state.error = error as NormalizedAgentError;
    return false;
  }
}

export function kconfigEnter(id: string): Promise<boolean> {
  return kconfigCall("fw.kconfig.enter", { id });
}

export function kconfigUp(): Promise<boolean> {
  return kconfigCall("fw.kconfig.up");
}

/** Assign one symbol. The reply is the whole current menu, because one
 * assignment can rewrite the screen - picking a different architecture
 * replaces essentially every row. */
export function kconfigSet(id: string, value: string): Promise<boolean> {
  return kconfigCall("fw.kconfig.set", { id, value });
}

/** Discard unsaved edits by reparsing from disk. */
export function kconfigReset(): Promise<boolean> {
  return kconfigCall("fw.kconfig.reset");
}

/** Help is fetched per symbol rather than shipped with the tree - Klipper's
 * full help is several hundred KB against 40-80 KB for the tree without it. */
export async function kconfigHelp(id: string): Promise<boolean> {
  if (client === null || state.kconfig === null) return false;
  try {
    const help = await callAgent<KconfigHelp>(client, "fw.kconfig.help", {
      session: state.kconfig.session,
      id,
    });
    state.kconfig.help = help;
    state.error = null;
    return true;
  } catch (error) {
    state.error = error as NormalizedAgentError;
    return false;
  }
}

export function closeKconfigHelp(): void {
  if (state.kconfig !== null) state.kconfig.help = null;
}

/** Search replaces the node list while active; an empty query clears it
 * locally without a round trip. */
export async function kconfigSearch(query: string): Promise<boolean> {
  if (client === null || state.kconfig === null) return false;
  if (!query.trim()) {
    state.kconfig.search = null;
    return true;
  }
  try {
    const result = await callAgent<KconfigSearchResult>(
      client,
      "fw.kconfig.search",
      { session: state.kconfig.session, query },
    );
    state.kconfig.search = result;
    state.error = null;
    return true;
  } catch (error) {
    state.error = error as NormalizedAgentError;
    return false;
  }
}

export function clearKconfigSearch(): void {
  if (state.kconfig !== null) state.kconfig.search = null;
}

/** Write the answers, optionally building straight afterwards. The agent
 * takes the *build* lock for this and can refuse with `busy` while a build
 * runs - the unsaved edits survive that, so retrying later loses nothing.
 * A started build's job/log events arrive the normal way; nothing here
 * needs to adopt them. */
export async function kconfigSave(build: boolean): Promise<boolean> {
  if (client === null || state.kconfig === null) return false;
  try {
    const result = await callAgent<{ menu?: KconfigMenu }>(
      client,
      "fw.kconfig.save",
      { session: state.kconfig.session, build },
    );
    if (result.menu) applyKconfigMenu(result.menu);
    state.error = null;
    return true;
  } catch (error) {
    state.error = error as NormalizedAgentError;
    return false;
  }
}

/** Fire-and-forget: the session expires on its own, so a failure here is
 * not worth reporting to someone who already closed the dialog. */
export function closeKconfig(): void {
  const session = state.kconfig?.session;
  state.kconfig = null;
  if (client === null || session === undefined) return;
  void callAgent(client, "fw.kconfig.close", { session }).catch(() => {
    /* see comment above */
  });
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
