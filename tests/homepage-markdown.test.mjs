import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import ts from "typescript";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { marked } from "marked";
import { sourceLoader } from "./docs-markdown.test.mjs";

const require = createRequire(import.meta.url);
export async function homepageLoader(overrides = {}, transform = (_file, source) => source) {
  // Reuse the established source loader; only DocsTeaser needs an async module
  // wrapper, as in the existing landing/footer test. No replacement card copy.
  const base = sourceLoader(overrides, transform);
  const file = path.resolve("components/docs/DocsTeaser.tsx");
  const module = { exports: {} };
  const code = ts.transpileModule(transform(file, readFileSync(file, "utf8")), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
  }).outputText;
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
  await new AsyncFunction("require", "module", "exports", code)((name) => {
    if (name === "server-only" || name.endsWith(".module.css")) return {};
    if (name === "./content") return base("components/docs/content.ts");
    if (name.endsWith(".json")) return JSON.parse(readFileSync(path.resolve(path.dirname(file), name), "utf8"));
    return require(name);
  }, module, module.exports);
  return sourceLoader({
    [file]: module.exports,
    "next/font/google": { Geist: () => ({ variable: "test-font" }) },
    [path.resolve("app/globals.css")]: {},
    ...overrides,
  }, transform);
}

export function homepageTree(load) {
  return load("app/layout.tsx").default({ children: React.createElement(load("app/page.tsx").default) });
}

test("homepage GET is a deterministic read-only Markdown response from the actual entry", async () => {
  assert.ok(existsSync("app/markdown/route.ts"), "homepage Markdown route is missing");
  const load = await homepageLoader();
  const route = load("app/markdown/route.ts");
  const first = await route.GET();
  assert.equal(first.status, 200);
  assert.equal(first.headers.get("content-type"), "text/markdown; charset=utf-8");
  assert.equal(first.headers.get("x-content-type-options"), "nosniff");
  assert.equal(route.dynamic, "force-dynamic");
  const body = await first.text();
  assert.equal(await (await route.GET()).text(), body);
  const html = renderToStaticMarkup(homepageTree(load));
  assert.ok(html.includes("Pay. Get paid."));
  assert.ok(marked.parse(body).includes("Pay. Get paid."));
});

test("payment Markdown retains every actual interactive step and its illustrative qualification", async () => {
  const load = await homepageLoader();
  const story = load("components/PaymentIllustration.tsx").default();
  const markdown = await (await load("app/markdown/route.ts").GET()).text();
  const parsed = marked.parse(markdown);
  for (const step of story.props.steps) {
    for (const field of ["label", "title", "description"]) {
      const expected = renderToStaticMarkup(React.createElement("div", null, step[field]));
      assert.ok(parsed.includes(expected.slice(5, -6)), `missing actual payment step ${field}: ${step[field]}`);
    }
  }
  const warning = renderToStaticMarkup(story.props.footer).replace(/<[^>]+>/g, "");
  assert.ok(parsed.includes(warning), "the illustrative-integration qualification must survive");
});

test("video export retains the actual teaser interaction disclosure", async () => {
  const load = await homepageLoader();
  const teaser = renderToStaticMarkup(React.createElement(load("components/Interactions.tsx").Teaser, null, "Synthetic poster"));
  const label = teaser.match(/aria-label="([^"]+)"/)[1];
  const body = marked.parse(await (await load("app/markdown/route.ts").GET()).text());
  assert.ok(body.includes(label), "video origin/load disclosure must not disappear at the client boundary");
});

function observe(html, chunks = false) {
  const p = spawnSync("python", ["-B", "tests/homepage-markdown-observe.py", ...(chunks ? ["--chunks"] : [])], { input: JSON.stringify(html), encoding: "utf8" });
  assert.equal(p.status, 0, p.stderr);
  return JSON.parse(p.stdout);
}

async function expandedHTML(overrides = {}, transform = (_file, source) => source) {
  // Actual server tree + actual step props: the initial HTML only displays one
  // step title. Expose all states for the independent finite content oracle.
  // The form has an initially empty status. Render its real post-action text
  // using a test-process-only initializer, without invoking hooks ourselves.
  const allStates = (file, text) => {
    const source = transform(file, text);
    return file === path.resolve("components/Interactions.tsx")
      ? source.replace('useState("")', "useState(inquiryDraftStatus(contactEmail))") : source;
  };
  const base = await homepageLoader(overrides, allStates);
  const video = base("components/interactionContent.ts").teaserVideo;
  const real = base("components/Interactions.tsx");
  const load = await homepageLoader({ ...overrides,
    [path.resolve("components/PaymentStoryController.tsx")]: { __esModule: true, default: ({ children, steps, footer }) => React.createElement("figure", null,
      children, steps.map((step, key) => React.createElement("section", { key }, React.createElement("p", null, step.label),
        React.createElement("h3", null, step.title), React.createElement("p", null, step.description))), footer) },
    [path.resolve("components/Interactions.tsx")]: { ...real, Teaser: ({ children }) => React.createElement("div", null,
      children, React.createElement("p", null, React.createElement("a", { href: video.src }, video.playLabel))) },
  }, allStates);
  return renderToStaticMarkup(homepageTree(load));
}

test("complete homepage consumed Markdown matches actual HTML and shared interactive content", async () => {
  const load = await homepageLoader();
  const body = await (await load("app/markdown/route.ts").GET()).text();
  const [expected, actual] = observe([await expandedHTML(), marked.parse(body)]);
  assert.deepEqual(actual, expected);
});

test("actual homepage footer has exactly one visible accessible llms.txt action", async () => {
  const load = await homepageLoader();
  const html = renderToStaticMarkup(homepageTree(load));
  const footer = html.slice(html.indexOf("<footer"));
  assert.equal((footer.match(/href="\/llms\.txt"/g) ?? []).length, 1);
  assert.match(footer, /<a href="\/llms\.txt">llms\.txt(?:<| )/);
  assert.doesNotMatch(footer, /href="\/markdown"|View as Markdown/);
  assert.ok(!footer.includes("docs-footer-metadata"));
});

test("llms indexes curated technical Markdown without changing docs source identities", async () => {
  const load = await homepageLoader();
  const body = await (await load("app/llms.txt/route.ts").GET()).text();
  const [index] = observe([marked.parse(body)]);
  assert.ok(!index.links.some(([href]) => href === "/markdown"), "legacy full-homepage export is not an LLM guide resource");
  assert.ok(index.links.some(([href]) => href === "/docs/index.md"));
  for (const version of ["released", "coming-soon"]) {
    const doc = await load("components/docs/content.ts").getDocumentation(version);
    assert.ok(body.includes(doc.version.commit));
    assert.ok(index.links.some(([href]) => href === doc.version.sourceUrl));
    for (const page of doc.pages) assert.ok(index.links.some(([href, label]) => href === `/docs/${version}/${page.slug}/index.md` && label === page.title));
  }
});

test("request clocks keep HTML and Markdown conference visibility identical before and at cutoff", async (t) => {
  const load = await homepageLoader();
  const until = load("lib/conference.mjs").conferenceVisibleUntil;
  const cutoff = Date.parse(until);
  const rows = [];
  for (const now of [cutoff - 1, cutoff, cutoff + 1]) {
    t.mock.method(Date, "now", () => now);
    const rawHTML = renderToStaticMarkup(homepageTree(load));
    const markdown = await (await load("app/markdown/route.ts").GET()).text();
    const expanded = await expandedHTML();
    const [expected, actual] = observe([expanded, marked.parse(markdown)]);
    rows.push({ now, html: rawHTML, expandedHTML: expanded, markdown, expected, actual });
    assert.deepEqual(actual, expected, new Date(now).toISOString());
    assert.equal(rawHTML.includes("Meet us in El Salvador"), now < cutoff);
    assert.equal(markdown.includes("Meeting in El Salvador"), now < cutoff);
    assert.equal(markdown.includes("/api/calendar"), now < cutoff);
    assert.ok(actual.links.some(([href]) => href === "mailto:info@synonym.to"));
  }
  if (process.env.PAYKIT_HOMEPAGE_EVIDENCE) writeFileSync(process.env.PAYKIT_HOMEPAGE_EVIDENCE, JSON.stringify(rows, null, 2) + "\n");
});

test("entry layout feature and landing-body qualifications flow through the real route", async () => {
  const sources = ["app/page.tsx", "app/layout.tsx", "components/Features.tsx", "components/Landing.tsx"];
  for (const source of sources) {
    const qualification = `Synthetic ${source}: ~~only~~ &copy; [not a link](x).`;
    // Transform the actual source bytes inside the same module graph, not a
    // component captured from a different loader with different client refs.
    const transform = (file, text) => {
      if (file !== path.resolve(source)) return text;
      const start = text.lastIndexOf("return (") + "return (".length;
      const end = text.lastIndexOf("  );");
      assert.ok(start > 0 && end > start, "actual mutation seam exists");
      return text.slice(0, start) + `<><aside role="note">{${JSON.stringify(qualification)}}<a href="https://example.invalid/a?x=1&amp;copy;=2#fragment">Exact original context</a></aside>` + text.slice(start, end) + "</>" + text.slice(end);
    };
    const load = await homepageLoader({}, transform);
    const parsed = marked.parse(await (await load("app/markdown/route.ts").GET()).text());
    const [expected, actual] = observe([await expandedHTML({}, transform), parsed]);
    assert.deepEqual(actual, expected, source);
    assert.ok(actual.text.includes(qualification));
    assert.ok(!parsed.includes("<del>"));
  }
});

test("future generated card metadata updates both homepage formats without a parallel frozen copy", async () => {
  const base = await homepageLoader();
  const content = base("components/docs/content.ts");
  const doc = structuredClone(await content.getDocumentation(content.defaultDocumentationVersion));
  const featured = JSON.parse(readFileSync("automation/corrections.json", "utf8")).landingDocs.featuredSlugs;
  for (const slug of featured) {
    const page = doc.pages.find((page) => page.slug === slug);
    page.title = `Synthetic ${slug} ~~only~~ &copy; [not](x)`;
    page.description = `Synthetic imported ${slug}: qualification survives & source update.`;
  }
  const override = { [path.resolve("components/docs/content.ts")]: { ...content, getDocumentation: async () => doc } };
  const load = await homepageLoader(override);
  const body = await (await load("app/markdown/route.ts").GET()).text();
  const [expected, actual] = observe([await expandedHTML(override), marked.parse(body)]);
  assert.deepEqual(actual, expected);
  for (const slug of featured) {
    const page = doc.pages.find((page) => page.slug === slug);
    assert.ok(actual.headings.some(([, text]) => text === page.title));
    assert.ok(actual.links.some(([href, label]) => href === content.documentationPath(content.defaultDocumentationVersion, slug) && label.includes(page.description)));
  }
});

test("video source changes remain shared with the real client disclosure", async () => {
  const video = { src: "https://example.invalid/video?x=1&copy;=2#fragment", title: "Synthetic title", playLabel: "Synthetic video disclosure ~~only~~ &copy;" };
  const shared = (await homepageLoader())("components/interactionContent.ts");
  const override = { [path.resolve("components/interactionContent.ts")]: { ...shared, teaserVideo: video } };
  const load = await homepageLoader(override);
  const actualPlayer = renderToStaticMarkup(React.createElement(load("components/Interactions.tsx").Teaser, null, "Poster"));
  assert.ok(actualPlayer.includes("Synthetic video disclosure"));
  const [expected, actual] = observe([await expandedHTML(override), marked.parse(await (await load("app/markdown/route.ts").GET()).text())]);
  assert.deepEqual(actual, expected);
  assert.ok(actual.links.some(([href, text]) => href === video.src && text === video.playLabel));
});

test("the bounded homepage adapter refuses unexamined native grammar", async () => {
  const load = await homepageLoader();
  assert.throws(() => load("components/homepage/markdown.tsx").homepageMarkdown(React.createElement("table", null, "unsupported new body")), /Unsupported homepage element/);
});

test("approved llms guide and legacy compatibility are durable in current import instructions", () => {
  const rules = JSON.parse(readFileSync("automation/corrections.json", "utf8"));
  const legacy = rules.requirements.find((rule) => rule.id === "homepage-reviewed-markdown-export");
  assert.match(legacy.requirement, /\/markdown/);
  assert.match(legacy.requirement, /actual homepage entry.*layout/i);
  assert.match(legacy.requirement, /generated.*DocsTeaser/i);
  const rule = rules.requirements.find((rule) => rule.id === "llms-txt-valuable-guide-v2");
  assert.ok(rule, "record the superseding LLM-value correction");
  assert.equal(legacy.supersededBy, rule.id);
  assert.equal(rule.proposal.url, "https://llmstxt.org/");
  assert.equal(rule.proposal.version, 2);
  assert.match(rule.requirement, /generate.*metadata/i);
  assert.match(rule.requirement, /no new.*facts/i);
  assert.match(rule.requirement, /\/index\.md/);
  assert.match(rule.requirement, /describedby/);
  assert.match(rule.requirement, /Sunday.*05:00 UTC/);
  assert.match(rule.requirement, /independent review/i);
  for (const file of rule.enforcedBy) assert.ok(existsSync(file), file);
  for (const file of ["automation/COORDINATOR.md", "automation/WEEKLY.md", "automation/skills/writing.md", "automation/skills/evaluation.md"]) {
    const text = readFileSync(file, "utf8");
    assert.match(text, /llms\.txt/, file);
    assert.match(text, /\/index\.md/, file);
    assert.match(text, /generate.*metadata/i, file);
    assert.match(text, /legacy.*\/markdown/i, file);
    assert.match(text, /docs-llms\.test\.mjs/, file);
    assert.match(text, /homepage-markdown\.test\.mjs/, file);
  }
});

test("form field guidance survives as static source-derived text", async () => {
  const load = await homepageLoader();
  const html = renderToStaticMarkup(homepageTree(load));
  const [actual] = observe([marked.parse(await (await load("app/markdown/route.ts").GET()).text())]);
  const placeholders = [...html.matchAll(/placeholder="([^"]*)"/g)].map((match) => observe([`<p>${match[1]}</p>`])[0].text);
  assert.equal(placeholders.length, 4, "the real form guidance inventory is exercised");
  for (const placeholder of placeholders) assert.ok(actual.text.includes(placeholder), `missing source form guidance: ${placeholder}`);
});

function assertInitialCopy(load, markdown) {
  // Supplement the all-state oracle with the actual hooked client SSR. A new
  // client wrapper warning must fail, not disappear behind the test projection.
  const [html, actual] = observe([renderToStaticMarkup(homepageTree(load)), marked.parse(markdown)], true);
  const controls = new Set(Object.values(load("components/PaymentIllustration.tsx").default().props.labels));
  for (const chunk of html.chunks) {
    if (!controls.has(chunk)) assert.ok(actual.text.includes(chunk), `missing initial HTML copy: ${chunk}`);
  }
  const video = load("components/interactionContent.ts").teaserVideo;
  assert.deepEqual(actual.links.filter(([href]) => href !== video.src), html.links);
  assert.deepEqual(actual.images, html.images);
  for (const heading of html.headings) assert.ok(actual.headings.some((value) => JSON.stringify(value) === JSON.stringify(heading)));
}

test("actual initial client SSR copy is retained with explicit control-only omissions", async () => {
  const load = await homepageLoader();
  const markdown = await (await load("app/markdown/route.ts").GET()).text();
  assertInitialCopy(load, markdown);
  const controls = load("components/PaymentIllustration.tsx").default().props.labels;
  for (const label of Object.values(controls)) assert.ok(!markdown.includes(label), `playback-only control is omitted: ${label}`);
  assert.ok(!markdown.includes("<svg"));
  assert.ok(!markdown.includes("![]"));
});

test("initial SSR oracle detects an unshared new qualification in each client wrapper", async () => {
  for (const [source, seam] of [["components/Interactions.tsx", "{children}"], ["components/PaymentStoryController.tsx", "{footer}"]]) {
    const marker = "Synthetic new client qualification: only after review.";
    const transform = (file, text) => {
      if (file !== path.resolve(source)) return text;
      assert.ok(text.includes(seam), "the real client copy mutation seam exists");
      return text.replace(seam, `${seam}<p>${marker}</p>`);
    };
    const load = await homepageLoader({}, transform);
    const markdown = await (await load("app/markdown/route.ts").GET()).text();
    assert.throws(() => assertInitialCopy(load, markdown), /missing initial HTML copy: Synthetic new client qualification/, source);
  }
});

test("form post-action email guidance is shared with the actual client and static export", async () => {
  const base = await homepageLoader();
  const shared = base("components/interactionContent.ts");
  assert.equal(typeof shared.inquiryDraftStatus, "function", "form fallback guidance needs one shared actual source");
  const contactEmail = "example@example.invalid";
  const transform = (file, text) => file === path.resolve("components/Interactions.tsx")
    ? text.replace('useState("")', "useState(inquiryDraftStatus(contactEmail))") : text;
  const load = await homepageLoader({}, transform);
  const client = renderToStaticMarkup(React.createElement(load("components/Interactions.tsx").InquiryForm, { contactEmail }, "Fields"));
  const [actualClient] = observe([client]);
  assert.ok(actualClient.text.includes(shared.inquiryDraftStatus(contactEmail)));
  const markdown = await (await base("app/markdown/route.ts").GET()).text();
  const [actual] = observe([marked.parse(markdown)]);
  assert.ok(actual.text.includes(shared.inquiryDraftStatus("info@synonym.to")));
  assert.match(readFileSync("components/Interactions.tsx", "utf8"), /setStatus\(inquiryDraftStatus\(contactEmail\)\)/);
});

test("homepage GET preserves the payment heading sentence boundary", async () => {
  const load = await homepageLoader();
  const markdown = await (await load("app/markdown/route.ts").GET()).text();
  const [actual] = observe([marked.parse(markdown)]);
  assert.deepEqual(actual.headings.find(([level, text]) => level === 2 && text.startsWith("Pay a person.")),
    [2, "Pay a person. Let your wallet handle the payment logistics."]);
});

test("ordinary inline composition preserves explicit spaces without inventing separators", async () => {
  const view = (await homepageLoader())("components/homepage/markdown.tsx").homepageMarkdown;
  for (const [tree, text] of [
    [React.createElement("h2", null, "First.", React.createElement("span", null,
      React.createElement("em", null, "Second"), ".")), "First.Second."],
    [React.createElement("h2", null, "First.", " ", React.createElement("span", null,
      React.createElement("em", null, "Second"), ".")), "First. Second."],
    [React.createElement("p", null, React.createElement("strong", null, "Meet Paykit"), " ",
      React.createElement("span", null, "Watch the introduction")), "Meet Paykit Watch the introduction"],
    [React.createElement("p", null, "Pay", React.createElement("span", null, "kit"), ":",
      React.createElement("em", null, "ready"), "."), "Paykit:ready."],
  ]) {
    const [expected, actual] = observe([renderToStaticMarkup(tree), marked.parse(view(tree))]);
    assert.equal(expected.text, text);
    assert.deepEqual(actual, expected);
  }
});

test("each shared JSX separator controls only its own homepage word boundary", async () => {
  const boundaries = (observation) => [
    observation.links.find(([href]) => href === "#")?.[1],
    observation.text.match(/Meet Paykit\s*Watch the introduction/)?.[0],
    observation.headings.find(([level, text]) => level === 2 && text.startsWith("Pay a person."))?.[1],
  ];
  const desired = [
    "Paykit the open payment coordination protocol",
    "Meet Paykit Watch the introduction",
    "Pay a person. Let your wallet handle the payment logistics.",
  ];
  const seams = [
    ['<span className="wordmark">Paykit</span>{" "}', "Paykitthe open payment coordination protocol"],
    ['<strong>Meet Paykit</strong>{" "}', "Meet PaykitWatch the introduction"],
    ['Pay a person.{" "}', "Pay a person.Let your wallet handle the payment logistics."],
  ];
  const base = await homepageLoader();
  const originals = observe([renderToStaticMarkup(homepageTree(base)),
    marked.parse(await (await base("app/markdown/route.ts").GET()).text())]);
  for (const original of originals) assert.deepEqual(boundaries(original), desired);
  for (const [index, [seam, joined]] of seams.entries()) {
    const load = await homepageLoader({}, (file, source) => {
      if (file !== path.resolve("components/Landing.tsx")) return source;
      assert.equal(source.split(seam).length, 2, "the actual shared separator seam must occur exactly once");
      return source.replace(seam, seam.replace('{" "}', ""));
    });
    const expected = desired.map((text, at) => at === index ? joined : text);
    const observations = observe([renderToStaticMarkup(homepageTree(load)),
      marked.parse(await (await load("app/markdown/route.ts").GET()).text())]);
    for (const actual of observations) assert.deepEqual(boundaries(actual), expected,
      "removing one source space must fuse only that boundary in SSR and consumed Markdown");
  }
});

test("homepage GET preserves the shared brand and teaser word boundaries", async () => {
  const load = await homepageLoader();
  const markdown = await (await load("app/markdown/route.ts").GET()).text();
  const [actual] = observe([marked.parse(markdown)]);
  assert.deepEqual({
    brand: actual.links.find(([href]) => href === "#")?.[1],
    caption: actual.text.match(/Meet Paykit\s*Watch the introduction/)?.[0],
  }, {
    brand: "Paykit the open payment coordination protocol",
    caption: "Meet Paykit Watch the introduction",
  });
});

test("homepage imports retain routine source-whitespace repairs and rendered prose checks", () => {
  const rules = JSON.parse(readFileSync("automation/corrections.json", "utf8"));
  const rule = rules.requirements.find((row) => row.id === "homepage-reviewed-markdown-export");
  assert.match(rule.requirement, /whitespace-only.*routine/i);
  assert.match(rule.requirement, /visible wording and design/);
  assert.match(rule.requirement, /captions.*shared chrome/);
  assert.ok(rule.enforcedBy.includes("tests/browser/docs-homepage-markdown.spec.ts"));
});
