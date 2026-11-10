import { useCallback, useEffect, useRef, useState } from "react";
import { api, formatClock, type AppChoice, type Settings } from "../api";
import { useLive } from "../live";
import { useAction, useToast } from "../toasts";
import LevelMeter from "./LevelMeter";
import Progress from "./Progress";
import RecordControls from "./RecordControls";
import styles from "./RecordTab.module.css";
import layout from "./layout.module.css";
import TranscriptView from "./TranscriptView";

export default function RecordTab({ settings }: { settings: Settings | null }) {
  const { snapshot, setSnapshot } = useLive();
  const run = useAction();
  const toast = useToast();

  const [apps, setApps] = useState<AppChoice[]>([]);
  const [app, setApp] = useState("system");
  const [name, setName] = useState("");
  const [myName, setMyName] = useState("");
  const [theirCount, setTheirCount] = useState(1);
  const [model, setModel] = useState("large-v3");
  const [pending, setPending] = useState(false);

  // Fill defaults once settings arrive.
  useEffect(() => {
    if (!settings) return;
    setName((n) => n || settings.default_session_name);
    setMyName((n) => n || settings.my_name);
    setModel(settings.default_model);
  }, [settings]);

  const refreshApps = useCallback(
    async (keepSelection: boolean) => {
      const result = await run(api.apps);
      if (!result) return;
      setApps(result.apps);
      setApp((current) =>
        keepSelection && result.apps.some((a) => a.id === current) ? current : result.default,
      );
    },
    [run],
  );
  useEffect(() => {
    refreshApps(false);
  }, [refreshApps]);

  // Report a failed Finish once, then let the user retry.
  const reportedJob = useRef<string | null>(null);
  const job = snapshot?.job;
  useEffect(() => {
    if (job?.kind === "call" && job.status === "failed" && reportedJob.current !== job.id) {
      reportedJob.current = job.id;
      toast("error", job.error ?? "Transcription failed.");
    }
  }, [job, toast]);

  // A finished session's name is used up; offer a fresh timestamp for the next one.
  const newSessionName = useCallback(
    () => api.settings().then((s) => setName(s.default_session_name)).catch(() => {}),
    [],
  );
  const state = snapshot?.state;
  useEffect(() => {
    if (state === "Done") newSessionName();
  }, [state, newSessionName]);

  if (!snapshot) return <p className="hint">Connecting…</p>;

  const act = async (fn: () => Promise<unknown>) => {
    setPending(true);
    try {
      const result = await run(fn);
      if (result && typeof result === "object" && "state" in result) setSnapshot(result as typeof snapshot);
      return result;
    } finally {
      setPending(false);
    }
  };

  const onRecord = async () => {
    const started = await act(() => api.record(name, app));
    if (started) toast("info", "Recording. Wear headphones, or the other side bleeds into your mic track.");
  };
  const onPauseResume = () => act(snapshot.state === "Paused" ? api.resume : api.pause);
  const onStop = () => act(api.stop);
  const onFinish = async () => {
    if (await act(() => api.finish(myName, theirCount, model))) {
      api.session().then(setSnapshot).catch(() => {});
    }
  };

  const live = state === "Recording" || state === "Paused";
  const locked = live || state === "Processing";
  const callJob = job?.kind === "call" && job.status === "running" ? job : null;

  return (
    <div className={layout.page}>
      <header className={layout.header}>
        <h1>Record a call</h1>
        <p className={layout.subtitle}>
          Your mic and the other side are recorded as two synced tracks, then transcribed on this Mac.
        </p>
      </header>

      <div className={`card ${styles.setup}`}>
        <div className={layout.fields}>
          <label>
            Record the other side from
            <div className={layout.row}>
              <select value={app} onChange={(e) => setApp(e.target.value)} disabled={locked}>
                {apps.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
              <button className="btn" onClick={() => refreshApps(true)} disabled={locked} title="Refresh running apps">
                ↻
              </button>
            </div>
          </label>
          <label>
            Session name
            <div className={layout.row}>
              <input type="text" value={name} onChange={(e) => setName(e.target.value)} disabled={locked} />
              <button className="btn" onClick={newSessionName} disabled={locked} title="Use the current time">
                ⏱
              </button>
            </div>
          </label>
        </div>
        <div className={layout.fields}>
          <label>
            Your name in the transcript
            <input type="text" value={myName} onChange={(e) => setMyName(e.target.value)} disabled={state === "Processing"} />
          </label>
          <label>
            People on the other end
            <input
              type="number"
              min={1}
              max={20}
              value={theirCount}
              onChange={(e) => setTheirCount(Math.max(1, Number(e.target.value) || 1))}
              disabled={state === "Processing"}
            />
            {theirCount > 1 && (
              <span className={`hint ${settings && !settings.hf_token ? "warn" : ""}`}>
                {settings && !settings.hf_token
                  ? "More than 1 needs HF_TOKEN in .env (see README)."
                  : "Speakers will be labeled Speaker 1, Speaker 2, …"}
              </span>
            )}
          </label>
          <label>
            Whisper model
            <select value={model} onChange={(e) => setModel(e.target.value)} disabled={state === "Processing"}>
              {(settings?.models ?? ["large-v3"]).map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>

      <div className={`card ${styles.deck}`}>
        <div className={styles.status}>
          <span className={`${styles.badge} ${styles[snapshot.state.toLowerCase()]}`}>{snapshot.state}</span>
          <span className={styles.clock} aria-label="Elapsed time">
            {formatClock(snapshot.elapsed)}
          </span>
        </div>
        <div className={styles.meters}>
          <LevelMeter label="You" level={snapshot.levels.me} />
          <LevelMeter label="Them" level={snapshot.levels.them} />
        </div>
        <RecordControls
          snapshot={snapshot}
          pending={pending}
          onRecord={onRecord}
          onPauseResume={onPauseResume}
          onStop={onStop}
          onFinish={onFinish}
        />
        {live && <p className={styles.headphones}>🎧 Wear headphones. Speakers leak the other side into your mic track.</p>}
        {state === "Stopped" && snapshot.tracks && (
          <p className="hint">
            Saved {snapshot.tracks.map((t) => t.split("/").pop()).join(" and ")}. Set the number of people, then
            click Finish.
          </p>
        )}
        {callJob && <Progress job={callJob} />}
      </div>

      {state === "Done" && snapshot.transcript_id && <TranscriptView id={snapshot.transcript_id} />}
    </div>
  );
}
