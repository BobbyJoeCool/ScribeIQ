import { expect, test } from "@playwright/test";
import { createTranscript, openApp, openTab, transcriptView } from "./helpers";

test("rename a speaker everywhere, and it survives a reload", async ({ page, request }) => {
  const id = await createTranscript(request, "E2E rename");
  await openApp(page);
  await openTab(page, "History");
  const list = page.getByRole("list", { name: "Transcripts" });
  await list.getByRole("button", { name: /E2E rename/ }).click();

  const view = transcriptView(page);
  await view.getByRole("button", { name: "Speaker 1" }).click();
  const input = view.getByLabel("New name for Speaker 1");
  await input.fill("Dana");
  await input.press("Enter");

  await expect(view.getByRole("button", { name: "Dana" })).toBeVisible();
  await expect(view.getByRole("button", { name: "Speaker 1" })).toHaveCount(0);
  await expect(page.getByRole("status")).toContainText("Renamed Speaker 1 to Dana");
  await expect(list.getByRole("button", { name: /E2E rename/ })).toContainText("Dana, Speaker 2");

  // The saved files changed too.
  const txt = await (await request.get(`/api/transcripts/${encodeURIComponent(id)}/download?format=txt`)).text();
  expect(txt).toContain("Dana:");
  expect(txt).not.toContain("Speaker 1:");

  await page.reload();
  await openTab(page, "History");
  await list.getByRole("button", { name: /E2E rename/ }).click();
  await expect(view.getByRole("button", { name: "Dana" })).toBeVisible();
});

test("Escape cancels a rename", async ({ page, request }) => {
  await createTranscript(request, "E2E rename cancel");
  await openApp(page);
  await openTab(page, "History");
  await page.getByRole("list", { name: "Transcripts" }).getByRole("button", { name: /E2E rename cancel/ }).click();
  const view = transcriptView(page);
  await view.getByRole("button", { name: "Speaker 2" }).click();
  await view.getByLabel("New name for Speaker 2").fill("Nobody");
  await view.getByLabel("New name for Speaker 2").press("Escape");
  await expect(view.getByRole("button", { name: "Speaker 2" })).toBeVisible();
  await expect(view.getByRole("button", { name: "Nobody" })).toHaveCount(0);
});
