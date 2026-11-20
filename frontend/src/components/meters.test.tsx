import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import type { Job } from "../api";
import LevelMeter from "./LevelMeter";
import Progress from "./Progress";

afterEach(cleanup);

const fillWidth = (el: HTMLElement) => (el.firstElementChild as HTMLElement).style.width;

describe("LevelMeter", () => {
  it.each([
    [0, 0],
    [0.2, 30], // boosted 1.5x so speech fills the bar
    [0.5, 75],
    [0.667, 100],
    [1, 100], // clamped
    [3, 100],
  ])("level %s fills %s%%", (level, pct) => {
    render(<LevelMeter label="You" level={level} />);
    const meter = screen.getByRole("meter", { name: "You level" });
    expect(meter.getAttribute("aria-valuenow")).toBe(String(pct));
    expect(fillWidth(meter)).toBe(`${pct}%`);
  });
});

describe("Progress", () => {
  const job = (progress: number): Job => ({
    id: "j",
    kind: "file",
    status: "running",
    progress,
    step: "Transcribing with turbo...",
    error: null,
    transcript_id: null,
  });

  it.each([
    [0, 0],
    [0.155, 16],
    [1, 100],
  ])("progress %s shows %s%%", (progress, pct) => {
    render(<Progress job={job(progress)} />);
    const bar = screen.getByRole("progressbar");
    expect(bar.getAttribute("aria-valuenow")).toBe(String(pct));
    expect(fillWidth(bar)).toBe(`${pct}%`);
    expect(screen.getByText(`${pct}%`)).toBeTruthy();
    expect(screen.getByText("Transcribing with turbo...")).toBeTruthy();
  });
});
