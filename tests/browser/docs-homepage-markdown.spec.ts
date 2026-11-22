import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { marked } from "marked";
import AxeBuilder from "@axe-core/playwright";
import { test, expect } from "@playwright/test";

test("homepage Markdown HTTP response preserves rendered primary headings", async ({ page, request }, testInfo) => {
  const response = await request.get("/markdown");
  expect(response.status()).toBe(200);
  expect(response.headers()["content-type"]).toBe("text/markdown; charset=utf-8");
  expect(response.headers()["x-content-type-options"]).toBe("nosniff");
  const markdown = await response.text();
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.emulateMedia({ reducedMotion: "reduce" });
  expect((await page.goto("/"))?.status()).toBe(200);
  const expected = await page.locator("main h1, main h2").evaluateAll((headings) =>
    headings.map((heading) => [heading.tagName.toLowerCase(), (heading as HTMLElement).innerText.replace(/\s+/g, " ").trim()]));
  const actual = await page.evaluate((html) => {
    const parsed = new DOMParser().parseFromString(html, "text/html");
    return Array.from(parsed.querySelectorAll("h1, h2"), (heading) =>
      [heading.tagName.toLowerCase(), (heading.textContent || "").replace(/\s+/g, " ").trim()]);
  }, marked.parse(markdown, { async: false }));
  await testInfo.attach("primary-heading-parity", { body: JSON.stringify({ expected, actual }, null, 2), contentType: "application/json" });
  expect(actual).toEqual(expected);
  expect(markdown).toContain("Illustrative payment options, not a list of supported integrations.");
  expect(markdown).toContain("This website doesn’t store your inquiry.");
  expect(markdown).toContain("/docs/coming-soon/wallets");
  expect(markdown).toContain("/docs/coming-soon/point-of-sale");
  const index = await request.get("/llms.txt");
  expect(index.status()).toBe(200);
  expect(await index.text()).toContain("/docs/coming-soon/overview/index.md");
  expect(errors).toEqual([]);
});

for (const width of [375, 1440]) {
  test(`homepage llms.txt footer action is visible and keyboard-operable at ${width}px`, async ({ page, request }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.setViewportSize({ width, height: 1000 });
    expect((await page.goto("/"))?.status()).toBe(200);
    const footer = page.locator(".site-footer");
    const link = footer.getByRole("link", { name: "llms.txt", exact: true });
    await expect(link).toHaveCount(1);
    await expect(footer.getByRole("link", { name: "View as Markdown", exact: true })).toHaveCount(0);
    await link.scrollIntoViewIfNeeded();
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute("href", "/llms.txt");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const accessibility = await new AxeBuilder({ page }).include(".site-footer").withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
    expect(accessibility.violations, JSON.stringify(accessibility.violations)).toEqual([]);
    const directory = path.join(process.cwd(), ".automation/homepage-markdown-screenshots");
    await mkdir(directory, { recursive: true });
    await footer.screenshot({ path: path.join(directory, `footer-${width}.png`) });
    const exported = await request.get("/llms.txt");
    expect(exported.status()).toBe(200);
    await writeFile(path.join(directory, `llms-${width}.txt`), await exported.body());
    await link.focus();
    await expect(link).toBeFocused();
    const navigationResponse = page.waitForResponse((response) => new URL(response.url()).pathname === "/llms.txt");
    await page.keyboard.press("Enter");
    const navigated = await navigationResponse;
    expect(navigated.status()).toBe(200);
    expect(navigated.request().method()).toBe("GET");
    expect(errors).toEqual([]);
  });
}

for (const width of [375, 1440]) {
  test(`homepage visible prose boundaries match Markdown at ${width}px`, async ({ page, request }, testInfo) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.setViewportSize({ width, height: 1000 });
    expect((await page.goto("/"))?.status()).toBe(200);
    await page.evaluate(() => document.fonts.ready);
    const response = await request.get("/markdown");
    expect(response.status()).toBe(200);
    const consumed = await page.evaluate((html) => {
      const document = new DOMParser().parseFromString(html, "text/html");
      return (document.body.textContent || "").replace(/\s+/g, " ").trim();
    }, marked.parse(await response.text(), { async: false }));
    const directory = path.join(process.cwd(), ".automation/shared-space-captures");
    await mkdir(directory, { recursive: true });
    const observations = [];
    for (const [name, selector, captureSelector] of [
      ["brand", ".site-header .brand-lockup", ".site-header"],
      ["caption", ".poster-caption", ".hero-video"],
      ["heading", "#how-title", "#how-title"],
    ]) {
      const element = page.locator(selector);
      const expected = (await element.innerText()).replace(/\s+/g, " ").trim();
      observations.push({ name, expected, present: consumed.includes(expected) });
      const capture = page.locator(captureSelector);
      await capture.scrollIntoViewIfNeeded();
      await capture.screenshot({ path: path.join(directory, `${name}-${width}.png`) });
    }
    await testInfo.attach("visible-prose-boundaries", { body: JSON.stringify(observations, null, 2), contentType: "application/json" });
    expect(observations.filter((row) => !row.present)).toEqual([]);
  });
}
