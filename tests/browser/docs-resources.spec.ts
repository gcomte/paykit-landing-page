import { readFile } from "node:fs/promises";
import AxeBuilder from "@axe-core/playwright";
import { marked } from "marked";
import { test, expect } from "@playwright/test";

type Documentation = {
  lastUpdated: string;
  version: { commit: string; tag?: string; ref?: string; label: string };
  pages: { slug: string; title: string; group: string; kind: string; sections: {
    id: string; title: string; links?: { label: string; href: string }[];
  }[] }[];
};

// Real generated content only. External links are checked by recorded href;
// anonymous public-source acquisition belongs to the coordinator, not the browser.
for (const version of ["released", "coming-soon"] as const) {
  test(`${version} Resources: primary links, TOC, navigation, exports, footer and accessible responsive bounds`, async ({ page, request }) => {
    const document: Documentation = JSON.parse(await readFile(`content/docs/${version}.json`, "utf8"));
    const resources = document.pages.find((entry) => entry.slug === "resources");
    expect(resources, "generate the actual Resources page before browser QA").toBeTruthy();
    expect(resources!.title).toBe("Resources");
    expect(resources!.group).toBe("Reference");
    expect(resources!.kind).toBe("reference");
    expect(resources!.sections.length).toBeGreaterThan(1);
    expect(document.lastUpdated).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    const route = `/docs/${version}/resources`;
    await page.emulateMedia({ reducedMotion: "reduce" });
    expect((await page.goto(route))?.status()).toBe(200);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Resources");
    await expect(page.getByTestId("docs-page-kind")).toHaveText("Reference");
    const navigation = page.getByRole("navigation", { name: "Documentation pages", exact: true });
    await expect(navigation.getByRole("heading", { name: "Reference", exact: true })).toBeVisible();
    await expect(navigation.getByRole("link", { name: "Resources", exact: true })).toHaveAttribute("aria-current", "page");
    await expect(page.getByTestId("docs-version-marker")).toContainText(document.version.commit);
    const contents = page.getByRole("navigation", { name: "On this page", exact: true });
    await expect(page.locator("article section > h2")).toHaveText(resources!.sections.map((section) => section.title));
    for (const section of resources!.sections) {
      const toc = contents.getByRole("link", { name: section.title, exact: true });
      await expect(toc).toHaveAttribute("href", `#${section.id}`);
      await toc.click();
      expect(new URL(page.url()).hash).toBe(`#${section.id}`);
      const body = page.locator(`[id="${section.id}"]`);
      await expect(body).toHaveCount(1);
      await expect(body.getByRole("heading", { level: 2 })).toHaveText(section.title);
      await expect(page.locator(`[id="${section.id}-title"]`)).toBeInViewport();
      expect(section.links?.some(({ href }) => href.startsWith(`https://github.com/pubky/paykit-rs/blob/${document.version.commit}/`))).toBe(true);
      for (const link of section.links ?? []) {
        await expect(body.getByRole("link", { name: link.label, exact: true })).toHaveAttribute("href", link.href);
      }
    }
    const covered = document.version.tag ?? document.version.ref ?? document.version.label;
    const date = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" })
      .format(new Date(`${document.lastUpdated}T00:00:00Z`));
    const metadata = `Covers Paykit ${covered} · Last updated: ${date}`;
    await expect(page.getByTestId("docs-footer-metadata")).toHaveText(metadata);
    await expect(page.getByTestId("docs-footer-metadata").locator("time")).toHaveAttribute("datetime", document.lastUpdated);
    await expect(page.getByRole("link", { name: "View as Markdown", exact: true })).toHaveAttribute("href", `${route}/markdown`);
    const response = await request.get(`${route}/markdown`);
    expect(response.status()).toBe(200);
    const markdown = await response.text();
    expect(markdown).toContain(document.version.commit);
    expect(markdown).toContain(metadata);
    for (const section of resources!.sections) {
      expect(markdown).toContain(`<a id="${section.id}"></a>`);
      expect(markdown).toContain(`## ${section.title}`);
      for (const link of section.links ?? []) expect(marked.parse(markdown)).toContain(`href="${link.href}"`);
    }
    const index = await request.get("/llms.txt");
    expect(index.status()).toBe(200);
    expect(marked.parse(await index.text())).toContain(`<a href="${route}/index.md">Resources</a>`);
    await expect(page.getByRole("navigation", { name: "Documentation version", exact: true })).toHaveCount(0);
    await expect(navigation.locator(`a[href="${route}"]`)).toHaveAttribute("aria-current", "page");
    for (const width of [1440, 375]) {
      await page.setViewportSize({ width, height: 1000 });
      expect(await page.evaluate(() => globalThis.document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      for (const target of [navigation, page.getByRole("article"), page.getByTestId("docs-footer-metadata"), ...await page.locator("article section").all()]) {
        await target.scrollIntoViewIfNeeded();
        await expect(target).toBeVisible();
        expect(await target.evaluate((element) => {
          const bounds = element.getBoundingClientRect();
          return bounds.left >= 0 && bounds.right <= innerWidth && element.scrollWidth <= element.clientWidth;
        }), `${route} fits ${width}px`).toBe(true);
      }
      const accessibility = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
      expect(accessibility.violations, `${route} at ${width}px: ${JSON.stringify(accessibility.violations)}`).toEqual([]);
    }
  });
}
