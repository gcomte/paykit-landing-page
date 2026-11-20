import { readFile } from "node:fs/promises";
import { marked } from "marked";
import { test, expect } from "@playwright/test";

type Documentation = {
  lastUpdated: string;
  version: { commit: string; tag?: string; ref?: string; label: string };
  pages: { slug: string; title: string; group: string; kind: string; sections: {
    id: string; title: string; paragraphs?: string[]; links?: { label: string; href: string }[];
  }[] }[];
};

// Uses the real generated corpora and parent's fresh production build, not
// intercepted responses. Missing glossary or metadata is an acceptance failure.
for (const version of ["released", "coming-soon"] as const) {
  test(`${version} glossary: ordered anchors, Reference navigation, versioned exports and readable bounds`, async ({ page, request }) => {
    // Every term is clicked and checked at both widths with real scrolling.
    test.setTimeout(120_000);
    const documentation: Documentation = JSON.parse(await readFile(`content/docs/${version}.json`, "utf8"));
    const glossary = documentation.pages.find((entry) => entry.slug === "glossary");
    expect(glossary, "generate the actual source-bound glossary before browser QA").toBeTruthy();
    expect(glossary!.group).toBe("Reference");
    expect(glossary!.kind).toBe("reference");
    const route = `/docs/${version}/glossary`;
    expect((await page.goto(route))?.status()).toBe(200);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(glossary!.title);
    await expect(page.getByTestId("docs-page-kind")).toHaveText("Reference");
    const navigation = page.getByRole("navigation", { name: "Documentation pages", exact: true });
    await expect(navigation.getByRole("heading", { name: "Reference", exact: true })).toBeVisible();
    await expect(navigation.getByRole("link", { name: glossary!.title, exact: true })).toHaveAttribute("aria-current", "page");
    await expect(page.getByTestId("docs-version-marker")).toContainText(documentation.version.commit);
    const names = glossary!.sections.map((section) => section.title);
    expect(names.map((name) => name.toLowerCase())).toEqual(names.map((name) => name.toLowerCase()).sort());
    await expect(page.locator("article section > h2")).toHaveText(names);
    const contents = page.getByRole("navigation", { name: "On this page", exact: true });
    for (const section of glossary!.sections) {
      const link = contents.getByRole("link", { name: section.title, exact: true });
      await expect(link).toHaveAttribute("href", `#${section.id}`);
      await expect(page.locator(`[id="${section.id}"]`)).toHaveCount(1);
      await expect(page.locator(`[id="${section.id}-title"]`)).toHaveText(section.title);
      await link.click();
      expect(new URL(page.url()).hash).toBe(`#${section.id}`);
      await expect(page.locator(`[id="${section.id}-title"]`)).toBeInViewport();
    }
    const covered = documentation.version.tag ?? documentation.version.ref ?? documentation.version.label;
    const date = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" })
      .format(new Date(`${documentation.lastUpdated}T00:00:00Z`));
    const metadata = `Covers Paykit ${covered} · Last updated: ${date}`;
    await expect(page.getByTestId("docs-footer-metadata")).toHaveText(metadata);
    await expect(page.getByTestId("docs-footer-metadata").locator("time")).toHaveAttribute("datetime", documentation.lastUpdated);
    const markdownLink = page.getByRole("link", { name: "View as Markdown", exact: true });
    await expect(markdownLink).toHaveAttribute("href", `${route}/markdown`);
    const response = await request.get(`${route}/markdown`);
    expect(response.status()).toBe(200);
    const markdown = await response.text();
    expect(markdown).toContain(metadata);
    expect(markdown).toContain(documentation.version.commit);
    for (const section of glossary!.sections) {
      expect(markdown).toContain(`<a id="${section.id}"></a>`);
      expect(markdown).toContain(`## ${section.title}`);
      for (const link of section.links ?? []) expect(marked.parse(markdown)).toContain(`href="${link.href}"`);
    }
    const index = await request.get("/llms.txt");
    expect(index.status()).toBe(200);
    expect(marked.parse(await index.text())).toContain(`<a href="${route}/markdown">${glossary!.title}</a>`);
    const switcher = page.getByRole("navigation", { name: "Documentation version", exact: true });
    await expect(switcher.getByRole("link", { name: "Released", exact: true })).toHaveAttribute("href", "/docs/released/glossary");
    await expect(switcher.getByRole("link", { name: "Selected source", exact: true })).toHaveAttribute("href", "/docs/coming-soon/glossary");
    for (const width of [1440, 375]) {
      await page.setViewportSize({ width, height: 1000 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      for (const target of [navigation, page.getByRole("article"), page.getByTestId("docs-footer-metadata"), ...await page.locator("article section").all()]) {
        await target.scrollIntoViewIfNeeded();
        await expect(target).toBeVisible();
        expect(await target.evaluate((element) => {
          const bounds = element.getBoundingClientRect();
          return bounds.left >= 0 && bounds.right <= innerWidth && element.scrollWidth <= element.clientWidth;
        }), `${route} fits ${width}px without clipped terms`).toBe(true);
      }
    }
  });
}
