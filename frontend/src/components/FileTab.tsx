import { useEffect, useRef, useState, type DragEvent } from "react";
import { api, type Settings } from "../api";
import { useLive } from "../live";
import { useAction, useToast } from "../toasts";
import styles from "./FileTab.module.css";
import layout from "./layout.module.css";
import Progress from "./Progress";
import TranscriptView from "./TranscriptView";

/** "My%20Talk.mp3?x=1" -> "My Talk", like pipeline.source_name on the server. */
function suggestedName(fileOrUrl: string): string {
  let name = fileOrUrl;
  try {
    name = decodeURIComponent(new URL(fileOrUrl).pathname.split("/").pop() ?? "");
  } catch {
    // not a URL: a plain filename
  }
  return name.replace(/\.[^.]+$/, "");
}

export default function FileTab({ settings }: { settings: Settings | null }) {
  const { snapshot } = useLive();
  const run = useAction();
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);

  const [file, setFile] = useState<File | null>(null);
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [nameTouched, setNameTouched] = useState(false);
  const [identify, setIdentify] = useState(true);
  const [speakers, setSpeakers] = useState("2");
  const [model, setModel] = useState("large-v3");
  const [dragging, setDragging] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [transcriptId, setTranscriptId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (settings) setModel(settings.default_model);
  }, [settings]);

  // Suggest an output name from the audio unless the user typed one.
  useEffect(() => {
    if (nameTouched) return;
    const source = url.trim() || file?.name || "";
    setName(source ? suggestedName(source) : "");
  }, [file, url, nameTouched]);

  // Follow our job through the live feed.
  const job = snapshot?.job && snapshot.job.id === jobId ? snapshot.job : null;
  useEffect(() => {
    if (!job) return;
    if (job.status === "done" && job.transcript_id) {
      setTranscriptId(job.transcript_id);
      setJobId(null);
    } else if (job.status === "failed") {
      toast("error", job.error ?? "Transcription failed.");
      setJobId(null);
    }
  }, [job, toast]);

  const pick = (f: File | null | undefined) => {
    if (!f) return;
    setFile(f);
    setUrl("");
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    pick(e.dataTransfer.files[0]);
  };

  const busy = submitting || jobId !== null;
  const otherJobRunning = snapshot?.job?.status === "running" && snapshot.job.id !== jobId;

  const submit = async () => {
    if (!file && !url.trim()) {
      toast("error", "Choose an audio file or paste an audio URL first.");
      return;
    }
    const form = new FormData();
    if (url.trim()) form.append("url", url.trim());
    else if (file) form.append("file", file);
    form.append("name", name);
    form.append("identify_speakers", String(identify));
    if (identify && speakers.trim()) form.append("num_speakers", speakers.trim());
    form.append("model", model);

    setSubmitting(true);
    setTranscriptId(null);
    const started = await run(() => api.transcribeFile(form));
    setSubmitting(false);
    if (started) setJobId(started.id);
  };

  return (
    <div className={layout.page}>
      <header className={layout.header}>
        <h1>Transcribe a file</h1>
        <p className={layout.subtitle}>An audio file from this Mac, or a direct link to one.</p>
      </header>

      <div className={`card ${styles.form}`}>
        <div
          className={`${styles.drop} ${dragging ? styles.dragging : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          onClick={() => input.current?.click()}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
        >
          <input
            ref={input}
            type="file"
            accept="audio/*,video/*,.m4a,.mp3,.wav,.aac,.flac,.ogg"
            hidden
            onChange={(e) => pick(e.target.files?.[0])}
          />
          {file && !url ? (
            <>
              <strong>{file.name}</strong>
              <span className="hint">{(file.size / 1e6).toFixed(1)} MB · click or drop to replace</span>
            </>
          ) : (
            <>
              <strong>Drop an audio file here</strong>
              <span className="hint">or click to choose one</span>
            </>
          )}
        </div>

        <label>
          …or paste a direct audio URL
          <input
            type="url"
            placeholder="https://example.com/path/to/audio.mp3"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
        </label>

        <div className={layout.fields}>
          <label>
            Output name
            <input
              type="text"
              placeholder="Defaults to the audio's filename"
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                setNameTouched(e.target.value !== "");
              }}
            />
          </label>
          <label>
            Expected number of speakers
            <input
              type="number"
              min={1}
              placeholder="Let it guess"
              value={speakers}
              onChange={(e) => setSpeakers(e.target.value)}
              disabled={!identify}
            />
          </label>
          <label>
            Whisper model
            <select value={model} onChange={(e) => setModel(e.target.value)}>
              {(settings?.models ?? ["large-v3"]).map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>
        </div>

        <label className={layout.check}>
          <input type="checkbox" checked={identify} onChange={(e) => setIdentify(e.target.checked)} />
          Identify speakers
          <span className={`hint ${identify && settings && !settings.hf_token ? "warn" : ""}`}>
            {identify && settings && !settings.hf_token ? "Needs HF_TOKEN in .env (see README)." : "Slower; needs HF_TOKEN."}
          </span>
        </label>

        <div className="actions">
          <button className="btn btn-primary" onClick={submit} disabled={busy || otherJobRunning}>
            Transcribe
          </button>
          {otherJobRunning && <span className="hint">Another transcription is running.</span>}
        </div>
        {job && job.status === "running" && <Progress job={job} />}
      </div>

      {transcriptId && <TranscriptView id={transcriptId} />}
    </div>
  );
}
