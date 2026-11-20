import { expect, type APIRequestContext, type Page } from "@playwright/test";

/** Open the app and wait for the live feed to connect. */
export async function openApp(page: Page) {
  await page.goto("/");
  await expect(page.getByText("Connected", { exact: true })).toBeVisible();
}

export async function openTab(page: Page, name: "Record" | "Transcribe a file" | "History") {
  await page.getByRole("navigation", { name: "Sections" }).getByRole("button", { name }).click();
}

export const transcriptView = (page: Page) => page.getByRole("region", { name: "Transcript" });

/** Save a transcript through the API (stubbed transcription of a link) and
 *  return its id, so a spec doesn't depend on another spec's leftovers. */
export async function createTranscript(request: APIRequestContext, name: string): Promise<string> {
  const started = await request.post("/api/files/transcribe", {
    multipart: { url: `https://example.com/${encodeURIComponent(name)}.mp3`, name, identify_speakers: "true", model: "turbo" },
  });
  expect(started.status()).toBe(202);
  const { id } = await started.json();
  let job = { status: "running", transcript_id: null as string | null };
  await expect
    .poll(async () => {
      job = await (await request.get(`/api/jobs/${id}`)).json();
      return job.status;
    }, { timeout: 15_000 })
    .toBe("done");
  return job.transcript_id!;
}
