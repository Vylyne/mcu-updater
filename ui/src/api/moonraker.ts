// The websocket transport to Moonraker. Framing here is plain JSON-over-WS -
// the ETX-terminated framing in docs/agent-api.md is for the agent's own unix
// socket connection to Moonraker and does not apply to a browser client.

export type ConnectionState = "connecting" | "open" | "closed";

/** The subset of the DOM WebSocket surface this module needs, so tests can
 * inject a fake without a real network or jsdom's WebSocket stub. */
export interface WebSocketLike {
  readonly readyState: number;
  send(data: string): void;
  close(): void;
  addEventListener(
    type: string,
    listener: (ev: { data?: string }) => void,
  ): void;
}

export type WebSocketFactory = (url: string) => WebSocketLike;

const WS_OPEN = 1;

const defaultFactory: WebSocketFactory = (url) => new WebSocket(url);

/** Moonraker's own JSON-RPC error shape, or one of this client's own
 * synthesized codes (`not_connected`, `timeout`) for a call that never
 * reached the wire. */
export interface RpcError {
  code: string | number;
  message: string;
  data?: unknown;
}

interface PendingCall {
  resolve: (value: unknown) => void;
  reject: (error: RpcError) => void;
  timeout: ReturnType<typeof setTimeout>;
}

const NOT_CONNECTED: RpcError = {
  code: "not_connected",
  message: "Not connected to Moonraker.",
};

export class MoonrakerClient {
  private ws: WebSocketLike | null = null;
  private nextId = 1;
  private readonly pending = new Map<number, PendingCall>();
  private readonly notificationHandlers = new Set<
    (method: string, params: unknown) => void
  >();
  private readonly stateHandlers = new Set<(state: ConnectionState) => void>();
  private reconnectAttempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private closedByUser = true;
  state: ConnectionState = "closed";

  constructor(
    private readonly url: string,
    private readonly factory: WebSocketFactory = defaultFactory,
  ) {}

  connect(): void {
    this.closedByUser = false;
    this.open();
  }

  close(): void {
    this.closedByUser = true;
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.ws?.close();
    this.ws = null;
    this.setState("closed");
    this.rejectAllPending(NOT_CONNECTED);
  }

  onNotification(
    handler: (method: string, params: unknown) => void,
  ): () => void {
    this.notificationHandlers.add(handler);
    return () => this.notificationHandlers.delete(handler);
  }

  onStateChange(handler: (state: ConnectionState) => void): () => void {
    this.stateHandlers.add(handler);
    return () => this.stateHandlers.delete(handler);
  }

  /** Moonraker's own `call_method_with_response` has no timeout - if the
   * agent never answers, the socket just never gets a reply. `timeoutMs` is
   * the client-side backstop so a wedged agent can't leave a caller waiting
   * forever. Keyed per request id, not a single shared timer, so one slow
   * call cannot mis-time another in flight beside it. */
  call<T = unknown>(
    method: string,
    params: Record<string, unknown> = {},
    timeoutMs = 15000,
  ): Promise<T> {
    if (this.ws === null || this.ws.readyState !== WS_OPEN) {
      return Promise.reject(NOT_CONNECTED);
    }
    const ws = this.ws;
    const id = this.nextId++;
    return new Promise<T>((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.pending.delete(id);
        reject({
          code: "timeout",
          message: `${method} did not respond in time.`,
        });
      }, timeoutMs);
      this.pending.set(id, {
        resolve: resolve as (value: unknown) => void,
        reject,
        timeout,
      });
      ws.send(JSON.stringify({ jsonrpc: "2.0", id, method, params }));
    });
  }

  private setState(state: ConnectionState): void {
    this.state = state;
    for (const handler of this.stateHandlers) handler(state);
  }

  private open(): void {
    this.setState("connecting");
    let ws: WebSocketLike;
    try {
      ws = this.factory(this.url);
    } catch {
      // No WebSocket implementation available (e.g. this file evaluated
      // outside a browser), or a synchronous construction failure. Same
      // recovery path as a dropped connection.
      this.setState("closed");
      if (!this.closedByUser) this.scheduleReconnect();
      return;
    }
    this.ws = ws;
    ws.addEventListener("open", () => {
      this.reconnectAttempt = 0;
      this.setState("open");
    });
    ws.addEventListener("close", () => {
      this.setState("closed");
      this.rejectAllPending(NOT_CONNECTED);
      if (!this.closedByUser) this.scheduleReconnect();
    });
    ws.addEventListener("message", (event: { data: string }) =>
      this.handleMessage(event.data),
    );
  }

  private scheduleReconnect(): void {
    const delay = Math.min(1000 * 2 ** this.reconnectAttempt, 15000);
    this.reconnectAttempt += 1;
    this.reconnectTimer = setTimeout(() => this.open(), delay);
  }

  private rejectAllPending(error: RpcError): void {
    for (const call of this.pending.values()) {
      clearTimeout(call.timeout);
      call.reject(error);
    }
    this.pending.clear();
  }

  private handleMessage(raw: string): void {
    let msg: {
      id?: number;
      result?: unknown;
      error?: RpcError;
      method?: string;
      params?: unknown;
    };
    try {
      msg = JSON.parse(raw);
    } catch {
      return;
    }

    if (msg.id !== undefined && this.pending.has(msg.id)) {
      const call = this.pending.get(msg.id)!;
      this.pending.delete(msg.id);
      clearTimeout(call.timeout);
      if (msg.error !== undefined) call.reject(msg.error);
      else call.resolve(msg.result);
      return;
    }

    if (msg.method !== undefined) {
      for (const handler of this.notificationHandlers)
        handler(msg.method, msg.params);
    }
  }
}

/** One-shot HTTP path for calls that don't need the websocket - `/access/info`
 * during startup, before a connection necessarily exists. */
export async function httpGetJson<T>(
  path: string,
  apiKey?: string,
): Promise<T> {
  const headers: Record<string, string> = {};
  if (apiKey) headers["X-Api-Key"] = apiKey;
  const response = await fetch(path, { headers });
  if (!response.ok) {
    throw {
      code: `http_${response.status}`,
      message: response.statusText,
    } satisfies RpcError;
  }
  return (await response.json()) as T;
}
