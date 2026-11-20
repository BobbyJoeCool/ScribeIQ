import { expect, test, type Page } from "@playwright/test";
import { openApp, openTab, transcriptView } from "./helpers";

/** Drop a small fake audio file on the drop zone, as a user dragging it from Finder. */
async function pickFile(page: Page, name: string) {
  const dataTransfer = await page.evaluateHandle((fileName) => {
    const dt = new DataTransfer();
    dt.items.add(new File(["fake audio bytes"], fileName, { type: "audio/mp4" }));
    return dt;
  }, name);
  await page.getByRole("button", { name: /Drop an audio file|click or drop to replace/ }).dispatchEvent("drop", { dataTransfer });
}

test.beforeEach(async ({ page }) => {
  await openApp(page);
  await openTab(page, "Transcribe a file");
});

test("upload a file, watch progress, read and download the transcript", async ({ page, request }) => {
  await pickFile(page, "interview.m4a");
  await expect(page.getByText("interview.m4a")).toBeVisible();
  await expect(page.getByLabel("Output name")).toHaveValue("interview");
  await expect(page.getByLabel(/Identify speakers/)).toBeChecked();

  await page.getByRole("button", { name: "Transcribe", exact: true }).click();
  await expect(page.getByRole("progressbar")).toBeVisible();
  await expect(page.getByText(/Transcribing with large-v3|Identifying speakers/)).toBeVisible();

  const view = transcriptView(page);
  await expect(view).toBeVisible({ timeout: 15_000 });
  await expect(view.getByRole("heading", { name: "interview" })).toBeVisible();
  await expect(view.getByRole("button", { name: "Speaker 1" })).toBeVisible();
  await expect(view.getByText("Welcome to interview.m4a.")).toBeVisible();

  // Downloads offered: .txt and .md (the .json is the app's own store, not a download).
  for (const [link, check] of [
    [".txt", (s: string) => expect(s).toContain("Speaker 1:")],
    [".md", (s: string) => expect(s.startsWith("# interview\n")).toBe(true)],
  ] as const) {
    const anchor = view.getByRole("link", { name: link });
    const [download] = await Promise.all([page.waitForEvent("download"), anchor.click()]);
    expect(download.suggestedFilename()).toBe(`interview${link}`);
    check(await (await request.get((await anchor.getAttribute("href"))!)).text());
  }
  await expect(view.getByRole("link", { name: ".json" })).toHaveCount(0);
});

test("transcribe without speaker labels", async ({ page }) => {
  await pickFile(page, "lecture.m4a");
  await page.getByLabel(/Identify speakers/).uncheck();
  await expect(page.getByLabel("Expected number of speakers")).toBeDisabled();
  await page.getByLabel("Output name").fill("E2E plain lecture");
  await page.getByRole("button", { name: "Transcribe", exact: true }).click();
  const view = transcriptView(page);
  await expect(view.getByRole("heading", { name: "E2E plain lecture" })).toBeVisible({ timeout: 15_000 });
  await expect(view.getByText("Welcome to lecture.m4a.")).toBeVisible();
  await expect(view.getByRole("button", { name: /Speaker/ })).toHaveCount(0);
});

test("nothing chosen asks for a file or link", async ({ page }) => {
  await page.getByRole("button", { name: "Transcribe", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Choose an audio file or paste an audio URL first.");
});

test("an unsupported link is refused", async ({ page }) => {
  await page.getByPlaceholder("https://example.com/path/to/audio.mp3").fill("ftp://example.com/a.mp3");
  await page.getByRole("button", { name: "Transcribe", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("The URL must start with http:// or https://.");
});

test("an unreadable file shows why and allows another try", async ({ page }) => {
  await pickFile(page, "bad.m4a");
  await page.getByRole("button", { name: "Transcribe", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Couldn't read bad.m4a as audio", { timeout: 15_000 });
  await expect(page.getByRole("button", { name: "Transcribe", exact: true })).toBeEnabled();
  await expect(transcriptView(page)).toHaveCount(0);
});
