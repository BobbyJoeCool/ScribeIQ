import { act, cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Job } from "../api";
import {
  calls,
  FakeWebSocket,
  installFakeWebSocket,
  mockFetch,
  renderWithProviders,
  settings,
  snap,
  transcript,
} from "../test-utils";
import RecordTab from "./RecordTab";

const APPS = {
  apps: [
    { id: "system", name: "All system audio" },
    { id: "us.zoom.xos", name: "zoom.us" },
    { id: "com.apple.Safari", name: "Safari" },
  ],
  default: "us.zoom.xos",
};

const job = (extra: Partial<Job> = {}): Job => ({
  id: "j1",
  kind: "call",
  status: "running",
  progress: 0.5,
  step: "Transcribing...",
  error: null,
  transcript_id: null,
  ...extra,
});

function routes(extra: Record<string, unknown> = {}) {
  return mockFetch({
    "GET /api/session": snap("Idle"),
    "GET /api/apps": APPS,
    "GET /api/config": { ...settings, default_session_name: "2026-11-18_1000" },
    "POST /api/session/record": snap("Recording", { name: "2026-11-18_0930", app: "us.zoom.xos" }),
    "POST /api/session/pause": snap("Paused"),
    "POST /api/session/resume": snap("Recording"),
    "POST /api/session/stop": snap("Stopped", { tracks: ["/r/call_me.wav", "/r/call_them.wav"] }),
    "POST /api/session/finish": job(),
    "GET /api/transcripts/call": transcript,
    ...extra,
  });
}

const button = (name: RegExp) => screen.getByRole("button", { name });
const push = (s: ReturnType<typeof snap>) => act(() => FakeWebSocket.last.push(s));

beforeEach(installFakeWebSocket);
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("RecordTab", () => {
  it("lists running apps with the server's default chosen", async () => {
    routes();
    renderWithProviders(<RecordTab settings={settings} />);
    const select = (await screen.findByDisplayValue("zoom.us")) as HTMLSelectElement;
    expect([...select.options].map((o) => o.textContent)).toEqual(["All system audio", "zoom.us", "Safari"]);
    expect(screen.getByDisplayValue("2026-11-18_0930")).toBeTruthy();
  });

  it("records from the chosen app or system audio", async () => {
    const spy = routes();
    renderWithProviders(<RecordTab settings={settings} />);
    fireEvent.change(await screen.findByDisplayValue("zoom.us"), { target: { value: "system" } });
    fireEvent.change(screen.getByDisplayValue("2026-11-18_0930"), { target: { value: "Weekly sync" } });
    fireEvent.click(button(/record/i));
    await waitFor(() => expect(calls(spy, "POST", "/api/session/record")).toHaveLength(1));
    const body = JSON.parse(String(calls(spy, "POST", "/api/session/record")[0][1]!.body));
    expect(body).toEqual({ name: "Weekly sync", app: "system" });
    expect(await screen.findByText(/Recording\. Wear headphones/)).toBeTruthy();
    expect(screen.getByDisplayValue("All system audio")).toHaveProperty("disabled", true);
  });

  it("pauses, resumes, stops and finishes", async () => {
    const spy = routes();
    renderWithProviders(<RecordTab settings={settings} />);
    await screen.findByDisplayValue("zoom.us");
    fireEvent.click(button(/record/i));
    fireEvent.click(await screen.findByRole("button", { name: /pause/i, hidden: false }));
    await waitFor(() => expect(screen.getByText("Paused")).toBeTruthy());
    fireEvent.click(button(/resume/i));
    await waitFor(() => expect(button(/pause/i)).toBeTruthy());
    fireEvent.click(button(/stop/i));
    expect(await screen.findByText(/Saved call_me\.wav and call_them\.wav/)).toBeTruthy();

    fireEvent.change(screen.getByDisplayValue("Me"), { target: { value: "Bob" } });
    fireEvent.change(screen.getByDisplayValue("1"), { target: { value: "2" } });
    fireEvent.click(button(/finish/i));
    await waitFor(() => expect(calls(spy, "POST", "/api/session/finish")).toHaveLength(1));
    const body = JSON.parse(String(calls(spy, "POST", "/api/session/finish")[0][1]!.body));
    expect(body).toEqual({ my_name: "Bob", their_count: 2, model: "large-v3" });
    for (const path of ["/api/session/pause", "/api/session/resume", "/api/session/stop"]) {
      expect(calls(spy, "POST", path)).toHaveLength(1);
    }
  });

  it("shows the timer and both level meters from the live feed", async () => {
    routes();
    renderWithProviders(<RecordTab settings={settings} />);
    await screen.findByDisplayValue("zoom.us");
    push(snap("Recording", { elapsed: 65, levels: { me: 0.2, them: 0.5 } }));
    expect(screen.getByLabelText("Elapsed time").textContent).toBe("01:05");
    expect(screen.getByRole("meter", { name: "You level" }).getAttribute("aria-valuenow")).toBe("30");
    expect(screen.getByRole("meter", { name: "Them level" }).getAttribute("aria-valuenow")).toBe("75");
  });

  it("shows progress while processing, then the transcript", async () => {
    const spy = routes();
    renderWithProviders(<RecordTab settings={settings} />);
    await screen.findByDisplayValue("zoom.us");
    push(snap("Processing", { job: job() }));
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("50");
    expect(screen.getByDisplayValue("Me")).toHaveProperty("disabled", true);
    push(snap("Done", { transcript_id: "call", job: job({ status: "done", progress: 1, transcript_id: "call" }) }));
    expect(await screen.findByText("Hi there.")).toBeTruthy();
    expect(screen.queryByRole("progressbar")).toBeNull();
    // A finished session's name is used up: a fresh one is offered.
    expect(await screen.findByDisplayValue("2026-11-18_1000")).toBeTruthy();
    expect(calls(spy, "GET", "/api/config").length).toBeGreaterThan(0);
  });

  it("shows a recorder error and stays ready to retry", async () => {
    routes({
      "POST /api/session/record": { status: 400, body: { detail: "Screen Recording permission is needed" } },
    });
    renderWithProviders(<RecordTab settings={settings} />);
    await screen.findByDisplayValue("zoom.us");
    fireEvent.click(button(/record/i));
    expect((await screen.findByRole("alert")).textContent).toContain("Screen Recording permission is needed");
    expect(button(/record/i)).toHaveProperty("disabled", false);
  });

  it("reports a failed transcription once", async () => {
    routes();
    renderWithProviders(<RecordTab settings={settings} />);
    await screen.findByDisplayValue("zoom.us");
    const failed = job({ status: "failed", error: "Transcription failed: model exploded" });
    push(snap("Stopped", { job: failed }));
    push(snap("Stopped", { job: failed }));
    expect(screen.getAllByText("Transcription failed: model exploded")).toHaveLength(1);
    expect(button(/finish/i)).toHaveProperty("disabled", false);
  });

  it("warns when more than one person is on the other end without HF_TOKEN", async () => {
    routes();
    renderWithProviders(<RecordTab settings={{ ...settings, hf_token: false }} />);
    fireEvent.change(await screen.findByDisplayValue("1"), { target: { value: "3" } });
    expect(screen.getByText("More than 1 needs HF_TOKEN in .env (see README).")).toBeTruthy();
  });

  it("refreshes the app list and keeps the current choice", async () => {
    const spy = routes();
    renderWithProviders(<RecordTab settings={settings} />);
    fireEvent.change(await screen.findByDisplayValue("zoom.us"), { target: { value: "com.apple.Safari" } });
    fireEvent.click(screen.getByTitle("Refresh running apps"));
    await waitFor(() => expect(calls(spy, "GET", "/api/apps")).toHaveLength(2));
    expect(screen.getByDisplayValue("Safari")).toBeTruthy();
  });
});
