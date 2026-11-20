import { expect, test, type Page } from "@playwright/test";
import { openApp, openTab, transcriptView } from "./helpers";

// app.py opens the window with min_size=(900, 640).
test.use({ viewport: { width: 900, height: 640 } });

async function expectNoHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(() => {
    const el = document.scrollingElement!;
    return el.scrollWidth - el.clientWidth;
  });
  expect(overflow).toBeLessThanOrEqual(0);
}

test("every tab fits the minimum window without sideways scrolling", async ({ page }, testInfo) => {
  await openApp(page);
  const nav = page.getByRole("navigation", { name: "Sections" });

  for (const tab of ["Record", "Transcribe a file", "History"] as const) {
    await openTab(page, tab);
    await expect(nav.getByRole("button", { name: tab })).toBeInViewport();
    await expectNoHorizontalScroll(page);
    await testInfo.attach(`${tab} at 900x640`, { body: await page.screenshot(), contentType: "image/png" });
  }

  await openTab(page, "Record");
  for (const name of ["● Record", /Pause|Resume/, /Stop/, /Finish/]) {
    const button = page.getByRole("button", { name });
    await button.scrollIntoViewIfNeeded();
    await expect(button).toBeInViewport();
  }
});

test("History's list and transcript sit side by side without overlapping", async ({ page }) => {
  await openApp(page);
  await openTab(page, "History");
  await page.getByRole("list", { name: "Transcripts" }).getByRole("button", { name: /Seeded team call/ }).click();
  const view = transcriptView(page);
  await expect(view).toBeVisible();
  const list = (await page.getByRole("list", { name: "Transcripts" }).boundingBox())!;
  const detail = (await view.boundingBox())!;
  expect(list.x + list.width).toBeLessThanOrEqual(detail.x + 1);
  expect(detail.x + detail.width).toBeLessThanOrEqual(900);
  await expectNoHorizontalScroll(page);
});
