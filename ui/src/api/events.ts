// The notify_agent_event router. docs/agent-api.md's "Events" section is the
// source of truth for these shapes:
//
//   {"jsonrpc": "2.0", "method": "notify_agent_event",
//    "params": [{"agent": "mcu_updater", "event": "state", "data": {...}}]}
//
// `params` is a list with one object; `data` is omitted entirely when null
// (agent/events.py's EventEmitter.emit), not present-and-null - every reader
// here treats it as optional rather than nullable for that reason.

export interface LogLine {
  i: number;
  s: string;
  t: string;
}

export interface LogBatch {
  job_id: string;
  seq: number;
  lines: LogLine[];
}

export interface AgentEventEnvelope {
  agent: string;
  event: string;
  data?: unknown;
}

export function parseAgentEventParams(
  params: unknown,
): AgentEventEnvelope | null {
  const envelope = Array.isArray(params) ? params[0] : params;
  if (envelope === null || typeof envelope !== "object") return null;
  if (typeof (envelope as { event?: unknown }).event !== "string") return null;
  return envelope as AgentEventEnvelope;
}

/** Mutable so the caller can track "next expected log index" across calls
 * without this module owning any state itself. */
export interface LogCursor {
  current: number;
}

export interface AgentEventHandlers {
  onState: (status: unknown) => void;
  onBus: (devices: unknown[]) => void;
  onJob: (job: unknown) => void;
  /** `isGap` is true when `batch.seq` does not match the cursor - per
   * docs/agent-api.md's "Client contract", the caller must then resync via
   * `fw.job.get {job_id, log_from: <cursor before this call>}` rather than
   * append the batch as-is. The cursor is still advanced to `seq + lines.length`
   * either way, since the next resync (if any) should ask from the newest
   * point this event told us about. */
  onLog: (batch: LogBatch, isGap: boolean) => void;
  /** Moonraker's own agent-connected/disconnected events, carried as
   * `event: "connected" | "disconnected"` inside notify_agent_event -
   * see agent/events.py's RESERVED_EVENTS comment. `data` is the agent's
   * identify payload on `connected`, absent on `disconnected`. */
  onConnected: (identify: unknown) => void;
  onDisconnected: () => void;
}

export function routeAgentEvent(
  envelope: AgentEventEnvelope,
  cursor: LogCursor,
  handlers: AgentEventHandlers,
): void {
  const data = envelope.data;
  switch (envelope.event) {
    case "state":
      handlers.onState(data);
      return;
    case "bus":
      handlers.onBus(
        (data as { devices?: unknown[] } | undefined)?.devices ?? [],
      );
      return;
    case "job":
      handlers.onJob((data as { job?: unknown } | undefined)?.job ?? null);
      return;
    case "log": {
      const batch = data as LogBatch;
      const isGap = batch.seq !== cursor.current;
      handlers.onLog(batch, isGap);
      cursor.current = batch.seq + batch.lines.length;
      return;
    }
    case "connected":
      handlers.onConnected(data);
      return;
    case "disconnected":
      handlers.onDisconnected();
      return;
    default:
      // A future event this build doesn't know about - deliberately ignored
      // rather than surfaced as an error, the same tolerance additive fields
      // get elsewhere in this API.
      return;
  }
}
