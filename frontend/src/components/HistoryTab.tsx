import { useCallback, useEffect, useState } from "react";
import { api, formatDuration, type TranscriptSummary } from "../api";
import { useAction } from "../toasts";
import styles from "./HistoryTab.module.css";
import layout from "./layout.module.css";
import TranscriptView from "./TranscriptView";

export default function HistoryTab() {
  const [items, setItems] = useState<TranscriptSummary[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const run = useAction();

  const load = useCallback(async () => {
    const list = await run(api.transcripts);
    if (list) setItems(list);
  }, [run]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className={layout.page}>
      <header className={`${layout.header} ${styles.header}`}>
        <div>
          <h1>History</h1>
          <p className={layout.subtitle}>Every saved transcript, newest first.</p>
        </div>
        <button className="btn btn-small" onClick={load}>
          ↻ Refresh
        </button>
      </header>

      <div className={styles.split}>
        <ul className={`card ${styles.list}`} aria-label="Transcripts">
          {items === null && <li className="hint">Loading…</li>}
          {items?.length === 0 && <li className="hint">No transcripts yet. Record a call or transcribe a file.</li>}
          {items?.map((t) => (
            <li key={t.id}>
              <button
                className={`${styles.item} ${selected === t.id ? styles.selected : ""}`}
                onClick={() => setSelected(t.id)}
                aria-current={selected === t.id ? "true" : undefined}
              >
                <span className={styles.title}>{t.title}</span>
                <span className="hint">
                  {[
                    new Date(t.created).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }),
                    formatDuration(t.duration),
                    t.kind === "call" ? "Call" : t.kind === "file" ? "File" : "Text only",
                  ]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
                {t.speakers.length > 0 && <span className={styles.speakers}>{t.speakers.join(", ")}</span>}
              </button>
            </li>
          ))}
        </ul>
        <div className={styles.detail}>
          {selected ? (
            <TranscriptView
              id={selected}
              onChange={load}
              onDelete={() => {
                setSelected(null);
                load();
              }}
            />
          ) : (
            <div className={`card hint ${styles.empty}`}>Choose a transcript to open it.</div>
          )}
        </div>
      </div>
    </div>
  );
}
