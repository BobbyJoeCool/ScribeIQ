import type { Job } from "../api";
import styles from "./Progress.module.css";

/** Progress bar with the current step label, for a running transcription job. */
export default function Progress({ job }: { job: Job }) {
  const pct = Math.round(job.progress * 100);
  return (
    <div className={styles.wrap}>
      <div className={styles.label}>
        <span>{job.step}</span>
        <span className={styles.pct}>{pct}%</span>
      </div>
      <div className={styles.track} role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div className={styles.fill} style={{ width: `${pct}%` }} />
      </div>
      <p className="hint">Transcription runs on this Mac. Expect a few minutes per hour of audio.</p>
    </div>
  );
}
