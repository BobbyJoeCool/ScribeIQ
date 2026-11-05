import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";
import { LiveProvider } from "./live";
import { ToastProvider } from "./toasts";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ToastProvider>
      <LiveProvider>
        <App />
      </LiveProvider>
    </ToastProvider>
  </StrictMode>,
);
