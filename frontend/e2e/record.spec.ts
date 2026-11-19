import { expect, test } from "@playwright/test";
import { openApp, transcriptView } from "./helpers";

test("record a call with a pause and land on the transcript", async ({ page }) => {
  await openApp(page);
  const youMeter = page.getByRole("meter", { name: "You level" });
  const themMeter = page.getByRole("meter", { name: "Them level" });
  const clock = page.getByLabel("Elapsed time");

  await page.getByLabel("Record the other side from").selectOption({ label: "zoom.us" });
  await page.getByLabel("Session name").fill("E2E call");
  await page.getByLabel("Your name in the transcript").fill("Bob");
  await expect(youMeter).toHaveAttribute("aria-valuenow", "0");

  await page.getByRole("button", { name: "● Record" }).click();
  await expect(page.getByText("Recording", { exact: true })).toBeVisible();
  await expect(page.getByText(/Wear headphones/).first()).toBeVisible();
  // The fake helper reports levels 0.25 / 0.5, boosted 1.5x by the meter.
  await expect(youMeter).toHaveAttribute("aria-valuenow", "38");
  await expect(themMeter).toHaveAttribute("aria-valuenow", "75");
  await expect(clock).not.toHaveText("00:00", { timeout: 5000 });
  await expect(page.getByLabel("Record the other side from")).toBeDisabled();

  await page.getByRole("button", { name: /Pause/ }).click();
  await expect(page.getByText("Paused", { exact: true })).toBeVisible();
  await expect(youMeter).toHaveAttribute("aria-valuenow", "0");
  await page.waitForTimeout(1200); // long enough for a "paused for 1s" marker
  await page.getByRole("button", { name: /Resume/ }).click();
  await expect(page.getByText("Recording", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: /Stop/ }).click();
  await expect(page.getByText("Stopped", { exact: true })).toBeVisible();
  await expect(page.getByText("Saved E2E call_me.wav and E2E call_them.wav.", { exact: false })).toBeVisible();

  await page.getByRole("button", { name: /Finish/ }).click();
  await expect(page.getByRole("progressbar")).toBeVisible();
  await expect(page.getByText("Processing", { exact: true })).toBeVisible();

  const view = transcriptView(page);
  await expect(view).toBeVisible({ timeout: 15_000 });
  await expect(view.getByRole("heading", { name: "E2E call" })).toBeVisible();
  await expect(view.getByRole("button", { name: "Bob" })).toBeVisible();
  // The pause lands between the other side's two lines, splitting them into two turns.
  await expect(view.getByRole("button", { name: "Them" })).toHaveCount(2);
  await expect(view.getByText("Loud and clear.")).toBeVisible();
  await expect(view.getByText(/recording paused for \d+s/)).toBeVisible();
  await expect(view.getByText("Let's get started.")).toBeVisible();
  await expect(page.getByText("Done", { exact: true })).toBeVisible();
  // A fresh session name is offered for the next call.
  await expect(page.getByLabel("Session name")).not.toHaveValue("E2E call");
});
