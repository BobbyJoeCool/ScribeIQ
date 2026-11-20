import { expect, test } from "@playwright/test";
import { openApp } from "./helpers";

test.beforeEach(async ({ page }) => openApp(page));

test("a recorder error is shown and the user can retry", async ({ page }) => {
  // The fake helper refuses a session named "fail", like the real one without
  // Screen Recording permission.
  await page.getByLabel("Session name").fill("fail");
  await page.getByRole("button", { name: "● Record" }).click();
  await expect(page.getByRole("alert")).toContainText("Screen Recording permission is needed");
  const record = page.getByRole("button", { name: "● Record" });
  await expect(record).toBeEnabled();
  await expect(page.getByLabel("Session name")).toBeEnabled();

  await page.getByLabel("Session name").fill("E2E retry");
  await record.click();
  await expect(page.getByText("Recording", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /Stop/ }).click();
  await expect(page.getByText("Stopped", { exact: true })).toBeVisible();
});

test("an app that quit after the list loaded is reported", async ({ page }) => {
  await page.getByLabel("Record the other side from").selectOption({ label: "Gone App" });
  await page.getByLabel("Session name").fill("E2E gone");
  await page.getByRole("button", { name: "● Record" }).click();
  await expect(page.getByRole("alert")).toContainText("com.example.gone is not running");
  await expect(page.getByRole("button", { name: "● Record" })).toBeEnabled();
  await expect(page.getByText("Recording", { exact: true })).toHaveCount(0);
});
