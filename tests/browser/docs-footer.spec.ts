import { readFile } from "node:fs/promises";
import { test, expect } from "@playwright/test";

type Documentation = {
  lastUpdated?: string;
  version: { tag?: string; ref?: string; label: string };
  pages: { slug: string }[];
};

// Run against the parent's fresh build AFTER both real generators have run.
// Missing persisted dates are a failure, not synthetic browser-fixture success.
test("all seventeen docs footers retain generated version/date metadata; landing has none", async ({ page, request }) => {
  const rows: { route: string; documentation: Documentation }[] = [];
  for (const version of ["released", "coming-soon"]) {
    const documentation: Documentation = JSON.parse(await readFile(`content/docs/${version}.json`, "utf8"));
    expect(documentation.lastUpdated, `${version} must be generated before browser QA`).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    for (const entry of documentation.pages) rows.push({ route: `/docs/${version}/${entry.slug}`, documentation });
    if (version === "coming-soon") rows.push({ route: "/docs", documentation });
  }
  expect(rows).toHaveLength(17);
  for (const { route, documentation } of rows) {
    const covered = documentation.version.tag ?? documentation.version.ref ?? documentation.version.label;
    const date = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" })
      .format(new Date(`${documentation.lastUpdated}T00:00:00Z`));
    const text = `Covers Paykit ${covered} · Last updated: ${date}`;
    expect((await page.goto(route))?.status()).toBe(200);
    const footer = page.locator("footer");
    const metadata = footer.getByTestId("docs-footer-metadata");
    await expect(metadata).toHaveText(text);
    await expect(metadata.locator("time")).toHaveAttribute("datetime", documentation.lastUpdated!);
    for (const width of [1440, 375]) {
      await page.setViewportSize({ width, height: 1000 });
      await metadata.scrollIntoViewIfNeeded();
      await expect(metadata).toBeVisible();
      expect(await metadata.evaluate((element) => {
        const bounds = element.getBoundingClientRect();
        return bounds.left >= 0 && bounds.right <= innerWidth && element.scrollWidth <= element.clientWidth;
      }), `${route} footer fits ${width}px`).toBe(true);
    }
    const response = await request.get(`${route}/markdown`);
    expect(response.status()).toBe(200);
    expect(await response.text()).toContain(text);
  }
  await page.goto("/");
  await expect(page.getByTestId("docs-footer-metadata")).toHaveCount(0);
  await expect(page.getByText(/Covers Paykit|Last updated:/)).toHaveCount(0);
});
