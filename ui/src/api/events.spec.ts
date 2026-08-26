import { describe, expect, it, vi } from "vitest";
import {
  parseAgentEventParams,
  routeAgentEvent,
  type AgentEventHandlers,
  type LogCursor,
} from "./events";

function handlers(): AgentEventHandlers {
  return {
    onState: vi.fn(),
    onBus: vi.fn(),
    onJob: vi.fn(),
    onLog: vi.fn(),
    onConnected: vi.fn(),
    onDisconnected: vi.fn(),
  };
}

describe("parseAgentEventParams", () => {
  it("unwraps the single-element params list", () => {
    const params = [
      { agent: "mcu_updater", event: "state", data: { targets: [] } },
    ];
    expect(parseAgentEventParams(params)).toEqual(params[0]);
  });

  it("rejects a payload with no event field", () => {
    expect(parseAgentEventParams([{ agent: "mcu_updater" }])).toBeNull();
    expect(parseAgentEventParams(null)).toBeNull();
    expect(parseAgentEventParams(undefined)).toBeNull();
  });
});

describe("routeAgentEvent", () => {
  it("routes state with the full payload", () => {
    const h = handlers();
    routeAgentEvent(
      { agent: "mcu_updater", event: "state", data: { read_only: true } },
      { current: 0 },
      h,
    );
    expect(h.onState).toHaveBeenCalledWith({ read_only: true });
  });

  it("unwraps bus.devices, defaulting to an empty list when data is omitted", () => {
    const h = handlers();
    routeAgentEvent({ agent: "mcu_updater", event: "bus" }, { current: 0 }, h);
    expect(h.onBus).toHaveBeenCalledWith([]);
  });

  it("unwraps job.job", () => {
    const h = handlers();
    routeAgentEvent(
      { agent: "mcu_updater", event: "job", data: { job: { id: "job-1" } } },
      { current: 0 },
      h,
    );
    expect(h.onJob).toHaveBeenCalledWith({ id: "job-1" });
  });

  it("routes connected/disconnected, carrying the identify payload on connect", () => {
    const h = handlers();
    routeAgentEvent(
      {
        agent: "mcu_updater",
        event: "connected",
        data: { client_name: "mcu_updater" },
      },
      { current: 0 },
      h,
    );
    expect(h.onConnected).toHaveBeenCalledWith({ client_name: "mcu_updater" });
    routeAgentEvent(
      { agent: "mcu_updater", event: "disconnected" },
      { current: 0 },
      h,
    );
    expect(h.onDisconnected).toHaveBeenCalled();
  });

  it("ignores an event this build does not know about", () => {
    const h = handlers();
    expect(() =>
      routeAgentEvent(
        { agent: "mcu_updater", event: "future_event", data: {} },
        { current: 0 },
        h,
      ),
    ).not.toThrow();
    expect(h.onState).not.toHaveBeenCalled();
  });

  describe("log gap detection", () => {
    it("is not a gap when seq matches the cursor, and advances it past the batch", () => {
      const h = handlers();
      const cursor: LogCursor = { current: 120 };
      const batch = {
        job_id: "job-7",
        seq: 120,
        lines: [
          { i: 120, s: "stdout", t: "a" },
          { i: 121, s: "stdout", t: "b" },
        ],
      };
      routeAgentEvent(
        { agent: "mcu_updater", event: "log", data: batch },
        cursor,
        h,
      );
      expect(h.onLog).toHaveBeenCalledWith(batch, false);
      expect(cursor.current).toBe(122);
    });

    it("is a gap when seq does not match the cursor, e.g. after a dropped frame", () => {
      const h = handlers();
      const cursor: LogCursor = { current: 50 };
      const batch = {
        job_id: "job-7",
        seq: 120,
        lines: [{ i: 120, s: "stdout", t: "a" }],
      };
      routeAgentEvent(
        { agent: "mcu_updater", event: "log", data: batch },
        cursor,
        h,
      );
      expect(h.onLog).toHaveBeenCalledWith(batch, true);
      // Still advances - a resync should ask from the newest point known, not the stale one.
      expect(cursor.current).toBe(121);
    });
  });
});
