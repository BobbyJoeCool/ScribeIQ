// Shared helpers for the Vitest suites: a fake fetch router, a fake WebSocket
// that tests push live snapshots through, and ready-made API fixtures.

import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { vi } from "vitest";
import type { Settings, Snapshot, Transcript } from "./api";
import { LiveProvider } from "./live";
import { ToastProvider } from "./toasts";

type Reply = unknown | { status: number; body: unknown };
type Handler = (url: string, init?: RequestInit) => Reply | Promise<Reply>;

/** Route fetch calls by "METHOD /path" (query string ignored). Unrouted calls
 *  fail the test loudly instead of hanging. Returns the spy. */
export function mockFetch(routes: Record<string, Reply | Handler>) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    const key = `${init?.method ?? "GET"} ${url.split("?")[0]}`;
    if (!(key in routes)) throw new Error(`Unexpected request: ${key}`);
    const route = routes[key];
    let reply = typeof route === "function" ? await (route as Handler)(url, init) : route;
    let status = 200;
    if (reply && typeof reply === "object" && "status" in reply && "body" in reply) {
      status = (reply as { status: number }).status;
      reply = (reply as { body: unknown }).body;
    }
    return new Response(JSON.stringify(reply), { status, headers: { "Content-Type": "application/json" } });
  });
}

export const calls = (spy: ReturnType<typeof mockFetch>, method: string, path: string) =>
  spy.mock.calls.filter(([url, init]) => (init?.method ?? "GET") === method && String(url).split("?")[0] === path);

/** Minimal WebSocket stand-in. FakeWebSocket.last is the newest socket. */
export class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  static get last() {
    return FakeWebSocket.instances[FakeWebSocket.instances.length - 1];
  }
  url: string;
  closed = false;
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }
  open() {
    this.onopen?.();
  }
  push(s: Snapshot) {
    this.onmessage?.({ data: JSON.stringify(s) });
  }
  /** The server went away. */
  drop() {
    this.onclose?.();
  }
  close() {
    this.closed = true;
  }
}

export function installFakeWebSocket() {
  FakeWebSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
}

export const settings: Settings = {
  models: ["large-v3", "turbo"],
  default_model: "large-v3",
  my_name: "Me",
  hf_token: true,
  default_session_name: "2026-11-18_0930",
};

const BUTTONS: Record<Snapshot["state"], Snapshot["buttons"]> = {
  Idle: { record: true, pause: false, stop: false, finish: false },
  Recording: { record: false, pause: true, stop: true, finish: false },
  Paused: { record: false, pause: true, stop: true, finish: false },
  Stopped: { record: true, pause: false, stop: false, finish: true },
  Processing: { record: false, pause: false, stop: false, finish: false },
  Done: { record: true, pause: false, stop: false, finish: false },
};

export function snap(state: Snapshot["state"], extra: Partial<Snapshot> = {}): Snapshot {
  return {
    state,
    buttons: BUTTONS[state],
    elapsed: 0,
    levels: { me: 0, them: 0 },
    name: null,
    app: null,
    tracks: null,
    transcript_id: null,
    job: null,
    notice: null,
    ...extra,
  };
}

export const transcript: Transcript = {
  id: "call",
  title: "Weekly sync",
  created: "2026-11-18T09:30:00",
  kind: "call",
  labeled: true,
  duration: 8,
  speakers: ["Me", "Them"],
  segments: [
    { start: 0, end: 2, text: "Hi there.", speaker: "Me" },
    { start: 2.5, end: 4, text: "Hello!", speaker: "Them" },
  ],
};

export function renderWithProviders(ui: ReactNode) {
  return render(
    <ToastProvider>
      <LiveProvider>{ui}</LiveProvider>
    </ToastProvider>,
  );
}
