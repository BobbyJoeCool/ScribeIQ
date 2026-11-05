// Typed wrappers around the ScribeIQ API (scribeiq/api.py).

export type StateName = "Idle" | "Recording" | "Paused" | "Stopped" | "Processing" | "Done";

export interface Buttons {
  record: boolean;
  pause: boolean;
  stop: boolean;
  finish: boolean;
}

export interface Job {
  id: string;
  kind: "call" | "file";
  status: "running" | "done" | "failed";
  progress: number;
  step: string;
  error: string | null;
  transcript_id: string | null;
}

export interface Snapshot {
  state: StateName;
  buttons: Buttons;
  elapsed: number;
  levels: { me: number; them: number };
  name: string | null;
  app: string | null;
  tracks: string[] | null;
  transcript_id: string | null;
  job: Job | null;
  notice: { id: number; message: string } | null;
}

export interface Segment {
  start: number;
  end: number;
  text: string;
  speaker?: string;
  marker?: string;
}

export interface Transcript {
  id: string;
  title: string;
  created?: string;
  kind?: "call" | "file" | null;
  labeled: boolean;
  duration?: number | null;
  segments: Segment[];
  speakers: string[];
  legacy?: boolean;
  text?: string; // legacy transcripts only
}

export interface TranscriptSummary {
  id: string;
  title: string;
  created: string;
  kind: "call" | "file" | null;
  duration: number | null;
  speakers: string[];
  legacy: boolean;
}

export interface AppChoice {
  id: string;
  name: string;
}

export interface Settings {
  models: string[];
  default_model: string;
  my_name: string;
  hf_token: boolean;
  default_session_name: string;
}

export class ApiError extends Error {}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError("Can't reach the ScribeIQ server. Is it still running?");
  }
  if (!res.ok) {
    throw new ApiError(await errorMessage(res));
  }
  return (await res.json()) as T;
}

async function errorMessage(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail) && body.detail[0]?.msg) return body.detail[0].msg;
  } catch {
    // fall through
  }
  return `Request failed (${res.status})`;
}

const json = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: body === undefined ? undefined : JSON.stringify(body),
});

const enc = encodeURIComponent;

export const api = {
  settings: () => request<Settings>("/api/config"),
  apps: () => request<{ apps: AppChoice[]; default: string }>("/api/apps"),
  session: () => request<Snapshot>("/api/session"),
  record: (name: string, app: string) => request<Snapshot>("/api/session/record", json("POST", { name, app })),
  pause: () => request<Snapshot>("/api/session/pause", { method: "POST" }),
  resume: () => request<Snapshot>("/api/session/resume", { method: "POST" }),
  stop: () => request<Snapshot>("/api/session/stop", { method: "POST" }),
  finish: (my_name: string, their_count: number, model: string) =>
    request<Job>("/api/session/finish", json("POST", { my_name, their_count, model })),
  transcribeFile: (form: FormData) => request<Job>("/api/files/transcribe", { method: "POST", body: form }),
  job: (id: string) => request<Job>(`/api/jobs/${enc(id)}`),
  transcripts: () => request<TranscriptSummary[]>("/api/transcripts"),
  transcript: (id: string) => request<Transcript>(`/api/transcripts/${enc(id)}`),
  renameSpeakers: (id: string, mapping: Record<string, string>) =>
    request<Transcript>(`/api/transcripts/${enc(id)}/speakers`, json("PATCH", mapping)),
  deleteTranscript: (id: string) =>
    request<{ deleted: string }>(`/api/transcripts/${enc(id)}`, { method: "DELETE" }),
  downloadUrl: (id: string, format: "txt" | "md") => `/api/transcripts/${enc(id)}/download?format=${format}`,
  async text(id: string): Promise<string> {
    const res = await fetch(api.downloadUrl(id, "txt"));
    if (!res.ok) throw new ApiError(await errorMessage(res));
    return res.text();
  },
};

export function formatClock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const pad = (n: number) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(s % 60)}` : `${pad(m)}:${pad(s % 60)}`;
}

export function formatTimestamp(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(Math.floor(s / 3600))}:${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}`;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (!seconds) return "";
  const m = Math.round(seconds / 60);
  if (m < 1) return `${Math.round(seconds)}s`;
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
}
