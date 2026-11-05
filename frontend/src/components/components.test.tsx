import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Snapshot, Transcript } from "../api";
import { ToastProvider } from "../toasts";
import RecordControls from "./RecordControls";
import TranscriptView, { toBlocks } from "./TranscriptView";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const snapshot = (state: Snapshot["state"], buttons: Snapshot["buttons"]): Snapshot => ({
  state,
  buttons,
  elapsed: 0,
  levels: { me: 0, them: 0 },
  name: null,
  app: null,
  tracks: null,
  transcript_id: null,
  job: null,
  notice: null,
});

describe("RecordControls", () => {
  const noop = () => {};
  const renderControls = (s: Snapshot, pending = false) =>
    render(
      <RecordControls snapshot={s} pending={pending} onRecord={noop} onPauseResume={noop} onStop={noop} onFinish={noop} />,
    );

  it("enables exactly the buttons the server allows", () => {
    renderControls(snapshot("Recording", { record: false, pause: true, stop: true, finish: false }));
    expect(screen.getByRole("button", { name: /record/i })).toHaveProperty("disabled", true);
    expect(screen.getByRole("button", { name: /pause/i })).toHaveProperty("disabled", false);
    expect(screen.getByRole("button", { name: /stop/i })).toHaveProperty("disabled", false);
    expect(screen.getByRole("button", { name: /finish/i })).toHaveProperty("disabled", true);
  });

  it("shows Resume while paused", () => {
    renderControls(snapshot("Paused", { record: false, pause: true, stop: true, finish: false }));
    expect(screen.getByRole("button", { name: /resume/i })).toBeTruthy();
  });

  it("disables everything while a request is pending", () => {
    renderControls(snapshot("Idle", { record: true, pause: false, stop: false, finish: false }), true);
    for (const b of screen.getAllByRole("button")) expect(b).toHaveProperty("disabled", true);
  });
});

const transcript: Transcript = {
  id: "call",
  title: "Weekly sync",
  labeled: true,
  speakers: ["Bob", "Them"],
  segments: [
    { start: 0, end: 1, text: "Hi.", speaker: "Bob" },
    { start: 1, end: 2, text: "How are you?", speaker: "Bob" },
    { start: 3, end: 4, text: "Good!", speaker: "Them" },
    { start: 5, end: 5, text: "", speaker: "", marker: "recording paused for 10s" },
    { start: 6, end: 7, text: "Back.", speaker: "Them" },
  ],
};

describe("toBlocks", () => {
  it("joins consecutive lines by the same speaker and keeps markers separate", () => {
    const blocks = toBlocks(transcript);
    expect(blocks.map((b) => b.kind)).toEqual(["turn", "turn", "marker", "turn"]);
    expect(blocks[0]).toMatchObject({ speaker: "Bob", text: "Hi. How are you?" });
  });
});

describe("TranscriptView", () => {
  const mockFetch = (handler: (url: string, init?: RequestInit) => unknown) =>
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const body = handler(String(input), init);
      return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    });

  it("renders speakers, timestamps, and pause markers", async () => {
    mockFetch(() => transcript);
    render(
      <ToastProvider>
        <TranscriptView id="call" />
      </ToastProvider>,
    );
    expect(await screen.findByText("Hi. How are you?")).toBeTruthy();
    expect(screen.getByText("recording paused for 10s")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Them" })).toHaveLength(2);
    expect(screen.getByText("00:00:03")).toBeTruthy();
  });

  it("deletes only after confirming", async () => {
    const fetchSpy = mockFetch((_url, init) => (init?.method === "DELETE" ? { deleted: "call" } : transcript));
    const onDelete = vi.fn();
    render(
      <ToastProvider>
        <TranscriptView id="call" onDelete={onDelete} />
      </ToastProvider>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    expect(fetchSpy.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Move to Trash" }));
    await waitFor(() => expect(onDelete).toHaveBeenCalledWith("call"));
    const del = fetchSpy.mock.calls.find(([, init]) => init?.method === "DELETE")!;
    expect(String(del[0])).toBe("/api/transcripts/call");
  });

  it("has no Delete button unless deleting is allowed", async () => {
    mockFetch(() => transcript);
    render(
      <ToastProvider>
        <TranscriptView id="call" />
      </ToastProvider>,
    );
    await screen.findByText("Hi. How are you?");
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
  });

  it("renames a speaker everywhere with one click", async () => {
    const renamed = {
      ...transcript,
      speakers: ["Bob", "Dave"],
      segments: transcript.segments.map((s) => (s.speaker === "Them" ? { ...s, speaker: "Dave" } : s)),
    };
    const fetchSpy = mockFetch((_url, init) => (init?.method === "PATCH" ? renamed : transcript));
    render(
      <ToastProvider>
        <TranscriptView id="call" />
      </ToastProvider>,
    );
    fireEvent.click((await screen.findAllByRole("button", { name: "Them" }))[0]);
    const input = screen.getByLabelText("New name for Them");
    fireEvent.change(input, { target: { value: "Dave" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => expect(screen.getAllByRole("button", { name: "Dave" })).toHaveLength(2));
    const patch = fetchSpy.mock.calls.find(([, init]) => init?.method === "PATCH")!;
    expect(String(patch[0])).toBe("/api/transcripts/call/speakers");
    expect(JSON.parse(String(patch[1]!.body))).toEqual({ Them: "Dave" });
  });
});
