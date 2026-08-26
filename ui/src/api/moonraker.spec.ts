import { afterEach, describe, expect, it, vi } from "vitest";
import { MoonrakerClient, type WebSocketLike } from "./moonraker";

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

function makeClient(): { client: MoonrakerClient; sockets: FakeWebSocket[] } {
  const sockets: FakeWebSocket[] = [];
  const client = new MoonrakerClient("ws://test/websocket", () => {
    const ws = new FakeWebSocket();
    sockets.push(ws);
    return ws;
  });
  return { client, sockets };
}

describe("MoonrakerClient", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("rejects a call made before the socket is open", async () => {
    const { client } = makeClient();
    client.connect();
    await expect(client.call("fw.ping")).rejects.toEqual({
      code: "not_connected",
      message: "Not connected to Moonraker.",
    });
  });

  it("correlates a reply to its request by id, even out of order", async () => {
    const { client, sockets } = makeClient();
    client.connect();
    sockets[0].open();

    const first = client.call("fw.ping");
    const second = client.call("fw.status");
    const [firstSent, secondSent] = sockets[0].sent.map((raw) =>
      JSON.parse(raw),
    );

    // Reply to the second request first.
    sockets[0].message({
      jsonrpc: "2.0",
      id: secondSent.id,
      result: { targets: [] },
    });
    sockets[0].message({
      jsonrpc: "2.0",
      id: firstSent.id,
      result: { api_version: 3 },
    });

    await expect(second).resolves.toEqual({ targets: [] });
    await expect(first).resolves.toEqual({ api_version: 3 });
  });

  it("rejects with the JSON-RPC error object on an error reply", async () => {
    const { client, sockets } = makeClient();
    client.connect();
    sockets[0].open();

    const call = client.call("fw.flash");
    const sent = JSON.parse(sockets[0].sent[0]);
    sockets[0].message({
      jsonrpc: "2.0",
      id: sent.id,
      error: {
        code: -32000,
        message: "refused",
        data: { code: "flashing_disabled" },
      },
    });

    await expect(call).rejects.toEqual({
      code: -32000,
      message: "refused",
      data: { code: "flashing_disabled" },
    });
  });

  it("times out a call the agent never answers, without blocking other pending calls", async () => {
    vi.useFakeTimers();
    const { client, sockets } = makeClient();
    client.connect();
    sockets[0].open();

    const wedged = client.call("fw.status", {}, 15000);
    const wedgedRejection = expect(wedged).rejects.toEqual({
      code: "timeout",
      message: "fw.status did not respond in time.",
    });

    vi.advanceTimersByTime(15000);
    await wedgedRejection;
  });

  it("dispatches a notification to every registered handler", () => {
    const { client, sockets } = makeClient();
    client.connect();
    sockets[0].open();

    const handler = vi.fn();
    client.onNotification(handler);
    sockets[0].message({
      jsonrpc: "2.0",
      method: "notify_agent_event",
      params: [{ event: "state" }],
    });

    expect(handler).toHaveBeenCalledWith("notify_agent_event", [
      { event: "state" },
    ]);
  });

  it("rejects every pending call when the socket closes", async () => {
    const { client, sockets } = makeClient();
    client.connect();
    sockets[0].open();

    const call = client.call("fw.status");
    sockets[0].close();

    await expect(call).rejects.toEqual({
      code: "not_connected",
      message: "Not connected to Moonraker.",
    });
  });
});
