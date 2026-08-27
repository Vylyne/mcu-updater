import { afterEach, describe, expect, it } from "vitest";
import type { WebSocketLike } from "../api/moonraker";
import { connect, disconnect, fetchTargetDetail, state } from "./agent";

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
