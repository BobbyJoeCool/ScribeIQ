import type { Snapshot } from "../api";

interface Props {
  snapshot: Snapshot;
  pending: boolean;
  onRecord: () => void;
  onPauseResume: () => void;
  onStop: () => void;
  onFinish: () => void;
}

/** The four session buttons. Which ones are enabled comes straight from the
 *  server's state table (snapshot.buttons); nothing is decided here. */
export default function RecordControls({ snapshot, pending, onRecord, onPauseResume, onStop, onFinish }: Props) {
  const b = snapshot.buttons;
  const paused = snapshot.state === "Paused";
  return (
    <div className="actions">
      <button className="btn btn-record" disabled={pending || !b.record} onClick={onRecord}>
        ● Record
      </button>
      <button className="btn" disabled={pending || !b.pause} onClick={onPauseResume}>
        {paused ? "▶ Resume" : "❚❚ Pause"}
      </button>
      <button className="btn" disabled={pending || !b.stop} onClick={onStop}>
        ■ Stop
      </button>
      <button className="btn btn-primary" disabled={pending || !b.finish} onClick={onFinish}>
        Finish → transcript
      </button>
    </div>
  );
}
