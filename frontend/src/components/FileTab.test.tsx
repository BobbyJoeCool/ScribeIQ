import { act, cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Job } from "../api";
import { calls, FakeWebSocket, installFakeWebSocket, mockFetch, renderWithProviders, settings, snap } from "../test-utils";
import FileTab from "./FileTab";

const job = (extra: Partial<Job> = {}): Job => ({
  id: "f1",
  kind: "file",
  status: "running",
  progress: 0.15,
  step: "Transcribing with large-v3...",
  error: null,
  transcript_id: null,
  ...extra,
});

const talk = {
  id: "talk",
  title: "talk",
  labeled: false,
  speakers: [],
  segments: [{ start: 0, end: 3, text: "From talk.m4a." }],
};

function routes(extra: Record<string, unknown> = {}) {
  return mockFetch({
    "GET /api/session": snap("Idle"),
    "POST /api/files/transcribe": { status: 202, body: job() },
    "GET /api/transcripts/talk": talk,
    ...extra,
  });
}

const fileInput = () => document.querySelector('input[type="file"]') as HTMLInputElement;
const choose = (name: string) =>
  fireEvent.change(fileInput(), { target: { files: [new File(["audio"], name, { type: "audio/mp4" })] } });
const sentForm = (spy: ReturnType<typeof mockFetch>) =>
  calls(spy, "POST", "/api/files/transcribe")[0][1]!.body as FormData;
const push = (s: ReturnType<typeof snap>) => act(() => FakeWebSocket.last.push(s));

beforeEach(installFakeWebSocket);
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("FileTab", () => {
  it("asks for a file or link when there's neither", async () => {
    const spy = routes();
    renderWithProviders(<FileTab settings={settings} />);
    fireEvent.click(screen.getByRole("button", { name: "Transcribe" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Choose an audio file or paste an audio URL first.");
    expect(calls(spy, "POST", "/api/files/transcribe")).toHaveLength(0);
  });

  it("uploads a picked file, names it after the file, and shows progress then the transcript", async () => {
    const spy = routes();
    renderWithProviders(<FileTab settings={settings} />);
    choose("talk.m4a");
    expect(screen.getByText("talk.m4a")).toBeTruthy();
    expect(screen.getByDisplayValue("talk")).toBeTruthy();
    fireEvent.click(screen.getByLabelText(/Identify speakers/));
    fireEvent.click(screen.getByRole("button", { name: "Transcribe" }));
    await waitFor(() => expect(calls(spy, "POST", "/api/files/transcribe")).toHaveLength(1));
    const form = sentForm(spy);
    expect((form.get("file") as File).name).toBe("talk.m4a");
    expect(form.get("name")).toBe("talk");
    expect(form.get("identify_speakers")).toBe("false");
    expect(form.get("num_speakers")).toBeNull();
    expect(form.get("model")).toBe("large-v3");

    await waitFor(() => expect(screen.getByRole("button", { name: "Transcribe" })).toHaveProperty("disabled", true));
    push(snap("Idle", { job: job() }));
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("15");
    push(snap("Idle", { job: job({ status: "done", progress: 1, transcript_id: "talk" }) }));
    expect(await screen.findByText("From talk.m4a.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Transcribe" })).toHaveProperty("disabled", false);
  });

  it("sends a pasted link instead of the file, with speakers and a typed name", async () => {
    const spy = routes();
    renderWithProviders(<FileTab settings={settings} />);
    choose("talk.m4a");
    fireEvent.change(screen.getByPlaceholderText(/example\.com/), { target: { value: " https://x.com/My%20Talk.mp3 " } });
    expect(screen.getByDisplayValue("My Talk")).toBeTruthy();
    fireEvent.change(screen.getByDisplayValue("My Talk"), { target: { value: "Episode 1" } });
    fireEvent.change(screen.getByDisplayValue("2"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Transcribe" }));
    await waitFor(() => expect(calls(spy, "POST", "/api/files/transcribe")).toHaveLength(1));
    const form = sentForm(spy);
    expect(form.get("url")).toBe("https://x.com/My%20Talk.mp3");
    expect(form.get("file")).toBeNull();
    expect(form.get("name")).toBe("Episode 1");
    expect(form.get("identify_speakers")).toBe("true");
    expect(form.get("num_speakers")).toBe("3");
  });

  it("shows the server's rejection of an unsupported link", async () => {
    routes({
      "POST /api/files/transcribe": { status: 400, body: { detail: "The URL must start with http:// or https://." } },
    });
    renderWithProviders(<FileTab settings={settings} />);
    fireEvent.change(screen.getByPlaceholderText(/example\.com/), { target: { value: "ftp://x.com/a.mp3" } });
    fireEvent.click(screen.getByRole("button", { name: "Transcribe" }));
    expect((await screen.findByRole("alert")).textContent).toContain("The URL must start with http:// or https://.");
  });

  it("reports a failed job and lets the user try again", async () => {
    routes();
    renderWithProviders(<FileTab settings={settings} />);
    choose("bad.m4a");
    fireEvent.click(screen.getByRole("button", { name: "Transcribe" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Transcribe" })).toHaveProperty("disabled", true));
    push(snap("Idle", { job: job({ status: "failed", error: "Couldn't read bad.m4a as audio (Invalid data)." }) }));
    expect((await screen.findByRole("alert")).textContent).toContain("Couldn't read bad.m4a as audio");
    expect(screen.getByRole("button", { name: "Transcribe" })).toHaveProperty("disabled", false);
  });

  it("waits while another transcription is running", () => {
    routes();
    renderWithProviders(<FileTab settings={settings} />);
    push(snap("Processing", { job: job({ id: "other", kind: "call" }) }));
    expect(screen.getByText("Another transcription is running.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Transcribe" })).toHaveProperty("disabled", true);
  });

  it("warns that identifying speakers needs HF_TOKEN", () => {
    routes();
    renderWithProviders(<FileTab settings={{ ...settings, hf_token: false }} />);
    expect(screen.getByText("Needs HF_TOKEN in .env (see README).")).toBeTruthy();
  });
});
