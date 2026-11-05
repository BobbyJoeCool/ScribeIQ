// One WebSocket to /api/live, shared by every tab. It pushes the session
// snapshot about five times a second; action responses also update it so the
// UI reacts immediately instead of waiting for the next push.

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, type Snapshot } from "./api";

interface LiveValue {
  snapshot: Snapshot | null;
  connected: boolean;
  setSnapshot: (s: Snapshot) => void;
}

const LiveContext = createContext<LiveValue>({ snapshot: null, connected: false, setSnapshot: () => {} });

export function LiveProvider({ children }: { children: ReactNode }) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let closed = false;

    api.session().then(setSnapshot).catch(() => {});

    const connect = () => {
      const scheme = location.protocol === "https:" ? "wss" : "ws";
      ws = new WebSocket(`${scheme}://${location.host}/api/live`);
      ws.onopen = () => setConnected(true);
      ws.onmessage = (e) => setSnapshot(JSON.parse(e.data) as Snapshot);
      ws.onclose = () => {
        setConnected(false);
        if (!closed) retry = setTimeout(connect, 1000);
      };
    };
    connect();
    return () => {
      closed = true;
      clearTimeout(retry);
      ws?.close();
    };
  }, []);

  return <LiveContext.Provider value={{ snapshot, connected, setSnapshot }}>{children}</LiveContext.Provider>;
}

export const useLive = () => useContext(LiveContext);

/** Recording, paused, or transcribing: leaving now would lose work. */
export function isBusy(s: Snapshot | null): boolean {
  if (!s) return false;
  return s.state === "Recording" || s.state === "Paused" || s.state === "Processing" || s.job?.status === "running";
}
