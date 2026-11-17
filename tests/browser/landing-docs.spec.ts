import { mkdir, readFile } from "node:fs/promises";
import path from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { test, expect } from "@playwright/test";

const featuredSlugs = ["flows", "wallets", "point-of-sale"];
const widths = [375, 768, 1440];

type GeneratedPage = { slug: string; title: string; description: string };

for (const width of widths) {
  test(`homepage Docs is a selected-source, accessible reading entry at ${width}px`, async ({ page, request }) => {
    const corrections = JSON.parse(await readFile(path.join(process.cwd(), "automation/corrections.json"), "utf8"));
    const policy = corrections.landingDocs;
    // These fixed user invariants must not silently move with implementation or policy drift.
    expect(policy.sectionId).toBe("docs-teaser");
    expect(policy.beforeSectionAriaLabelledby).toBe("foundation-title");
    expect(policy.mainHref).toBe("/docs");
    expect(policy.version).toBe("default");
    expect(policy.featuredSlugs).toEqual(featuredSlugs);
    expect(policy.responsiveWidths).toEqual(widths);
    const documentation = JSON.parse(await readFile(path.join(process.cwd(), "content/docs/coming-soon.json"), "utf8")) as { pages: GeneratedPage[] };
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.setViewportSize({ width, height: 1000 });
    expect((await page.goto("/"))?.status()).toBe(200);

    const section = page.locator("main > section#docs-teaser");
    await expect(section, "homepage must have a dedicated Docs section").toBeVisible();
    await expect(page.locator('#docs-teaser + section[aria-labelledby="foundation-title"]')).toHaveCount(1);
    await expect(page.locator("#foundation-title")).toHaveText("Your data.Your choice.");
    await expect(section.getByRole("heading", { level: 2 })).toContainText("Docs");
    const mainLink = section.locator('a[href="/docs"]');
    await expect(mainLink).toBeVisible();
    await expect(mainLink).toHaveAccessibleName(/docs/i);
    const cards = section.locator('a[href^="/docs/coming-soon/"]');
    await expect(cards).toHaveCount(3);
    await expect(section.getByRole("link")).toHaveCount(4);
    await expect(section.locator('a[href*="released"]')).toHaveCount(0);
    for (const [index, slug] of featuredSlugs.entries()) {
      const entry = documentation.pages.find((candidate) => candidate.slug === slug);
      expect(entry, `generated selected metadata must include ${slug}`).toBeTruthy();
      const card = cards.nth(index);
      await expect(card).toHaveAttribute("href", `/docs/coming-soon/${slug}`);
      await expect(card.getByRole("heading", { level: 3 })).toHaveText(entry!.title);
      await expect(card.locator("p")).toHaveText(entry!.description);
    }
    await page.evaluate(() => document.fonts.ready);
    await section.scrollIntoViewIfNeeded();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(await section.evaluate((element) => {
      const bounds = element.getBoundingClientRect();
      return bounds.left >= 0 && bounds.right <= innerWidth && element.scrollWidth <= element.clientWidth;
    })).toBe(true);
    for (const link of await section.getByRole("link").all()) {
      const box = (await link.boundingBox())!;
      expect(box.width).toBeGreaterThanOrEqual(44);
      expect(box.height).toBeGreaterThanOrEqual(44);
    }
    for (const text of await section.locator("h2, h3, p").all()) {
      expect(await text.evaluate((element) => parseFloat(getComputedStyle(element).fontSize))).toBeGreaterThanOrEqual(16);
      expect(await text.evaluate((element) => element.scrollWidth <= element.clientWidth &&
        (element.scrollHeight <= element.clientHeight || getComputedStyle(element).overflowY === "visible")),
      "text must fit horizontally and must not be vertically clipped (serif glyphs can exceed their line box)").toBe(true);
    }
    const accessibility = await new AxeBuilder({ page }).include("#docs-teaser")
      .withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
    expect(accessibility.violations, JSON.stringify(accessibility.violations)).toEqual([]);
    const screenshots = path.join(process.cwd(), ".automation/landing-docs-screenshots");
    await mkdir(screenshots, { recursive: true });
    // Fit the whole section before capture: Chromium's beyond-viewport capture
    // can composite the site's translated, offscreen fixed skip link over it.
    const sectionHeight = Math.ceil((await section.boundingBox())!.height);
    await page.setViewportSize({ width, height: Math.max(1000, sectionHeight + 2) });
    await section.scrollIntoViewIfNeeded();
    await section.screenshot({ path: path.join(screenshots, `docs-${width}.png`) });

    // Native tab order, a visible focus ring and keyboard activation of every route.
    await mainLink.focus();
    for (const link of await section.getByRole("link").all()) {
      await expect(link).toBeFocused();
      expect(await link.evaluate((element) => getComputedStyle(element).outlineStyle)).not.toBe("none");
      expect(await link.evaluate((element) => parseFloat(getComputedStyle(element).outlineWidth))).toBeGreaterThanOrEqual(2);
      const href = (await link.getAttribute("href"))!;
      expect((await request.get(href)).status(), href).toBe(200);
      await page.keyboard.press("Tab");
    }
    for (const href of ["/docs", ...featuredSlugs.map((slug) => `/docs/coming-soon/${slug}`)]) {
      const link = section.locator(`a[href="${href}"]`);
      await link.focus();
      await page.keyboard.press("Enter");
      await expect(page).toHaveURL(new RegExp(`${href}$`));
      await expect(page.getByRole("main")).toHaveAttribute("data-doc-version", "coming-soon");
      await page.goto("/");
    }
    expect(errors).toEqual([]);
  });
}
