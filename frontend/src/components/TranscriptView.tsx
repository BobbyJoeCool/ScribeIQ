import { useEffect, useRef, useState } from "react";
import { api, formatDuration, formatTimestamp, type Segment, type Transcript } from "../api";
import { useAction, useToast } from "../toasts";
import styles from "./TranscriptView.module.css";

type Block =
  | { kind: "turn"; speaker: string; start: number; text: string }
  | { kind: "marker"; start: number; text: string }
  | { kind: "line"; start: number; text: string };

/** Group consecutive segments by the same speaker into one turn, the same way
 *  the saved .txt does. */
export function toBlocks(t: Transcript): Block[] {
  const blocks: Block[] = [];
  for (const seg of t.segments as Segment[]) {
    if (seg.marker) {
      blocks.push({ kind: "marker", start: seg.start, text: seg.marker });
    } else if (!t.labeled || !seg.speaker) {
      blocks.push({ kind: "line", start: seg.start, text: seg.text });
    } else {
      const last = blocks[blocks.length - 1];
      if (last?.kind === "turn" && last.speaker === seg.speaker) {
        last.text += " " + seg.text;
      } else {
        blocks.push({ kind: "turn", speaker: seg.speaker, start: seg.start, text: seg.text });
      }
    }
  }
  return blocks;
}

interface Props {
  id: string;
  /** Called after a rename so lists can refresh. */
  onChange?: (t: Transcript) => void;
  /** When given, shows a Delete button; called after the transcript is moved to the Trash. */
  onDelete?: (id: string) => void;
}

export default function TranscriptView({ id, onChange, onDelete }: Props) {
  const [transcript, setTranscript] = useState<Transcript | null>(null);
  const [editing, setEditing] = useState<number | null>(null); // index of the turn being renamed
  const [confirmDelete, setConfirmDelete] = useState(false);
  const run = useAction();
  const toast = useToast();

  useEffect(() => {
    let current = true;
    setTranscript(null);
    setConfirmDelete(false);
    api
      .transcript(id)
      .then((t) => current && setTranscript(t))
      .catch((e) => toast("error", e.message));
    return () => {
      current = false;
    };
  }, [id, toast]);

  if (!transcript) return <div className={`card ${styles.loading}`}>Loading transcript…</div>;

  const rename = async (from: string, to: string) => {
    setEditing(null);
    if (!to.trim() || to.trim() === from) return;
    const updated = await run(() => api.renameSpeakers(id, { [from]: to.trim() }));
    if (updated) {
      setTranscript(updated);
      onChange?.(updated);
      toast("info", `Renamed ${from} to ${to.trim()}. The saved files are updated too.`);
    }
  };

  const copy = () =>
    run(async () => {
      await navigator.clipboard.writeText(await api.text(id));
      toast("info", "Transcript copied.");
    });

  const remove = async () => {
    const done = await run(() => api.deleteTranscript(id));
    if (done) {
      toast("info", `Moved "${transcript.title}" to the Trash.`);
      onDelete?.(id);
    }
  };

  const color = (speaker: string) => `var(--speaker-${transcript.speakers.indexOf(speaker) % 8})`;
  const blocks = toBlocks(transcript);

  return (
    <section className={`card ${styles.view}`} aria-label="Transcript">
      <header className={styles.head}>
        <div>
          <h2>{transcript.title}</h2>
          <p className="hint">
            {[
              transcript.created && new Date(transcript.created).toLocaleString(),
              formatDuration(transcript.duration),
              transcript.labeled && transcript.speakers.length > 0 && "Click a name to rename that speaker everywhere",
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>
        <div className="actions">
          <button className="btn btn-small" onClick={copy}>
            Copy
          </button>
          <a className="btn btn-small" href={api.downloadUrl(id, "txt")} download>
            .txt
          </a>
          <a className="btn btn-small" href={api.downloadUrl(id, "md")} download>
            .md
          </a>
          {onDelete && !confirmDelete && (
            <button className="btn btn-small btn-danger" onClick={() => setConfirmDelete(true)}>
              Delete
            </button>
          )}
        </div>
      </header>

      {confirmDelete && (
        <div className={styles.confirm} role="alertdialog" aria-label="Confirm delete">
          <span>
            Delete <strong>{transcript.title}</strong>? Its .txt, .md and .json files move to the Trash, where you
            can still recover them.
          </span>
          <div className="actions">
            <button className="btn btn-small" onClick={() => setConfirmDelete(false)}>
              Cancel
            </button>
            <button className="btn btn-small btn-danger-solid" onClick={remove}>
              Move to Trash
            </button>
          </div>
        </div>
      )}

      {transcript.legacy ? (
        <pre className={styles.legacy}>{transcript.text}</pre>
      ) : blocks.length === 0 ? (
        <p className="hint">No speech was found in this recording.</p>
      ) : (
        <div className={styles.body}>
          {blocks.map((b, i) => {
            if (b.kind === "marker") {
              return (
                <div key={i} className={styles.marker}>
                  <span className={styles.time}>{formatTimestamp(b.start)}</span>
                  {b.text}
                </div>
              );
            }
            if (b.kind === "line") {
              return (
                <p key={i} className={styles.line}>
                  <span className={styles.time}>{formatTimestamp(b.start)}</span>
                  {b.text}
                </p>
              );
            }
            return (
              <div key={i} className={styles.turn}>
                <div className={styles.who}>
                  {editing === i ? (
                    <RenameInput initial={b.speaker} onDone={(to) => rename(b.speaker, to)} />
                  ) : (
                    <button
                      className={styles.speaker}
                      style={{ color: color(b.speaker) }}
                      onClick={() => setEditing(i)}
                      title={`Rename ${b.speaker}`}
                    >
                      {b.speaker}
                    </button>
                  )}
                  <span className={styles.time}>{formatTimestamp(b.start)}</span>
                </div>
                <p className={styles.text} style={{ borderColor: color(b.speaker) }}>
                  {b.text}
                </p>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

function RenameInput({ initial, onDone }: { initial: string; onDone: (value: string) => void }) {
  const [value, setValue] = useState(initial);
  const ref = useRef<HTMLInputElement>(null);
  const done = useRef(false);
  useEffect(() => ref.current?.select(), []);

  const finish = (v: string) => {
    if (done.current) return;
    done.current = true;
    onDone(v);
  };

  return (
    <input
      ref={ref}
      type="text"
      className={styles.rename}
      aria-label={`New name for ${initial}`}
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onBlur={() => finish(value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") finish(value);
        if (e.key === "Escape") finish(initial);
      }}
    />
  );
}
