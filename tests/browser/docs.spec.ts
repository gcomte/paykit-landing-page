import { readFile } from "node:fs/promises";
import path from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { test, expect, type Page } from "@playwright/test";

type Version = "released" | "coming-soon";
type PageData = {
  slug: string;
  title: string;
  description: string;
  group: string;
  kind: "explanation" | "tutorial";
  sections: {
    id: string;
    title: string;
    paragraphs?: string[];
    list?: string[];
    code?: { language: string; code: string; caption?: string }[];
    diagram?: { id: string; caption: string; alt: string };
  }[];
  sourceLinks: { label: string; href: string }[];
};
type Documentation = {
  version: { label: string; commit: string; tag?: string; sourceUrl: string; readiness: { status: string; notice: string; sourceUrl: string } };
  pages: PageData[];
};
const versions: Version[] = ["released", "coming-soon"];
const slugs = ["overview", "components", "flows", "boundaries", "wallets", "point-of-sale"];
const warning = "Unreleased development version—may change.";
const documentPath = (version: Version, slug: string) => `/docs/${version}/${slug}`;
const readDocumentation = async (version: Version): Promise<Documentation> =>
  JSON.parse(await readFile(path.join(process.cwd(), "content", "docs", `${version}.json`), "utf8"));
const plainText = (text: string) => text.replace(/`([^`]+)`/g, "$1");

async function expectNoOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
    `document must fit ${page.viewportSize()?.width}px`).toBe(true);
  for (const code of await page.locator("pre").all()) {
    const dimensions = await code.evaluate((element) => {
      const bounds = element.getBoundingClientRect();
      return { left: bounds.left, right: bounds.right, viewport: innerWidth,
        overflow: getComputedStyle(element).overflowX };
    });
    expect(dimensions.left).toBeGreaterThanOrEqual(0);
    expect(dimensions.right).toBeLessThanOrEqual(dimensions.viewport);
    expect(["auto", "scroll"]).toContain(dimensions.overflow);
    await expect(code).toHaveAttribute("tabindex", "0");
    await expect(code).toHaveAccessibleName(/code/i);
  }
}

test("docs entry serves the released overview", async ({ request }) => {
  const response = await request.get("/docs");
  expect(response.status(), "the documentation entry point must exist").toBe(200);
  expect(await response.text()).toContain('data-doc-version="released"');
});

test("docs entry renders the released overview rather than a version chooser", async ({ page }) => {
  const documentation = await readDocumentation("released");
  const overview = documentation.pages.find((entry) => entry.slug === "overview")!;
  await page.goto("/docs");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(overview.title);
  await expect(page.getByTestId("docs-version-marker")).toContainText(documentation.version.commit);
  await expect(page.getByText(warning, { exact: true })).toHaveCount(0);
});

for (const version of versions) {
  for (const slug of slugs) {
    test(`${version}/${slug}: content, provenance, navigation, metadata and accessible responsive layout`, async ({ page }) => {
      const documentation = await readDocumentation(version);
      const entry = documentation.pages.find((item) => item.slug === slug)!;
      expect(entry, "every required page must come from generated documentation").toBeTruthy();
      const route = documentPath(version, slug);
      const errors: string[] = [];
      page.on("pageerror", (error) => errors.push(error.message));
      await page.emulateMedia({ reducedMotion: "reduce" });
      await page.setViewportSize({ width: 1440, height: 1000 });
      const response = await page.goto(route);
      expect(response?.status()).toBe(200);
      await expect(page.getByRole("main")).toHaveAttribute("data-doc-version", version);
      await expect(page.getByRole("heading", { level: 1 })).toHaveText(entry.title);
      await expect(page.getByTestId("docs-description")).toHaveText(plainText(entry.description));
      await expect(page.getByTestId("docs-page-kind")).toHaveText(entry.kind === "tutorial" ? "Integration tutorial" : "Explanation");
      if (["wallets", "point-of-sale"].includes(slug)) expect(entry.kind).toBe("tutorial");
      const navigation = page.getByRole("navigation", { name: "Documentation pages", exact: true });
      for (const group of ["Understand Paykit", "Integrate Paykit"]) {
        await expect(navigation.getByRole("heading", { name: group, exact: true })).toBeVisible();
      }
      await expect(navigation.getByRole("link", { name: entry.title, exact: true })).toHaveAttribute("aria-current", "page");
      const marker = page.getByTestId("docs-version-marker");
      await expect(marker).toContainText(version === "released" ? "Released" : "Coming soon");
      expect(documentation.version.commit).toMatch(/^[a-f0-9]{40}$/);
      if (version === "released") expect(documentation.version.tag, "released docs identify the exact release tag").toBeTruthy();
      await expect(marker.locator("code")).toHaveText(documentation.version.commit);
      if (documentation.version.tag) await expect(marker).toContainText(documentation.version.tag);
      await expect(marker.getByRole("link", { name: "View exact source" })).toHaveAttribute("href", documentation.version.sourceUrl);
      expect(new URL(documentation.version.sourceUrl).protocol).toBe("https:");
      expect(documentation.version.sourceUrl).toContain(documentation.version.commit);
      if (version === "coming-soon") {
        await expect(page.getByRole("note", { name: "Development version" })).toHaveText(warning);
      } else {
        await expect(page.getByText(warning, { exact: true })).toHaveCount(0);
      }
      const readiness = documentation.version.readiness;
      const readinessNotice = page.getByTestId("readiness-notice");
      await expect(readinessNotice).toContainText(readiness.notice);
      await expect(readinessNotice.getByRole("link")).toHaveAttribute("href", readiness.sourceUrl);
      expect(readiness.sourceUrl).toContain(documentation.version.commit);
      if (readiness.status === "experimental") expect(readiness.notice).toContain("not for production");
      else {
        expect(readiness.status).toBe("unassessed");
        expect(readiness.notice).toContain("does not establish production readiness");
      }
      for (const section of entry.sections) {
        await expect(page.locator(`[id="${section.id}"]`).getByRole("heading", { level: 2 })).toContainText(section.title);
        for (const paragraph of section.paragraphs ?? []) {
          await expect(page.getByRole("article")).toContainText(plainText(paragraph));
        }
        for (const item of section.list ?? []) await expect(page.getByRole("article")).toContainText(plainText(item));
        const sectionCode = page.locator(`[id="${section.id}"] pre code`);
        await expect(sectionCode).toHaveCount(section.code?.length ?? 0);
        for (const [index, code] of (section.code ?? []).entries()) {
          expect(await sectionCode.nth(index).textContent()).toBe(code.code);
        }
        if (section.diagram) {
          const diagram = section.diagram;
          const figure = page.locator("figure").filter({ has: page.getByRole("img", { name: diagram.alt, exact: true }) });
          await expect(figure.locator("figcaption")).toHaveText(diagram.caption);
          await expect(figure.getByTestId("diagram-text-alternative")).toContainText(plainText(diagram.alt));
          const image = figure.getByRole("img", { name: diagram.alt, exact: true });
          await expect(image).toHaveAttribute("src", `/docs-assets/${version}/${diagram.id}.svg`);
          await image.scrollIntoViewIfNeeded();
          await expect.poll(() => image.evaluate((node) => (node as HTMLImageElement).complete && (node as HTMLImageElement).naturalWidth > 0)).toBe(true);
        }
      }
      const sources = page.getByRole("navigation", { name: "Page sources", exact: true });
      expect(entry.sourceLinks.length).toBeGreaterThan(0);
      for (const source of entry.sourceLinks) {
        expect(new URL(source.href).protocol).toBe("https:");
        await expect(sources.getByRole("link", { name: source.label, exact: true })).toHaveAttribute("href", source.href);
      }
      const nextSteps = page.getByRole("navigation", { name: "Next steps", exact: true });
      expect(await nextSteps.getByRole("link").count()).toBeGreaterThan(0);
      for (const href of await nextSteps.locator("a").evaluateAll((links) => links.map((link) => link.getAttribute("href")))) {
        expect(href).toMatch(new RegExp(`^/docs/${version}/`));
      }
      await expect(page.locator('link[rel="canonical"]')).toHaveAttribute("href", `https://paykit.to${route}`);
      await expect(page.locator('meta[property="og:url"]')).toHaveAttribute("content", `https://paykit.to${route}`);
      await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", /noindex/);
      await expect(page).toHaveTitle(new RegExp(entry.title.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
      for (const width of [1440, 375]) {
        await page.setViewportSize({ width, height: 1000 });
        await expectNoOverflow(page);
        await expect(navigation).toBeVisible();
        for (const item of documentation.pages) {
          await expect(navigation.getByRole("link", { name: item.title, exact: true })).toBeVisible();
        }
        const switcher = page.getByRole("navigation", { name: "Documentation version", exact: true });
        await expect(switcher.getByRole("link", { name: "Released", exact: true })).toBeVisible();
        await expect(switcher.getByRole("link", { name: "Coming soon", exact: true })).toBeVisible();
        const accessibility = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
        expect(accessibility.violations, `${route} at ${width}px: ${JSON.stringify(accessibility.violations)}`).toEqual([]);
      }
      expect(errors).toEqual([]);
    });
  }
}

for (const slug of slugs) {
  test(`version switch preserves ${slug} in both directions`, async ({ page }) => {
    await page.goto(documentPath("released", slug));
    for (const [version, label] of [["coming-soon", "Coming soon"], ["released", "Released"]] as const) {
      const switcher = page.getByRole("navigation", { name: "Documentation version", exact: true });
      await switcher.getByRole("link", { name: label, exact: true }).click();
      await expect(page).toHaveURL(new RegExp(`${documentPath(version, slug)}$`));
      await expect(page.getByRole("navigation", { name: "Documentation version", exact: true })
        .getByRole("link", { name: label, exact: true })).toHaveAttribute("aria-current", "page");
    }
  });
}

test("all rendered docs links and anchors resolve; unknown routes are genuine 404s", async ({ page, request }) => {
  const internalTargets = new Set<string>();
  for (const version of versions) {
    for (const slug of slugs) {
      const route = documentPath(version, slug);
      await page.goto(route);
      const links = await page.locator("a[href]").evaluateAll((nodes) => nodes.map((node) => (node as HTMLAnchorElement).href));
      for (const href of links) {
        const target = new URL(href);
        if (target.origin !== new URL(page.url()).origin) continue;
        internalTargets.add(target.pathname);
        if (target.hash) {
          const document = target.pathname === route ? page : await page.context().newPage();
          if (document !== page) await document.goto(`${target.pathname}${target.search}`);
          expect(await document.locator(`[id="${decodeURIComponent(target.hash.slice(1))}"]`).count(), href).toBe(1);
          if (document !== page) await document.close();
        }
      }
    }
  }
  for (const href of internalTargets) expect((await request.get(href)).status(), href).toBe(200);
  for (const href of ["/docs/released/not-a-page", "/docs/coming-soon/not-a-page", "/docs/not-a-version/overview", "/docs/released/overview/extra"]) {
    expect((await request.get(href)).status(), href).toBe(404);
  }
});

test("landing navigation exposes Docs on desktop and phone", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/");
  const desktop = page.getByRole("navigation", { name: "Main navigation", exact: true }).getByRole("link", { name: "Docs", exact: true });
  await expect(desktop).toBeVisible();
  await expect(desktop).toHaveAttribute("href", "/docs");
  await desktop.click();
  await expect(page).toHaveURL(/\/docs$/);
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/");
  const disclosure = page.locator('.mobile-menu > summary');
  await expect(disclosure).toHaveAccessibleName('Open navigation');
  await disclosure.click();
  const mobile = page.getByRole("navigation", { name: "Mobile navigation", exact: true }).getByRole("link", { name: "Docs", exact: true });
  await expect(mobile).toBeVisible();
  await expect(mobile).toHaveAttribute("href", "/docs");
  await mobile.click();
  await expect(page).toHaveURL(/\/docs$/);
});

test("docs skip link, page navigation, version switching and code scrolling work with a keyboard", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/docs");
  await page.keyboard.press("Tab");
  const skip = page.getByRole("link", { name: "Skip to documentation", exact: true });
  await expect(skip).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("main")).toBeFocused();
  const navigation = page.getByRole("navigation", { name: "Documentation pages", exact: true });
  const wallets = navigation.locator('a[href="/docs/released/wallets"]');
  await wallets.focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/docs\/released\/wallets$/);
  const switcher = page.getByRole("navigation", { name: "Documentation version", exact: true });
  await switcher.getByRole("link", { name: "Coming soon", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/docs\/coming-soon\/wallets$/);
  const code = page.locator("pre").first();
  await expect(code).toBeVisible();
  await code.focus();
  await expect(code).toBeFocused();
  const canScroll = await code.evaluate((node) => node.scrollWidth > node.clientWidth);
  if (canScroll) {
    await page.keyboard.press("ArrowRight");
    await expect.poll(() => code.evaluate((node) => node.scrollLeft)).toBeGreaterThan(0);
  }
  const current = page.getByRole("navigation", { name: "Documentation version", exact: true }).getByRole("link", { name: "Released", exact: true });
  await current.focus();
  expect(await current.evaluate((node) => getComputedStyle(node).outlineStyle)).not.toBe("none");
});
