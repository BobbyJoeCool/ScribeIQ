import { useEffect, useRef, useState } from "react";
import styles from "./App.module.css";
import { api, type Settings } from "./api";
import FileTab from "./components/FileTab";
import HistoryTab from "./components/HistoryTab";
import RecordTab from "./components/RecordTab";
import { isBusy, useLive } from "./live";
import { useToast } from "./toasts";

type Tab = "record" | "file" | "history";

const TABS: { id: Tab; label: string; icon: string }[] = [
  { id: "record", label: "Record", icon: "●" },
  { id: "file", label: "Transcribe a file", icon: "↥" },
  { id: "history", label: "History", icon: "☰" },
];

export default function App() {
  const [tab, setTab] = useState<Tab>("record");
  const [settings, setSettings] = useState<Settings | null>(null);
  const { snapshot, connected } = useLive();
  const toast = useToast();

  useEffect(() => {
    api.settings().then(setSettings).catch((e) => toast("error", e.message));
  }, [toast]);

  // Warn before closing the page while recording or transcribing.
  const busy = isBusy(snapshot);
  useEffect(() => {
    if (!busy) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [busy]);

  // Errors the server noticed on its own (e.g. the recorder stopped).
  // The first snapshot's notice predates this page, so it is only remembered.
  const lastNotice = useRef<number | null | undefined>(undefined);
  useEffect(() => {
    if (!snapshot) return;
    const n = snapshot.notice;
    if (lastNotice.current === undefined) {
      lastNotice.current = n?.id ?? null;
    } else if (n && n.id !== lastNotice.current) {
      lastNotice.current = n.id;
      toast("error", n.message);
    }
  }, [snapshot, toast]);

  const live = snapshot?.state === "Recording" || snapshot?.state === "Paused";

  return (
    <div className={styles.shell}>
      <nav className={styles.sidebar} aria-label="Sections">
        <div className={styles.brand}>
          <span className={styles.logo} aria-hidden>
            ◉
          </span>
          ScribeIQ
        </div>
        {TABS.map((t) => (
          <button
            key={t.id}
            className={`${styles.navItem} ${tab === t.id ? styles.active : ""}`}
            onClick={() => setTab(t.id)}
            aria-current={tab === t.id ? "page" : undefined}
          >
            <span className={styles.icon} aria-hidden>
              {t.icon}
            </span>
            {t.label}
            {t.id === "record" && live && <span className={styles.liveDot} title="Recording" />}
          </button>
        ))}
        <div className={styles.footer}>
          <span className={connected ? styles.online : styles.offline} />
          {connected ? "Connected" : "Reconnecting…"}
        </div>
      </nav>
      <main className={styles.main}>
        {/* Record stays mounted so its form survives switching tabs. */}
        <div hidden={tab !== "record"}>
          <RecordTab settings={settings} />
        </div>
        <div hidden={tab !== "file"}>
          <FileTab settings={settings} />
        </div>
        {tab === "history" && <HistoryTab />}
      </main>
    </div>
  );
}
