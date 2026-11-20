import { expect, test } from "@playwright/test";
import { createTranscript, openApp, openTab, transcriptView } from "./helpers";

test("saved transcripts are listed and open when clicked", async ({ page }) => {
  await openApp(page);
  await openTab(page, "History");
  const list = page.getByRole("list", { name: "Transcripts" });
  const call = list.getByRole("button", { name: /Seeded team call/ });
  await expect(call).toContainText("30 min · Call");
  await expect(call).toContainText("Me, Speaker 1");
  await expect(list.getByRole("button", { name: /Seeded lecture/ })).toContainText("File");
  await expect(page.getByText("Choose a transcript to open it.")).toBeVisible();

  await call.click();
  const view = transcriptView(page);
  await expect(view.getByText("Morning, everyone.")).toBeVisible();
  await expect(call).toHaveAttribute("aria-current", "true");

  await list.getByRole("button", { name: /Seeded lecture/ }).click();
  await expect(view.getByText("Today we cover sorting.")).toBeVisible();
});

test("a new transcript shows up after Refresh", async ({ page, request }) => {
  await openApp(page);
  await openTab(page, "History");
  const list = page.getByRole("list", { name: "Transcripts" });
  await expect(list.getByRole("button", { name: /Seeded team call/ })).toBeVisible();
  await createTranscript(request, "E2E refreshed");
  await expect(list.getByRole("button", { name: /E2E refreshed/ })).toHaveCount(0);
  await page.getByRole("button", { name: "↻ Refresh" }).click();
  await expect(list.getByRole("button", { name: /E2E refreshed/ })).toBeVisible();
});

test("delete only after confirming", async ({ page, request }) => {
  await createTranscript(request, "E2E delete me");
  await openApp(page);
  await openTab(page, "History");
  const list = page.getByRole("list", { name: "Transcripts" });
  const item = list.getByRole("button", { name: /E2E delete me/ });
  await item.click();

  const view = transcriptView(page);
  await view.getByRole("button", { name: "Delete" }).click();
  const confirm = view.getByRole("alertdialog", { name: "Confirm delete" });
  await expect(confirm).toContainText("Delete E2E delete me?");
  await confirm.getByRole("button", { name: "Cancel" }).click();
  await expect(confirm).toHaveCount(0);
  await expect(item).toBeVisible();

  await view.getByRole("button", { name: "Delete" }).click();
  await confirm.getByRole("button", { name: "Move to Trash" }).click();
  await expect(page.getByRole("status")).toContainText('Moved "E2E delete me" to the Trash.');
  await expect(item).toHaveCount(0);
  await expect(page.getByText("Choose a transcript to open it.")).toBeVisible();

  await page.reload();
  await openTab(page, "History");
  await expect(list.getByRole("button", { name: /Seeded team call/ })).toBeVisible();
  await expect(item).toHaveCount(0);
});
