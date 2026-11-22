import { readFileSync } from "node:fs";
import { marked, type Tokens } from "marked";
import { test, expect } from "@playwright/test";

type Published = {
  lastUpdated: string;
  version: { label: string; commit: string; sourceUrl: string; readiness: { notice: string; sourceUrl: string } };
  pages: { slug: string; title: string; description: string }[];
};
const config = JSON.parse(readFileSync("docs-generation.json", "utf8")) as { defaultVersion: string };
const versions = [config.defaultVersion, ...["released", "coming-soon"].filter((v) => v !== config.defaultVersion)];
const snapshots = versions.map((version) => ({ version,
  documentation: JSON.parse(readFileSync(`content/docs/${version}.json`, "utf8")) as Published }));
const describedby = '</llms.txt>; rel="describedby"';

test("llms guide has useful generated version-specific lists and the same root Markdown guide", async ({ page, request }) => {
  const response = await request.get("/llms.txt");
  expect(response.status()).toBe(200);
  expect(response.headers()["content-type"]).toBe("text/plain; charset=utf-8");
  expect(response.headers()["x-content-type-options"]).toBe("nosniff");
  expect(response.headers()["link"]).toBe(describedby);
  const body = await response.text();
  expect(body).toMatch(/^# Paykit\n\n> [^\n]+\n\n/);
  expect(Buffer.byteLength(body)).toBeLessThan(8192); // project usefulness budget, not proposal requirement
  expect(body).not.toMatch(/View as Markdown|Pay\. Get paid\.|mailto:|\/api\/calendar|youtube|copyright|llms-full|\]\(\/markdown\)/i);
  const tokens = marked.lexer(body);
  const firstList = tokens.findIndex((t) => t.type === "heading" && t.depth === 2);
  const guidance = marked.parser(tokens.slice(0, firstList));
  const observed = await page.evaluate(({ html, guidance }) => {
    const parse = (text: string) => new DOMParser().parseFromString(text, "text/html");
    const document = parse(html);
    return { text: document.body.textContent || "", guidance: parse(guidance).body.textContent || "",
      headings: Array.from(document.querySelectorAll("h2"), (h) => h.textContent || ""),
      links: Array.from(document.querySelectorAll("a"), (a) => [a.getAttribute("href"), a.textContent]) };
  }, { html: marked.parse(body, { async: false }), guidance });
  let previous = -1;
  for (const { version, documentation: doc } of snapshots) {
    expect(observed.guidance).toContain(doc.version.label);
    expect(observed.guidance).toContain(doc.version.commit);
    expect(observed.guidance).toContain(doc.version.readiness.notice);
    expect(guidance).toContain(doc.version.sourceUrl);
    expect(guidance).toContain(doc.version.readiness.sourceUrl);
    const position = observed.headings.findIndex((h) => h.includes(doc.version.label));
    expect(position).toBeGreaterThan(previous);
    previous = position;
    for (const entry of doc.pages) {
      expect(observed.links).toContainEqual([`/docs/${version}/${entry.slug}/index.md`, entry.title]);
      expect(observed.text).toContain(entry.description);
    }
  }
  for (const list of tokens.filter((t): t is Tokens.List => t.type === "list")) {
    for (const item of list.items) expect(item.text).toMatch(/^\[[^\n]+\]\([^)]+\): .+/);
  }
  const markdown = await request.get("/index.md");
  expect(markdown.status()).toBe(200);
  expect(markdown.headers()["content-type"]).toBe("text/markdown; charset=utf-8");
  expect(markdown.headers()["x-content-type-options"]).toBe("nosniff");
  expect(markdown.headers()["link"]).toBe(describedby);
  expect(await markdown.text()).toBe(body);
});

test("HTTP Link discovery stays same-origin on homepage, docs entry and all versioned pages", async ({ request }) => {
  const targets = [["/", "/index.md"], ["/docs", "/docs/index.md"]];
  for (const { version, documentation } of snapshots) {
    for (const entry of documentation.pages) targets.push([`/docs/${version}/${entry.slug}`, `/docs/${version}/${entry.slug}/index.md`]);
  }
  for (const [route, target] of targets) {
    const response = await request.get(route);
    expect(response.status(), route).toBe(200);
    const header = response.headers()["link"];
    // Next also appends its existing font preload; compare discovery relations
    // exactly while the origin check below still covers every Link destination.
    const discovery = header.split(", ").filter((entry) => /;\s*rel="(?:alternate|describedby)"/.test(entry)).join(", ");
    expect(discovery, route).toBe(`<${target}>; rel="alternate"; type="text/markdown", ${describedby}`);
    for (const match of header.matchAll(/<([^>]+)>/g)) {
      expect(new URL(match[1], response.url()).origin).toBe(new URL(response.url()).origin);
    }
  }
});

test("all docs .md aliases preserve exact current bodies and invalid-route status", async ({ request }) => {
  const entry = await request.get("/docs/index.md");
  const legacyEntry = await request.get("/docs/markdown");
  expect(entry.status()).toBe(200);
  expect(entry.headers()["link"]).toBe(describedby);
  expect(await entry.body()).toEqual(await legacyEntry.body());
  for (const { version, documentation: doc } of snapshots) {
    for (const page of doc.pages) {
      const base = `/docs/${version}/${page.slug}`;
      const alias = await request.get(`${base}/index.md`);
      const legacy = await request.get(`${base}/markdown`);
      expect(alias.status(), base).toBe(200);
      expect(alias.headers()["content-type"]).toBe("text/markdown; charset=utf-8");
      expect(alias.headers()["x-content-type-options"]).toBe("nosniff");
      expect(alias.headers()["link"]).toBe(describedby);
      expect(await alias.body(), base).toEqual(await legacy.body());
      const body = await alias.text();
      expect(body).toContain(doc.version.commit);
      expect(body).toContain(doc.version.sourceUrl);
      const date = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" })
        .format(new Date(`${doc.lastUpdated}T00:00:00Z`));
      expect(body).toContain(date);
    }
  }
  for (const route of ["/docs/unknown/overview/index.md", "/docs/released/not-a-page/index.md"]) {
    expect((await request.get(route)).status()).toBe(404);
  }
});
