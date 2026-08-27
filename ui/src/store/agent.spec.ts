import { afterEach, describe, expect, it } from "vitest";
import type { WebSocketLike } from "../api/moonraker";
import type { Action } from "../api/targets";
import {
  cancelJob,
  closeKconfig,
  connect,
  disconnect,
  fetchTargetDetail,
  invokeAction,
  kconfigEnter,
  openKconfig,
  state,
} from "./agent";

class FakeWebSocket implements WebSocketLike {
  readyState = 0;
  sent: string[] = [];
  private listeners: Record<string, ((ev: { data?: string }) => void)[]> = {};

  addEventListener(
    type: string,
    listener: (ev: { data?: string }) => void,
  ): void {
    (this.listeners[type] ??= []).push(listener);
  }

  private fire(type: string, ev: { data?: string } = {}): void {
    for (const listener of this.listeners[type] ?? []) listener(ev);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.readyState = 3;
    this.fire("close");
  }

  open(): void {
    this.readyState = 1;
    this.fire("open");
  }

  message(payload: unknown): void {
    this.fire("message", { data: JSON.stringify(payload) });
  }
}

/** connect() chains identify -> extensions.list -> ping -> status, each a
 * separate awaited round trip. Answer whatever has been sent so far, flush a
 * macrotask, repeat - enough rounds to drain the whole chain regardless of
 * how many awaits sit between one send and the next. */
async function drainHandshake(socket: FakeWebSocket): Promise<void> {
  const answered = new Set<number>();
  for (let round = 0; round < 8; round++) {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (const raw of socket.sent) {
      const msg = JSON.parse(raw);
      if (answered.has(msg.id)) continue;
      answered.add(msg.id);
      if (msg.method === "server.extensions.list") {
        socket.message({
          jsonrpc: "2.0",
          id: msg.id,
          result: { agents: [{ name: "mcu_updater" }] },
        });
      } else {
        socket.message({ jsonrpc: "2.0", id: msg.id, result: {} });
      }
    }
  }
}

describe("fetchTargetDetail", () => {
  afterEach(() => {
    disconnect();
  });

  it("returns null when there is no connection", async () => {
    expect(await fetchTargetDetail("bttebb36", "kconfig_make")).toBeNull();
  });

  it("funnels through server.extensions.request and returns the target", async () => {
    let socket!: FakeWebSocket;
    connect("ws://test/websocket", () => {
      socket = new FakeWebSocket();
      return socket;
    });
    socket.open();
    await drainHandshake(socket);

    const before = socket.sent.length;
    const call = fetchTargetDetail("bttebb36", "kconfig_make");
    await new Promise((resolve) => setTimeout(resolve, 0));
    const request = JSON.parse(socket.sent[before]);
    expect(request.method).toBe("server.extensions.request");
    expect(request.params).toEqual({
      agent: "mcu_updater",
      method: "fw.target.get",
      arguments: { name: "bttebb36", provider: "kconfig_make" },
    });

    socket.message({
      jsonrpc: "2.0",
      id: request.id,
      result: { provider: "kconfig_make", target: { name: "bttebb36" } },
    });
    expect(await call).toEqual({ name: "bttebb36" });
  });

  it("routes a failure into state.error and resolves null", async () => {
    let socket!: FakeWebSocket;
    connect("ws://test/websocket", () => {
      socket = new FakeWebSocket();
      return socket;
    });
    socket.open();
    await drainHandshake(socket);

    const before = socket.sent.length;
    const call = fetchTargetDetail("nope", "kconfig_make");
    await new Promise((resolve) => setTimeout(resolve, 0));
    const request = JSON.parse(socket.sent[before]);

    socket.message({
      jsonrpc: "2.0",
      id: request.id,
      error: {
        code: -32000,
        message: "no such type",
        data: { code: "unknown_target", message: "target not found" },
      },
    });

    expect(await call).toBeNull();
    expect(state.error?.code).toBe("unknown_target");
  });
});

describe("invokeAction", () => {
  afterEach(() => {
    disconnect();
  });

  it("calls the action's own method with its params merged with a choice", async () => {
    let socket!: FakeWebSocket;
    connect("ws://test/websocket", () => {
      socket = new FakeWebSocket();
      return socket;
    });
    socket.open();
    await drainHandshake(socket);

    const action: Action = {
      id: "profile",
      label: "Change profile",
      method: "fw.profile.apply",
      params: { name: "carto_v4", fw: "cartographer" },
      blocked: null,
      choices: {
        method: "fw.profile.list",
        params: { name: "carto_v4" },
        param: "profile",
      },
    };

    const before = socket.sent.length;
    const call = invokeAction(action, { profile: "config.CartoV4USB" });
    await new Promise((resolve) => setTimeout(resolve, 0));
    const request = JSON.parse(socket.sent[before]);
    expect(request.params.method).toBe("fw.profile.apply");
    expect(request.params.arguments).toEqual({
      name: "carto_v4",
      fw: "cartographer",
      profile: "config.CartoV4USB",
    });

    socket.message({ jsonrpc: "2.0", id: request.id, result: {} });
    expect(await call).toBe(true);
  });

  it("routes a refusal into state.error and returns false", async () => {
    let socket!: FakeWebSocket;
    connect("ws://test/websocket", () => {
      socket = new FakeWebSocket();
      return socket;
    });
    socket.open();
    await drainHandshake(socket);

    const action: Action = {
      id: "flash",
      label: "Flash",
      method: "fw.flash_all",
      params: { name: "carto_v4", scope: "stale" },
      blocked: null,
    };

    const before = socket.sent.length;
    const call = invokeAction(action);
    await new Promise((resolve) => setTimeout(resolve, 0));
    const request = JSON.parse(socket.sent[before]);

    socket.message({
      jsonrpc: "2.0",
      id: request.id,
      error: {
        code: -32000,
        message: "busy",
        data: { code: "busy", message: "another job is running" },
      },
    });

    expect(await call).toBe(false);
    expect(state.error?.code).toBe("busy");
  });
});

describe("kconfig", () => {
  afterEach(() => {
    disconnect();
    state.kconfig = null;
  });

  it("opens a session and stores its menu, with search and help cleared", async () => {
    let socket!: FakeWebSocket;
    connect("ws://test/websocket", () => {
      socket = new FakeWebSocket();
      return socket;
    });
    socket.open();
    await drainHandshake(socket);

    const before = socket.sent.length;
    const call = openKconfig("carto_v4", "klipper");
    await new Promise((resolve) => setTimeout(resolve, 0));
    const request = JSON.parse(socket.sent[before]);
    expect(request.params.method).toBe("fw.kconfig.open");
    expect(request.params.arguments).toEqual({
      name: "carto_v4",
      fw: "klipper",
      force: false,
    });

    socket.message({
      jsonrpc: "2.0",
      id: request.id,
      result: {
        session: "sess-1",
        revision: 0,
        type: "carto_v4",
        fw: "klipper",
        dirty: false,
        breadcrumb: [{ id: "root", prompt: "Configuration" }],
        nodes: [],
      },
    });

    expect(await call).toBe(true);
    expect(state.kconfig?.session).toBe("sess-1");
    expect(state.kconfig?.search).toBeNull();
    expect(state.kconfig?.help).toBeNull();
  });

  it("routes a session conflict into state.error without opening", async () => {
    let socket!: FakeWebSocket;
    connect("ws://test/websocket", () => {
      socket = new FakeWebSocket();
      return socket;
    });
    socket.open();
    await drainHandshake(socket);

    const before = socket.sent.length;
    const call = openKconfig("carto_v4", "klipper");
    await new Promise((resolve) => setTimeout(resolve, 0));
    const request = JSON.parse(socket.sent[before]);

    socket.message({
      jsonrpc: "2.0",
      id: request.id,
      error: {
        code: -32000,
        message: "another session has unsaved changes",
        data: {
          code: "kconfig_session_conflict",
          message: "another session has unsaved changes",
          data: { session: "sess-0", type: "carto_v4", fw: "klipper" },
        },
      },
    });

    expect(await call).toBe(false);
    expect(state.kconfig).toBeNull();
    expect(state.error?.code).toBe("kconfig_session_conflict");
  });

  it("does nothing without an open session", async () => {
    expect(await kconfigEnter("id")).toBe(false);
  });

  it("sends the open session's id on every navigation call", async () => {
    let socket!: FakeWebSocket;
    connect("ws://test/websocket", () => {
      socket = new FakeWebSocket();
      return socket;
    });
    socket.open();
    await drainHandshake(socket);

    state.kconfig = {
      session: "sess-1",
      revision: 0,
      type: "carto_v4",
      fw: "klipper",
      dirty: false,
      breadcrumb: [{ id: "root", prompt: "Configuration" }],
      nodes: [],
      search: null,
      help: null,
    };

    const before = socket.sent.length;
    const call = kconfigEnter("menu:board");
    await new Promise((resolve) => setTimeout(resolve, 0));
    const request = JSON.parse(socket.sent[before]);
    expect(request.params.method).toBe("fw.kconfig.enter");
    expect(request.params.arguments).toEqual({
      session: "sess-1",
      id: "menu:board",
    });

    socket.message({
      jsonrpc: "2.0",
      id: request.id,
      result: {
        session: "sess-1",
        revision: 1,
        type: "carto_v4",
        fw: "klipper",
        dirty: false,
        breadcrumb: [
          { id: "root", prompt: "Configuration" },
          { id: "menu:board", prompt: "Board" },
        ],
        nodes: [],
      },
    });

    expect(await call).toBe(true);
    expect(state.kconfig?.revision).toBe(1);
  });

  it("closes a still-open session before opening a second one", async () => {
    let socket!: FakeWebSocket;
    connect("ws://test/websocket", () => {
      socket = new FakeWebSocket();
      return socket;
    });
    socket.open();
    await drainHandshake(socket);

    state.kconfig = {
      session: "sess-old",
      revision: 0,
      type: "carto_v4",
      fw: "klipper",
      dirty: false,
      breadcrumb: [{ id: "root", prompt: "Configuration" }],
      nodes: [],
      search: null,
      help: null,
    };

    const before = socket.sent.length;
    const call = openKconfig("bttebb36", "klipper");
    await new Promise((resolve) => setTimeout(resolve, 0));

    // Two calls went out: the close of the stale session, then the new
    // open - never just the open, which would orphan sess-old on the agent.
    const sentMethods = socket.sent
      .slice(before)
      .map((raw) => JSON.parse(raw).params.method);
    expect(sentMethods).toContain("fw.kconfig.close");
    const closeRequest = socket.sent
      .slice(before)
      .map((raw) => JSON.parse(raw))
      .find((req) => req.params.method === "fw.kconfig.close");
    expect(closeRequest.params.arguments).toEqual({ session: "sess-old" });

    const openRequest = socket.sent
      .slice(before)
      .map((raw) => JSON.parse(raw))
      .find((req) => req.params.method === "fw.kconfig.open");
    socket.message({
      jsonrpc: "2.0",
      id: openRequest.id,
      result: {
        session: "sess-new",
        revision: 0,
        type: "bttebb36",
        fw: "klipper",
        dirty: false,
        breadcrumb: [{ id: "root", prompt: "Configuration" }],
        nodes: [],
      },
    });

    expect(await call).toBe(true);
    expect(state.kconfig?.session).toBe("sess-new");
  });

  it("clears the session locally and fires the close call without waiting", async () => {
    let socket!: FakeWebSocket;
    connect("ws://test/websocket", () => {
      socket = new FakeWebSocket();
      return socket;
    });
    socket.open();
    await drainHandshake(socket);

    state.kconfig = {
      session: "sess-1",
      revision: 0,
      type: "carto_v4",
      fw: "klipper",
      dirty: false,
      breadcrumb: [{ id: "root", prompt: "Configuration" }],
      nodes: [],
      search: null,
      help: null,
    };

    const before = socket.sent.length;
    closeKconfig();
    expect(state.kconfig).toBeNull();
    await new Promise((resolve) => setTimeout(resolve, 0));
    const request = JSON.parse(socket.sent[before]);
    expect(request.params.method).toBe("fw.kconfig.close");
    expect(request.params.arguments).toEqual({ session: "sess-1" });
  });
});

describe("cancelJob", () => {
  afterEach(() => {
    disconnect();
  });

  it("does nothing without a running job", async () => {
    expect(await cancelJob()).toBe(false);
  });
});

describe("log gap-heal", () => {
  afterEach(() => {
    disconnect();
  });

  it("keeps lines already rendered and appends the resync, rather than replacing the log", async () => {
    let socket!: FakeWebSocket;
    connect("ws://test/websocket", () => {
      socket = new FakeWebSocket();
      return socket;
    });
    socket.open();
    await drainHandshake(socket);

    socket.message({
      jsonrpc: "2.0",
      method: "notify_agent_event",
      params: [
        {
          agent: "mcu_updater",
          event: "log",
          data: {
            job_id: "job-1",
            seq: 0,
            lines: [
              { i: 0, s: "stdout", t: "line 0" },
              { i: 1, s: "stdout", t: "line 1" },
            ],
          },
        },
      ],
    });
    expect(state.log?.lines.map((l) => l.i)).toEqual([0, 1]);

    // A gap: the next event's seq (5) does not match the cursor (2), so this
    // fires an fw.job.get resync rather than appending.
    const before = socket.sent.length;
    socket.message({
      jsonrpc: "2.0",
      method: "notify_agent_event",
      params: [
        {
          agent: "mcu_updater",
          event: "log",
          data: {
            job_id: "job-1",
            seq: 5,
            lines: [{ i: 5, s: "stdout", t: "line 5" }],
          },
        },
      ],
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    const request = JSON.parse(socket.sent[before]);
    expect(request.params.method).toBe("fw.job.get");
    expect(request.params.arguments).toEqual({ job_id: "job-1", log_from: 2 });

    socket.message({
      jsonrpc: "2.0",
      id: request.id,
      result: {
        log: [
          { i: 2, s: "stdout", t: "line 2" },
          { i: 3, s: "stdout", t: "line 3" },
        ],
        log_from: 2,
        log_next: 4,
        log_dropped: 0,
      },
    });
    await new Promise((resolve) => setTimeout(resolve, 0));

    // Lines 0-1 survive; the resync's 2-3 are appended. Line 5 from the
    // gap-carrying event itself was never appended - it is what the resync
    // was supposed to fill in, not a line to keep alongside it.
    expect(state.log?.lines.map((l) => l.i)).toEqual([0, 1, 2, 3]);
    expect(state.logOmitted).toBe(false);
  });

  it("flags omitted lines when the ring buffer already evicted what was asked for", async () => {
    let socket!: FakeWebSocket;
    connect("ws://test/websocket", () => {
      socket = new FakeWebSocket();
      return socket;
    });
    socket.open();
    await drainHandshake(socket);

    socket.message({
      jsonrpc: "2.0",
      method: "notify_agent_event",
      params: [
        {
          agent: "mcu_updater",
          event: "log",
          data: {
            job_id: "job-1",
            seq: 50,
            lines: [{ i: 50, s: "stdout", t: "x" }],
          },
        },
      ],
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    const request = JSON.parse(socket.sent[socket.sent.length - 1]);

    socket.message({
      jsonrpc: "2.0",
      id: request.id,
      result: {
        log: [{ i: 60, s: "stdout", t: "line 60" }],
        log_from: 60,
        log_next: 61,
        log_dropped: 40,
      },
    });
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(state.logOmitted).toBe(true);
  });
});
