import styles from "./LevelMeter.module.css";

/** Horizontal level meter. Speech rarely peaks near 1.0, so levels are boosted
 *  to make normal talking fill most of the bar. */
export default function LevelMeter({ label, level }: { label: string; level: number }) {
  const pct = Math.min(100, Math.round(level * 150));
  return (
    <div className={styles.meter}>
      <span className={styles.label}>{label}</span>
      <div className={styles.track} role="meter" aria-label={`${label} level`} aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div className={styles.fill} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
