import { afterEach, describe, expect, it } from "vitest";
import type { WebSocketLike } from "../api/moonraker";
import type { Action } from "../api/targets";
import {
  adoptSerial,
  cancelJob,
  clearRoadrunner,
  closeKconfig,
  connect,
  disconnect,
  fetchTargetDetail,
  invokeAction,
  ignoreCanbus,
  kconfigEnter,
  openKconfig,
  provisionRoadrunner,
  refresh,
  scanBareBoard,
  startAddMcu,
  state,
  updateSettings,
  unignoreCanbus,
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

  it("coalesces a second refresh while the first one is still running", async () => {
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
    const second = refresh();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(state.refreshing).toBe(true);
    const requests = socket.sent.slice(before).map((raw) => JSON.parse(raw));
    const statusRequests = requests.filter(
      (request) => request.params?.method === "fw.status",
    );
    const canRequests = requests.filter(
      (request) => request.params?.method === "fw.canbus.scan",
    );
    expect(statusRequests).toHaveLength(1);
    expect(canRequests).toHaveLength(1);

    socket.message({
      jsonrpc: "2.0",
      id: statusRequests[0].id,
      result: { bus: [] },
    });
    socket.message({
      jsonrpc: "2.0",
      id: canRequests[0].id,
      result: { devices: [{ uuid: "one-scan" }] },
    });
    await Promise.all([first, second]);
    expect(state.refreshing).toBe(false);
    expect(
      (state.canbus as { devices: { uuid: string }[] }).devices[0].uuid,
    ).toBe("one-scan");
  });

  it("clears the shared refresh state when the connection drops", async () => {
    let socket!: FakeWebSocket;
    connect("ws://test/websocket", () => {
      socket = new FakeWebSocket();
      return socket;
    });
    socket.open();
    await drainHandshake(socket);
    state.ping = { capabilities: ["fw.canbus.scan"] };

    void refresh();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(state.refreshing).toBe(true);

    disconnect();
    expect(state.refreshing).toBe(false);
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

  it("ignoreCanbus calls fw.canbus.ignore with the persistent UUID identity", async () => {
    let socket!: FakeWebSocket;
    connect("ws://test/websocket", () => {
      socket = new FakeWebSocket();
      return socket;
    });
    socket.open();
    await drainHandshake(socket);

    const before = socket.sent.length;
    const call = ignoreCanbus("abc123");
    await new Promise((resolve) => setTimeout(resolve, 0));
    const request = JSON.parse(socket.sent[before]);
    expect(request.params.method).toBe("fw.canbus.ignore");
    expect(request.params.arguments).toEqual({ uuid: "abc123" });
    socket.message({ jsonrpc: "2.0", id: request.id, result: {} });
    expect(await call).toBe(true);
  });

  it("unignoreCanbus calls fw.canbus.unignore with the persistent UUID identity", async () => {
    let socket!: FakeWebSocket;
    connect("ws://test/websocket", () => {
      socket = new FakeWebSocket();
      return socket;
    });
    socket.open();
    await drainHandshake(socket);

    const before = socket.sent.length;
    const call = unignoreCanbus("abc123");
    await new Promise((resolve) => setTimeout(resolve, 0));
    const request = JSON.parse(socket.sent[before]);
    expect(request.params.method).toBe("fw.canbus.unignore");
    expect(request.params.arguments).toEqual({ uuid: "abc123" });
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

describe("roadrunner", () => {
  afterEach(() => {
    disconnect();
  });

  it("provisionRoadrunner calls fw.roadrunner.provision with just the serial", async () => {
    let socket!: FakeWebSocket;
    connect("ws://test/websocket", () => {
      socket = new FakeWebSocket();
      return socket;
    });
    socket.open();
    await drainHandshake(socket);

    const before = socket.sent.length;
    const call = provisionRoadrunner("RR-UNPROVISIONED-0123456789ABCDEF");
    await new Promise((resolve) => setTimeout(resolve, 0));
    const request = JSON.parse(socket.sent[before]);
    expect(request.params.method).toBe("fw.roadrunner.provision");
    expect(request.params.arguments).toEqual({
      serial: "RR-UNPROVISIONED-0123456789ABCDEF",
    });
    socket.message({
      jsonrpc: "2.0",
      id: request.id,
      result: {
        serial: "RR-0123456789ABCDEFGHJKMNPQRS",
        prior_serial: "RR-UNPROVISIONED-0123456789ABCDEF",
        state: "provisioned",
      },
    });
    expect(await call).toBe(true);
  });

  it("provisionRoadrunner refreshes fw.status after a confirmed result", async () => {
    let socket!: FakeWebSocket;
    connect("ws://test/websocket", () => {
      socket = new FakeWebSocket();
      return socket;
    });
    socket.open();
    await drainHandshake(socket);

    const before = socket.sent.length;
    const call = provisionRoadrunner("RR-UNPROVISIONED-0123456789ABCDEF");
    await new Promise((resolve) => setTimeout(resolve, 0));
    const request = JSON.parse(socket.sent[before]);
    socket.message({
      jsonrpc: "2.0",
      id: request.id,
      result: {
        serial: "RR-0123456789ABCDEFGHJKMNPQRS",
        prior_serial: "RR-UNPROVISIONED-0123456789ABCDEF",
        state: "provisioned",
      },
    });
    await call;
    await new Promise((resolve) => setTimeout(resolve, 0));

    const refreshed = socket.sent
      .slice(before + 1)
      .map((raw) => JSON.parse(raw))
      .some((msg) => msg.params?.method === "fw.status");
    expect(refreshed).toBe(true);
  });

  it("provisionRoadrunner routes a roadrunner_* refusal into state.error and returns false", async () => {
    let socket!: FakeWebSocket;
    connect("ws://test/websocket", () => {
      socket = new FakeWebSocket();
      return socket;
    });
    socket.open();
    await drainHandshake(socket);

    const call = provisionRoadrunner("RR-UNPROVISIONED-0123456789ABCDEF");
    await new Promise((resolve) => setTimeout(resolve, 0));
    const request = JSON.parse(socket.sent[socket.sent.length - 1]);
    socket.message({
      jsonrpc: "2.0",
      id: request.id,
      error: {
        code: -32000,
        message: "no matching candidate",
        data: {
          code: "roadrunner_no_candidate",
          message: "No matching untracked Roadrunner was found.",
        },
      },
    });

    expect(await call).toBe(false);
    expect(state.error?.code).toBe("roadrunner_no_candidate");
    expect(state.error?.message).toBe(
      "No matching untracked Roadrunner was found.",
    );
  });

  it("clearRoadrunner calls fw.roadrunner.clear with just the serial", async () => {
    let socket!: FakeWebSocket;
    connect("ws://test/websocket", () => {
      socket = new FakeWebSocket();
      return socket;
    });
    socket.open();
    await drainHandshake(socket);

    const before = socket.sent.length;
    const call = clearRoadrunner("RR-0123456789ABCDEFGHJKMNPQRS");
    await new Promise((resolve) => setTimeout(resolve, 0));
    const request = JSON.parse(socket.sent[before]);
    expect(request.params.method).toBe("fw.roadrunner.clear");
    expect(request.params.arguments).toEqual({
      serial: "RR-0123456789ABCDEFGHJKMNPQRS",
    });
    socket.message({
      jsonrpc: "2.0",
      id: request.id,
      result: {
        serial: "RR-UNPROVISIONED-0123456789ABCDEF",
        prior_serial: "RR-0123456789ABCDEFGHJKMNPQRS",
        state: "unprovisioned",
      },
    });
    expect(await call).toBe(true);
  });

  it("clearRoadrunner routes a roadrunner_* refusal into state.error and returns false", async () => {
    let socket!: FakeWebSocket;
    connect("ws://test/websocket", () => {
      socket = new FakeWebSocket();
      return socket;
    });
    socket.open();
    await drainHandshake(socket);

    const call = clearRoadrunner("RR-0123456789ABCDEFGHJKMNPQRS");
    await new Promise((resolve) => setTimeout(resolve, 0));
    const request = JSON.parse(socket.sent[socket.sent.length - 1]);
    socket.message({
      jsonrpc: "2.0",
      id: request.id,
      error: {
        code: -32000,
        message: "already tracked",
        data: {
          code: "roadrunner_tracked",
          message: "This board is tracked by a type; untrack it first.",
        },
      },
    });

    expect(await call).toBe(false);
    expect(state.error?.code).toBe("roadrunner_tracked");
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

describe("job log restoration", () => {
  afterEach(() => {
    disconnect();
    state.job = null;
    state.log = null;
    state.logOmitted = false;
  });

  it("clears a stale displayed job when a fresh connection has none to restore", async () => {
    state.job = { id: "job-stale" } as never;
    state.log = {
      job_id: "job-stale",
      lines: [{ i: 0, s: "stdout", t: "old" }],
    };
    let socket!: FakeWebSocket;
    connect("ws://test/websocket", () => {
      socket = new FakeWebSocket();
      return socket;
    });
    socket.open();
    await drainHandshake(socket);

    expect(state.job).toBeNull();
    expect(state.log).toBeNull();
  });

  it("restores an active job and its retained log from fw.status", async () => {
    let socket!: FakeWebSocket;
    connect("ws://test/websocket", () => {
      socket = new FakeWebSocket();
      return socket;
    });
    socket.open();
    await drainHandshake(socket);
    state.ping = { capabilities: [] };

    const job = {
      id: "job-7",
      kind: "build",
      params: { name: "toolhead" },
      state: "running",
      created: 100,
      started: 101,
      finished: null,
      duration: 2,
      progress: { step: "Compiling", index: 1, total: 2 },
      result: null,
      error: null,
      cancel_requested: false,
      log_next: 2,
      log_dropped: 0,
    };
    const before = socket.sent.length;
    const call = refresh();
    await new Promise((resolve) => setTimeout(resolve, 0));
    const statusRequest = socket.sent
      .slice(before)
      .map((raw) => JSON.parse(raw))
      .find((request) => request.params?.method === "fw.status")!;
    socket.message({
      jsonrpc: "2.0",
      id: statusRequest.id,
      result: { bus: [], job, recent: [] },
    });
    await new Promise((resolve) => setTimeout(resolve, 0));

    const logRequest = socket.sent
      .slice(before)
      .map((raw) => JSON.parse(raw))
      .find((request) => request.params?.method === "fw.job.get");
    expect(logRequest?.params.arguments).toEqual({
      job_id: "job-7",
      log_from: 0,
    });
    socket.message({
      jsonrpc: "2.0",
      id: logRequest.id,
      result: {
        job,
        log: [
          { i: 0, s: "cmd", t: "make" },
          { i: 1, s: "stdout", t: "Compiling" },
        ],
        log_from: 0,
        log_next: 2,
        log_dropped: 0,
      },
    });
    await call;

    expect(state.job?.id).toBe("job-7");
    expect(state.log?.lines.map((line) => line.i)).toEqual([0, 1]);
  });

  it("restores the newest completed job when it finished within 15 minutes", async () => {
    let socket!: FakeWebSocket;
    connect("ws://test/websocket", () => {
      socket = new FakeWebSocket();
      return socket;
    });
    socket.open();
    await drainHandshake(socket);
    state.ping = { capabilities: [] };

    const recentJob = {
      id: "job-6",
      kind: "flash_all",
      params: {},
      state: "succeeded",
      created: Date.now() / 1000 - 120,
      started: Date.now() / 1000 - 110,
      finished: Date.now() / 1000 - 60,
      duration: 50,
      progress: { step: "Complete", index: 2, total: 2 },
      result: {},
      error: null,
      cancel_requested: false,
      log_next: 1,
      log_dropped: 0,
    };
    const before = socket.sent.length;
    const call = refresh();
    await new Promise((resolve) => setTimeout(resolve, 0));
    const statusRequest = socket.sent
      .slice(before)
      .map((raw) => JSON.parse(raw))
      .find((request) => request.params?.method === "fw.status")!;
    socket.message({
      jsonrpc: "2.0",
      id: statusRequest.id,
      result: { bus: [], job: null, recent: [recentJob] },
    });
    await new Promise((resolve) => setTimeout(resolve, 0));

    const logRequest = socket.sent
      .slice(before)
      .map((raw) => JSON.parse(raw))
      .find((request) => request.params?.method === "fw.job.get");
    expect(logRequest?.params.arguments).toEqual({
      job_id: "job-6",
      log_from: 0,
    });
    socket.message({
      jsonrpc: "2.0",
      id: logRequest.id,
      result: {
        job: recentJob,
        log: [{ i: 0, s: "info", t: "Complete" }],
        log_from: 0,
        log_next: 1,
        log_dropped: 0,
      },
    });
    await call;

    expect(state.job?.id).toBe("job-6");
    expect(state.log?.lines[0].t).toBe("Complete");
  });

  it("does not restore a completed job older than 15 minutes", async () => {
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
    const statusRequest = socket.sent
      .slice(before)
      .map((raw) => JSON.parse(raw))
      .find((request) => request.params?.method === "fw.status")!;
    socket.message({
      jsonrpc: "2.0",
      id: statusRequest.id,
      result: {
        bus: [],
        job: null,
        recent: [
          {
            id: "job-old",
            state: "succeeded",
            finished: Date.now() / 1000 - 15 * 60 - 1,
          },
        ],
      },
    });
    await call;

    const methods = socket.sent
      .slice(before)
      .map((raw) => JSON.parse(raw).params?.method);
    expect(methods).not.toContain("fw.job.get");
    expect(state.job).toBeNull();
    expect(state.log).toBeNull();
  });

  it("keeps a live log batch that arrives while retained history is loading", async () => {
    let socket!: FakeWebSocket;
    connect("ws://test/websocket", () => {
      socket = new FakeWebSocket();
      return socket;
    });
    socket.open();
    await drainHandshake(socket);
    state.ping = { capabilities: [] };

    const job = {
      id: "job-race",
      kind: "build",
      state: "running",
      finished: null,
    };
    const before = socket.sent.length;
    const call = refresh();
    await new Promise((resolve) => setTimeout(resolve, 0));
    const statusRequest = socket.sent
      .slice(before)
      .map((raw) => JSON.parse(raw))
      .find((request) => request.params?.method === "fw.status")!;
    socket.message({
      jsonrpc: "2.0",
      id: statusRequest.id,
      result: { bus: [], job, recent: [] },
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    const logRequest = socket.sent
      .slice(before)
      .map((raw) => JSON.parse(raw))
      .find((request) => request.params?.method === "fw.job.get")!;

    socket.message({
      jsonrpc: "2.0",
      method: "notify_agent_event",
      params: [
        {
          agent: "mcu_updater",
          event: "log",
          data: {
            job_id: "job-race",
            seq: 0,
            lines: [{ i: 0, s: "stdout", t: "arrived live" }],
          },
        },
      ],
    });
    socket.message({
      jsonrpc: "2.0",
      id: logRequest.id,
      result: {
        job,
        log: [],
        log_from: 0,
        log_next: 0,
        log_dropped: 0,
      },
    });
    await call;

    expect(state.log?.lines).toEqual([
      { i: 0, s: "stdout", t: "arrived live" },
    ]);
  });

  it("does not replace a live job event with an older status snapshot", async () => {
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
    const statusRequest = socket.sent
      .slice(before)
      .map((raw) => JSON.parse(raw))
      .find((request) => request.params?.method === "fw.status")!;
    socket.message({
      jsonrpc: "2.0",
      method: "notify_agent_event",
      params: [
        {
          agent: "mcu_updater",
          event: "job",
          data: { job: { id: "job-new", state: "running" } },
        },
      ],
    });
    socket.message({
      jsonrpc: "2.0",
      id: statusRequest.id,
      result: {
        bus: [],
        job: null,
        recent: [
          {
            id: "job-old",
            state: "succeeded",
            finished: Date.now() / 1000 - 10,
          },
        ],
      },
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    const logRequest = socket.sent
      .slice(before)
      .map((raw) => JSON.parse(raw))
      .find((request) => request.params?.method === "fw.job.get")!;
    socket.message({
      jsonrpc: "2.0",
      id: logRequest.id,
      result: {
        job: { id: "job-new", state: "running" },
        log: [],
        log_from: 0,
        log_next: 0,
        log_dropped: 0,
      },
    });
    await call;

    expect(state.job?.id).toBe("job-new");
    expect(logRequest.params.arguments.job_id).toBe("job-new");
  });
});
