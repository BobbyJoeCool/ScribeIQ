import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider, useAction, useToast } from "./toasts";

function Buttons() {
  const toast = useToast();
  const run = useAction();
  return (
    <>
      <button onClick={() => toast("info", "Saved.")}>info</button>
      <button onClick={() => toast("error", "Broke.")}>error</button>
      <button onClick={() => run(async () => Promise.reject(new Error("Action failed.")))}>fail</button>
      <button onClick={() => run(async () => Promise.reject("plain string"))}>failString</button>
    </>
  );
}

const renderToasts = () =>
  render(
    <ToastProvider>
      <Buttons />
    </ToastProvider>,
  );

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("toasts", () => {
  it("shows info as a status and errors as an alert", () => {
    renderToasts();
    fireEvent.click(screen.getByText("info"));
    fireEvent.click(screen.getByText("error"));
    expect(screen.getByRole("status").textContent).toContain("Saved.");
    expect(screen.getByRole("alert").textContent).toContain("Broke.");
  });

  it("dismisses info after 6 s and errors after 10 s", () => {
    renderToasts();
    fireEvent.click(screen.getByText("info"));
    fireEvent.click(screen.getByText("error"));
    act(() => vi.advanceTimersByTime(5999));
    expect(screen.queryByText("Saved.")).toBeTruthy();
    act(() => vi.advanceTimersByTime(1));
    expect(screen.queryByText("Saved.")).toBeNull();
    expect(screen.queryByText("Broke.")).toBeTruthy();
    act(() => vi.advanceTimersByTime(4000));
    expect(screen.queryByText("Broke.")).toBeNull();
  });

  it("dismisses on click", () => {
    renderToasts();
    fireEvent.click(screen.getByText("info"));
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText("Saved.")).toBeNull();
  });

  it("shows the same message once, not stacked", () => {
    renderToasts();
    fireEvent.click(screen.getByText("info"));
    fireEvent.click(screen.getByText("info"));
    expect(screen.getAllByText("Saved.")).toHaveLength(1);
  });
});

describe("useAction", () => {
  it("turns a thrown error into an error toast", async () => {
    renderToasts();
    await act(async () => fireEvent.click(screen.getByText("fail")));
    expect(screen.getByRole("alert").textContent).toContain("Action failed.");
  });

  it("handles non-Error rejections", async () => {
    renderToasts();
    await act(async () => fireEvent.click(screen.getByText("failString")));
    expect(screen.getByRole("alert").textContent).toContain("plain string");
  });
});
