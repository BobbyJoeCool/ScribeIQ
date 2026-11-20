import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { TranscriptSummary } from "../api";
import { calls, mockFetch, transcript } from "../test-utils";
import { ToastProvider } from "../toasts";
import HistoryTab from "./HistoryTab";

const items: TranscriptSummary[] = [
  { id: "call", title: "Weekly sync", created: "2026-11-18T09:30:00", kind: "call", duration: 600, speakers: ["Me", "Them"], legacy: false },
  { id: "talk", title: "Conference talk", created: "2026-11-17T12:00:00", kind: "file", duration: 3900, speakers: [], legacy: false },
  { id: "old", title: "old notes", created: "2026-01-01T08:00:00", kind: null, duration: null, speakers: [], legacy: true },
];

const renderHistory = () =>
  render(
    <ToastProvider>
      <HistoryTab />
    </ToastProvider>,
  );

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("HistoryTab", () => {
  it("lists transcripts in the server's newest-first order with details", async () => {
    mockFetch({ "GET /api/transcripts": items });
    renderHistory();
    const list = await screen.findByRole("list", { name: "Transcripts" });
    await within(list).findByText("Weekly sync");
    const titles = within(list).getAllByRole("button").map((b) => b.querySelector("span")!.textContent);
    expect(titles).toEqual(["Weekly sync", "Conference talk", "old notes"]);
    expect(within(list).getByText(/10 min · Call/)).toBeTruthy();
    expect(within(list).getByText(/1 h 5 min · File/)).toBeTruthy();
    expect(within(list).getByText(/Text only/)).toBeTruthy();
    expect(within(list).getByText("Me, Them")).toBeTruthy();
    expect(screen.getByText("Choose a transcript to open it.")).toBeTruthy();
  });

  it("shows an empty state", async () => {
    mockFetch({ "GET /api/transcripts": [] });
    renderHistory();
    expect(await screen.findByText("No transcripts yet. Record a call or transcribe a file.")).toBeTruthy();
  });

  it("shows an error when the list can't load", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("down"));
    renderHistory();
    expect((await screen.findByRole("alert")).textContent).toContain("Can't reach the ScribeIQ server");
    expect(screen.getByText("Loading…")).toBeTruthy();
  });

  it("opens a transcript when clicked", async () => {
    mockFetch({ "GET /api/transcripts": items, "GET /api/transcripts/call": transcript });
    renderHistory();
    fireEvent.click(await screen.findByRole("button", { name: /Weekly sync/ }));
    expect(await screen.findByText("Hi there.")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Weekly sync/ }).getAttribute("aria-current")).toBe("true");
  });

  it("refreshes, and reloads the list after a delete", async () => {
    let listed = items;
    const spy = mockFetch({
      "GET /api/transcripts": () => listed,
      "GET /api/transcripts/call": transcript,
      "DELETE /api/transcripts/call": () => {
        listed = items.slice(1);
        return { deleted: "call" };
      },
    });
    renderHistory();
    fireEvent.click(await screen.findByRole("button", { name: "↻ Refresh" }));
    await waitFor(() => expect(calls(spy, "GET", "/api/transcripts")).toHaveLength(2));

    fireEvent.click(await screen.findByRole("button", { name: /Weekly sync/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    fireEvent.click(screen.getByRole("button", { name: "Move to Trash" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: /Weekly sync/ })).toBeNull());
    expect(screen.getByText("Choose a transcript to open it.")).toBeTruthy();
    expect(screen.getByText('Moved "Weekly sync" to the Trash.')).toBeTruthy();
  });
});
