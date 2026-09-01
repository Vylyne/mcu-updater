import { afterEach, describe, expect, it } from "vitest";
import type { WebSocketLike } from "../api/moonraker";
import type { Action } from "../api/targets";
import {
  adoptSerial,
  cancelJob,
  closeKconfig,
  connect,
  disconnect,
  fetchTargetDetail,
  invokeAction,
  kconfigEnter,
  openKconfig,
  refresh,
  scanBareBoard,
  startAddMcu,
  state,
  updateSettings,
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

describe("refresh discovery", () => {
  afterEach(() => {
    disconnect();
    state.bus = [];
    state.canbus = null;
    state.canbusError = null;
  });

  it("updates USB status without waiting for the CAN scan", async () => {
    let socket!: FakeWebSocket;
    connect("ws://test/websocket", () => {
      socket = new FakeWebSocket();
      return socket;
    });
    socket.open();
    await drainHandshake(socket);

    const before = socket.sent.length;
    state.canbus = null;
    state.ping = { capabilities: ["fw.canbus.scan"] };
    const call = refresh();
    await new Promise((resolve) => setTimeout(resolve, 0));
    const requests = socket.sent.slice(before).map((raw) => JSON.parse(raw));
    const statusRequest = requests.find(
      (request) => request.params?.method === "fw.status",
    );
    const canRequest = requests.find(
      (request) => request.params?.method === "fw.canbus.scan",
    );
    expect(statusRequest).toBeDefined();
    expect(canRequest).toBeDefined();

    socket.message({
      jsonrpc: "2.0",
      id: statusRequest.id,
      result: { bus: [{ serial: "usb-board", tracked_by: null }] },
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(state.bus).toEqual([{ serial: "usb-board", tracked_by: null }]);
    expect(state.canbus).toBeNull();

    socket.message({
      jsonrpc: "2.0",
      id: canRequest.id,
      result: {
        interfaces: [],
        devices: [],
        failures: [],
        count: 0,
        message: null,
      },
    });
    await call;
  });

  it("does not let an older CAN scan overwrite a newer refresh", async () => {
    let socket!: FakeWebSocket;
    connect("ws://test/websocket", () => {
      socket = new FakeWebSocket();
      return socket;
    });
    socket.open();
    await drainHandshake(socket);

    const before = socket.sent.length;
    state.ping = { capabilities: ["fw.canbus.scan"] };
    const first = refresh();
    await new Promise((resolve) => setTimeout(resolve, 0));
    const firstCan = socket.sent
      .slice(before)
      .map((raw) => JSON.parse(raw))
      .find((request) => request.params?.method === "fw.canbus.scan")!;
    const second = refresh();
    await new Promise((resolve) => setTimeout(resolve, 0));
    const canRequests = socket.sent
      .slice(before)
      .map((raw) => JSON.parse(raw))
      .filter((request) => request.params?.method === "fw.canbus.scan");
    const secondCan = canRequests.at(-1)!;
    for (const request of socket.sent
      .slice(before)
      .map((raw) => JSON.parse(raw))
      .filter((request) => request.params?.method === "fw.status")) {
      socket.message({ jsonrpc: "2.0", id: request.id, result: { bus: [] } });
    }
    socket.message({
      jsonrpc: "2.0",
      id: secondCan.id,
      result: { devices: [{ uuid: "new" }] },
    });
    socket.message({
      jsonrpc: "2.0",
      id: firstCan.id,
      result: { devices: [{ uuid: "old" }] },
    });
    await Promise.all([first, second]);
    expect(
      (state.canbus as { devices: { uuid: string }[] }).devices[0].uuid,
    ).toBe("new");
  });

  it("does not call CAN scan when the agent lacks that capability", async () => {
    let socket!: FakeWebSocket;
    connect("ws://test/websocket", () => {
      socket = new FakeWebSocket();
      return socket;
    });
    socket.open();
    await drainHandshake(socket);
    state.ping = { capabilities: [] };
    const before = socket.sent.length;
    const call = refresh();
    await new Promise((resolve) => setTimeout(resolve, 0));
    const requests = socket.sent.slice(before).map((raw) => JSON.parse(raw));
    const statusRequest = requests.find(
      (request) => request.params?.method === "fw.status",
    )!;
    expect(
      requests.some((request) => request.params?.method === "fw.canbus.scan"),
    ).toBe(false);
    socket.message({
      jsonrpc: "2.0",
      id: statusRequest.id,
      result: { bus: [] },
    });
    await call;
  });

  it("clears stale CAN results when the current scan fails", async () => {
    let socket!: FakeWebSocket;
    connect("ws://test/websocket", () => {
      socket = new FakeWebSocket();
      return socket;
    });
    socket.open();
    await drainHandshake(socket);
    state.ping = { capabilities: ["fw.canbus.scan"] };
    state.canbus = {
      interfaces: [],
      devices: [],
      failures: [],
      count: 0,
      message: null,
    };
    const before = socket.sent.length;
    const call = refresh();
    await new Promise((resolve) => setTimeout(resolve, 0));
    const requests = socket.sent.slice(before).map((raw) => JSON.parse(raw));
    const statusRequest = requests.find(
      (request) => request.params?.method === "fw.status",
    )!;
    const canRequest = requests.find(
      (request) => request.params?.method === "fw.canbus.scan",
    )!;
    socket.message({
      jsonrpc: "2.0",
      id: statusRequest.id,
      result: { bus: [{ serial: "usb" }] },
    });
    socket.message({
      jsonrpc: "2.0",
      id: canRequest.id,
      error: { code: -1, message: "CAN failed" },
    });
    await call;
    expect(state.bus).toEqual([{ serial: "usb" }]);
    expect(state.canbus).toBeNull();
    expect(state.canbusError).not.toBeNull();
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

describe("Phase 8: settings, bus adoption, add_mcu", () => {
  afterEach(() => {
    disconnect();
    state.status = null;
  });

  it("updateSettings replaces state.status.settings from the reply", async () => {
    let socket!: FakeWebSocket;
    connect("ws://test/websocket", () => {
      socket = new FakeWebSocket();
      return socket;
    });
    socket.open();
    await drainHandshake(socket);
    state.status = { settings: { make_jobs: 0 } };

    const before = socket.sent.length;
    const call = updateSettings({ make_jobs: 4 });
    await new Promise((resolve) => setTimeout(resolve, 0));
    const request = JSON.parse(socket.sent[before]);
    expect(request.params.method).toBe("fw.settings.set");
    expect(request.params.arguments).toEqual({ settings: { make_jobs: 4 } });

    socket.message({
      jsonrpc: "2.0",
      id: request.id,
      result: { settings: { make_jobs: 4 }, changed: ["make_jobs"] },
    });
    expect(await call).toEqual({ ok: true, changed: ["make_jobs"] });
    expect(state.status.settings).toEqual({ make_jobs: 4 });
  });

  it("updateSettings routes a refusal into state.error", async () => {
    let socket!: FakeWebSocket;
    connect("ws://test/websocket", () => {
      socket = new FakeWebSocket();
      return socket;
    });
    socket.open();
    await drainHandshake(socket);

    const before = socket.sent.length;
    const call = updateSettings({ stop_services: ["klipper"] });
    await new Promise((resolve) => setTimeout(resolve, 0));
    const request = JSON.parse(socket.sent[before]);
    socket.message({
      jsonrpc: "2.0",
      id: request.id,
      error: {
        code: -32000,
        message: "cannot set stop_services from here",
        data: { code: "setting_not_settable", message: "not settable" },
      },
    });
    expect(await call).toEqual({ ok: false, changed: [] });
    expect(state.error?.code).toBe("setting_not_settable");
  });

  it("adoptSerial calls fw.serial.add with the type and serial", async () => {
    let socket!: FakeWebSocket;
    connect("ws://test/websocket", () => {
      socket = new FakeWebSocket();
      return socket;
    });
    socket.open();
    await drainHandshake(socket);

    const before = socket.sent.length;
    const call = adoptSerial("bttebb36", "1100...-if00");
    await new Promise((resolve) => setTimeout(resolve, 0));
    const request = JSON.parse(socket.sent[before]);
    expect(request.params.method).toBe("fw.serial.add");
    expect(request.params.arguments).toEqual({
      name: "bttebb36",
      serial: "1100...-if00",
    });
    socket.message({ jsonrpc: "2.0", id: request.id, result: {} });
    expect(await call).toBe(true);
  });

  it("scanBareBoard picks fw.dfu.scan or fw.bootsel.scan by mechanism", async () => {
    let socket!: FakeWebSocket;
    connect("ws://test/websocket", () => {
      socket = new FakeWebSocket();
      return socket;
    });
    socket.open();
    await drainHandshake(socket);

    const before = socket.sent.length;
    const call = scanBareBoard("bootsel");
    await new Promise((resolve) => setTimeout(resolve, 0));
    const request = JSON.parse(socket.sent[before]);
    expect(request.params.method).toBe("fw.bootsel.scan");
    socket.message({
      jsonrpc: "2.0",
      id: request.id,
      result: { ready: true, reason: null, devices: [] },
    });
    expect(await call).toEqual({ ready: true, reason: null, devices: [] });
  });

  it("startAddMcu passes dfu_serial only when given one", async () => {
    let socket!: FakeWebSocket;
    connect("ws://test/websocket", () => {
      socket = new FakeWebSocket();
      return socket;
    });
    socket.open();
    await drainHandshake(socket);

    const before = socket.sent.length;
    const call = startAddMcu("bttebb36");
    await new Promise((resolve) => setTimeout(resolve, 0));
    const request = JSON.parse(socket.sent[before]);
    expect(request.params.method).toBe("fw.add_mcu.start");
    expect(request.params.arguments).toEqual({ name: "bttebb36" });
    socket.message({ jsonrpc: "2.0", id: request.id, result: {} });
    expect(await call).toBe(true);
  });

  it("seeds state.bus from fw.status on connect, not just from a later bus event", async () => {
    let socket!: FakeWebSocket;
    connect("ws://test/websocket", () => {
      socket = new FakeWebSocket();
      return socket;
    });
    socket.open();

    const answered = new Set<number>();
    let sawStatus = false;
    for (let round = 0; round < 8 && !sawStatus; round++) {
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
        } else if (msg.params?.method === "fw.status") {
          socket.message({
            jsonrpc: "2.0",
            id: msg.id,
            result: {
              bus: [
                { fw: "Klipper", serial: "1100...-if00", tracked_by: null },
              ],
            },
          });
          sawStatus = true;
        } else {
          socket.message({ jsonrpc: "2.0", id: msg.id, result: {} });
        }
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(state.bus).toEqual([
      { fw: "Klipper", serial: "1100...-if00", tracked_by: null },
    ]);
  });

  it("startAddMcu includes dfu_serial when one was chosen", async () => {
    let socket!: FakeWebSocket;
    connect("ws://test/websocket", () => {
      socket = new FakeWebSocket();
      return socket;
    });
    socket.open();
    await drainHandshake(socket);

    const before = socket.sent.length;
    const call = startAddMcu("bttebb36", "3941335F3434");
    await new Promise((resolve) => setTimeout(resolve, 0));
    const request = JSON.parse(socket.sent[before]);
    expect(request.params.arguments).toEqual({
      name: "bttebb36",
      dfu_serial: "3941335F3434",
    });
    socket.message({ jsonrpc: "2.0", id: request.id, result: {} });
    expect(await call).toBe(true);
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
