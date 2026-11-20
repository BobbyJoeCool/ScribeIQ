import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { isBusy, LiveProvider, useLive } from "./live";
import { FakeWebSocket, installFakeWebSocket, mockFetch, snap } from "./test-utils";

function Probe() {
  const { snapshot, connected } = useLive();
  return (
    <p>
      {connected ? "connected" : "offline"} {snapshot?.state ?? "none"}
    </p>
  );
}

beforeEach(() => {
  installFakeWebSocket();
  mockFetch({ "GET /api/session": snap("Idle") });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("LiveProvider", () => {
  it("loads the session first, then follows the WebSocket", async () => {
    render(
      <LiveProvider>
        <Probe />
      </LiveProvider>,
    );
    expect(await screen.findByText("offline Idle")).toBeTruthy();
    expect(FakeWebSocket.last.url).toBe(`ws://${location.host}/api/live`);
    act(() => FakeWebSocket.last.open());
    act(() => FakeWebSocket.last.push(snap("Recording")));
    expect(screen.getByText("connected Recording")).toBeTruthy();
  });

  it("reconnects a second after the connection drops", async () => {
    vi.useFakeTimers();
    render(
      <LiveProvider>
        <Probe />
      </LiveProvider>,
    );
    act(() => FakeWebSocket.last.open());
    act(() => FakeWebSocket.last.drop());
    expect(screen.getByText(/^offline/)).toBeTruthy();
    expect(FakeWebSocket.instances).toHaveLength(1);
    act(() => vi.advanceTimersByTime(1000));
    expect(FakeWebSocket.instances).toHaveLength(2);
    act(() => FakeWebSocket.last.open());
    expect(screen.getByText(/^connected/)).toBeTruthy();
  });

  it("closes the socket and stops reconnecting when unmounted", () => {
    vi.useFakeTimers();
    const { unmount } = render(
      <LiveProvider>
        <Probe />
      </LiveProvider>,
    );
    const ws = FakeWebSocket.last;
    unmount();
    expect(ws.closed).toBe(true);
    ws.drop();
    vi.advanceTimersByTime(5000);
    expect(FakeWebSocket.instances).toHaveLength(1);
  });

  it("still connects when the first session request fails", async () => {
    vi.restoreAllMocks();
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("down"));
    render(
      <LiveProvider>
        <Probe />
      </LiveProvider>,
    );
    act(() => FakeWebSocket.last.push(snap("Paused")));
    expect(screen.getByText("offline Paused")).toBeTruthy();
  });
});

describe("isBusy", () => {
  it("is busy while recording, paused or transcribing", () => {
    expect(isBusy(null)).toBe(false);
    expect(isBusy(snap("Idle"))).toBe(false);
    expect(isBusy(snap("Done"))).toBe(false);
    expect(isBusy(snap("Recording"))).toBe(true);
    expect(isBusy(snap("Paused"))).toBe(true);
    expect(isBusy(snap("Processing"))).toBe(true);
    const job = { id: "j", kind: "file" as const, status: "running" as const, progress: 0, step: "", error: null, transcript_id: null };
    expect(isBusy(snap("Idle", { job }))).toBe(true);
    expect(isBusy(snap("Idle", { job: { ...job, status: "done" } }))).toBe(false);
  });
});
