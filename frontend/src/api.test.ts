import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, formatClock, formatDuration, formatTimestamp } from "./api";
import { mockFetch } from "./test-utils";

afterEach(() => vi.restoreAllMocks());

const lastCall = (spy: ReturnType<typeof mockFetch>) => {
  const [url, init] = spy.mock.calls[spy.mock.calls.length - 1];
  return { url: String(url), method: init?.method ?? "GET", body: init?.body };
};

describe("api calls", () => {
  it.each([
    ["settings", () => api.settings(), "GET", "/api/config"],
    ["apps", () => api.apps(), "GET", "/api/apps"],
    ["session", () => api.session(), "GET", "/api/session"],
    ["pause", () => api.pause(), "POST", "/api/session/pause"],
    ["resume", () => api.resume(), "POST", "/api/session/resume"],
    ["stop", () => api.stop(), "POST", "/api/session/stop"],
    ["job", () => api.job("abc"), "GET", "/api/jobs/abc"],
    ["transcripts", () => api.transcripts(), "GET", "/api/transcripts"],
    ["transcript", () => api.transcript("Weekly sync (2)"), "GET", "/api/transcripts/Weekly%20sync%20(2)"],
    ["delete", () => api.deleteTranscript("a/b"), "DELETE", "/api/transcripts/a%2Fb"],
  ])("%s sends %s %s", async (_name, call, method, path) => {
    const spy = mockFetch({ [`${method} ${path}`]: { ok: true } });
    await call();
    expect(lastCall(spy)).toMatchObject({ url: path, method });
  });

  it("record posts the name and app as JSON", async () => {
    const spy = mockFetch({ "POST /api/session/record": {} });
    await api.record("Call", "us.zoom.xos");
    const { body } = lastCall(spy);
    expect(JSON.parse(String(body))).toEqual({ name: "Call", app: "us.zoom.xos" });
    expect(spy.mock.calls[0][1]?.headers).toEqual({ "Content-Type": "application/json" });
  });

  it("finish posts my name, their count and the model", async () => {
    const spy = mockFetch({ "POST /api/session/finish": {} });
    await api.finish("Bob", 3, "turbo");
    expect(JSON.parse(String(lastCall(spy).body))).toEqual({ my_name: "Bob", their_count: 3, model: "turbo" });
  });

  it("renameSpeakers patches the mapping", async () => {
    const spy = mockFetch({ "PATCH /api/transcripts/call/speakers": {} });
    await api.renameSpeakers("call", { Them: "Dave" });
    expect(JSON.parse(String(lastCall(spy).body))).toEqual({ Them: "Dave" });
  });

  it("transcribeFile posts the form as is", async () => {
    const spy = mockFetch({ "POST /api/files/transcribe": {} });
    const form = new FormData();
    form.append("url", "https://x.com/a.mp3");
    await api.transcribeFile(form);
    expect(lastCall(spy).body).toBe(form);
  });

  it("downloadUrl encodes the id", () => {
    expect(api.downloadUrl("Weekly sync", "md")).toBe("/api/transcripts/Weekly%20sync/download?format=md");
  });

  it("text fetches the .txt download", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("[00:00:00] Me:\nHi"));
    expect(await api.text("call")).toBe("[00:00:00] Me:\nHi");
  });
});

describe("api errors", () => {
  const fail = (status: number, body: string) =>
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(body, { status }));

  it("uses the server's detail message", async () => {
    fail(409, JSON.stringify({ detail: "Can't finish while recording" }));
    await expect(api.finish("", 1, "turbo")).rejects.toThrow(new ApiError("Can't finish while recording"));
  });

  it("uses the first validation message", async () => {
    fail(422, JSON.stringify({ detail: [{ msg: "Input should be less than or equal to 20" }] }));
    await expect(api.finish("", 99, "turbo")).rejects.toThrow("Input should be less than or equal to 20");
  });

  it("falls back to the status code when the body isn't JSON", async () => {
    fail(500, "<html>oops</html>");
    await expect(api.session()).rejects.toThrow("Request failed (500)");
  });

  it("says the server is unreachable when fetch itself fails", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    await expect(api.session()).rejects.toThrow("Can't reach the ScribeIQ server. Is it still running?");
  });

  it("text reports a missing transcript", async () => {
    fail(404, JSON.stringify({ detail: "That transcript no longer exists." }));
    await expect(api.text("gone")).rejects.toThrow("That transcript no longer exists.");
  });
});

describe("formatting", () => {
  it("formatClock", () => {
    expect(formatClock(0)).toBe("00:00");
    expect(formatClock(-5)).toBe("00:00");
    expect(formatClock(65.9)).toBe("01:05");
    expect(formatClock(3725)).toBe("1:02:05");
  });

  it("formatTimestamp", () => {
    expect(formatTimestamp(0)).toBe("00:00:00");
    expect(formatTimestamp(3725)).toBe("01:02:05");
  });

  it("formatDuration", () => {
    expect(formatDuration(null)).toBe("");
    expect(formatDuration(0)).toBe("");
    expect(formatDuration(20)).toBe("20s");
    expect(formatDuration(600)).toBe("10 min");
    expect(formatDuration(3900)).toBe("1 h 5 min");
  });
});
