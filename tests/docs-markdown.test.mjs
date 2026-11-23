import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { createRequire } from 'node:module';
import ts from 'typescript';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { marked } from 'marked';

const require = createRequire(import.meta.url);
const root = process.cwd();
export function sourceLoader(overrides = {}, transform = (_file, source) => source) {
  const modules = new Map();
  function load(file) {
    file = path.resolve(root, file);
    if (Object.hasOwn(overrides, file)) return overrides[file];
    if (modules.has(file)) return modules.get(file).exports;
    const module = { exports: {} };
    modules.set(file, module);
    const code = ts.transpileModule(transform(file, readFileSync(file, 'utf8')), {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
    }).outputText;
    new Function('require', 'module', 'exports', code)((name) => {
      if (Object.hasOwn(overrides, name)) return overrides[name];
      if (name === 'server-only' || name.endsWith('.module.css')) return {};
      if (name === 'next/navigation') return { notFound() { throw new Error('NEXT_HTTP_ERROR_FALLBACK;404'); } };
      if (name.startsWith('.') || name.startsWith('@/')) {
        const p = name.startsWith('@/') ? path.resolve(root, name.slice(2)) : path.resolve(path.dirname(file), name);
        if (p.endsWith('.json')) return Object.hasOwn(overrides, p) ? overrides[p] : JSON.parse(readFileSync(p, 'utf8'));
        const resolved = [p, `${p}.ts`, `${p}.tsx`].find((p) => existsSync(p));
        assert.ok(resolved, `Missing source dependency: ${p}`);
        return load(resolved);
      }
      return require(name);
    }, module, module.exports);
    return module.exports;
  }
  return load;
}

export async function renderedDocumentation(load, version = 'coming-soon', slug = 'overview') {
  const entry = load('app/docs/[version]/[slug]/page.tsx');
  const layout = load('app/docs/layout.tsx').default;
  return layout({ children: await entry.default({ params: Promise.resolve({ version, slug }) }) });
}

test('Markdown GET uses the actual documentation route and layout, including source and internal readiness boundaries', async () => {
  const route = 'app/docs/[version]/[slug]/markdown/route.ts';
  assert.ok(existsSync(route), 'the existing article needs a working read-only Markdown route');
  const load = sourceLoader();
  const response = await load(route).GET(new Request('https://example.invalid/docs/coming-soon/overview/markdown'), { params: Promise.resolve({ version: 'coming-soon', slug: 'overview' }) });
  assert.equal(response.status, 200);
  assert.match(response.headers.get('content-type'), /^text\/markdown; charset=utf-8$/);
  const markdown = await response.text();
  const document = JSON.parse(readFileSync('content/docs/coming-soon.json', 'utf8'));
  assert.ok(!markdown.includes(document.version.readiness.notice));
  assert.ok(markdown.includes(`Covers Paykit ${document.version.tag ?? document.version.ref ?? document.version.label}`));
  for (const link of document.pages[0].sourceLinks) assert.ok(markdown.includes(link.href));
  assert.doesNotMatch(markdown, /Source commit|View exact source/);
  assert.ok(!markdown.includes('Selected source snapshot—check its ref and commit before integrating.'));
  assert.ok(markdown.includes(`# ${document.pages[0].title}`));
  assert.ok(marked.parse(markdown).includes(`<h1>${document.pages[0].title}</h1>`));
  const html = renderToStaticMarkup(await renderedDocumentation(load));
  assert.doesNotMatch(html, /docs-version-marker|aria-label="Documentation source"/);
  for (const link of document.pages[0].sourceLinks) assert.ok(html.includes(`href="${link.href}"`));
});

export function observations(html) {
  const p = spawnSync('python', ['-B', 'tests/docs-markdown-observe.py'], { input: JSON.stringify(html), encoding: 'utf8' });
  assert.equal(p.status, 0, p.stderr);
  return JSON.parse(p.stdout);
}

test('all sixteen published articles preserve observed prose, ordered links, code, headings, images, IDs and list ownership', async () => {
  const load = sourceLoader();
  const view = load('components/docs/markdown.ts').documentationMarkdown;
  const rows = [];
  for (const version of ['released', 'coming-soon']) {
    const d = JSON.parse(readFileSync(`content/docs/${version}.json`, 'utf8'));
    for (const page of d.pages) {
      const tree = await renderedDocumentation(load, version, page.slug);
      const html = renderToStaticMarkup(tree);
      const markdown = view(tree);
      const [expected, actual] = observations([html, marked.parse(markdown)]);
      rows.push({ route: `/docs/${version}/${page.slug}`, html, markdown, expected, actual });
    }
  }
  if (process.env.PAYKIT_MARKDOWN_EVIDENCE) writeFileSync(process.env.PAYKIT_MARKDOWN_EVIDENCE, JSON.stringify(rows, null, 2) + '\n');
  assert.equal(rows.length, 16);
  for (const row of rows) assert.deepEqual(row.actual, row.expected, row.route);
});

test('Docs index has a matching Markdown view of the actual selected overview route', async () => {
  const route = 'app/docs/markdown/route.ts';
  assert.ok(existsSync(route), 'the existing Docs entry needs its Markdown endpoint');
  const load = sourceLoader();
  const response = await load(route).GET();
  assert.equal(response.status, 200);
  const entry = load('app/docs/page.tsx');
  const tree = load('app/docs/layout.tsx').default({ children: await entry.default() });
  const [expected, actual] = observations([renderToStaticMarkup(tree), marked.parse(await response.text())]);
  assert.deepEqual(actual, expected);
});

test('every existing Docs page exposes a working Markdown action and footer llms index', async () => {
  const load = sourceLoader();
  for (const version of ['released', 'coming-soon']) {
    const d = JSON.parse(readFileSync(`content/docs/${version}.json`, 'utf8'));
    for (const page of d.pages) {
      const html = renderToStaticMarkup(await renderedDocumentation(load, version, page.slug));
      assert.ok(html.includes(`href="/docs/${version}/${page.slug}/markdown"`), 'article Markdown action');
      assert.ok(html.includes('View as Markdown'));
      assert.ok(html.includes('href="/llms.txt"'), 'documentation footer index');
    }
  }
  assert.ok(renderToStaticMarkup(await load('app/docs/page.tsx').default()).includes('href="/docs/markdown"'));
  const landing = readFileSync('components/Landing.tsx', 'utf8');
  assert.match(landing.slice(landing.indexOf('<footer')), /href="\/llms\.txt"/);
});

test('llms.txt GET lists only existing Markdown views with exact published titles and source identities', async () => {
  const route = 'app/llms.txt/route.ts';
  assert.ok(existsSync(route), 'the footer target must exist');
  const load = sourceLoader();
  const response = await load(route).GET();
  assert.equal(response.status, 200);
  assert.match(response.headers.get('content-type'), /^text\/plain; charset=utf-8$/);
  const text = await response.text();
  const [index] = observations([marked.parse(text)]);
  const targets = ['/docs/index.md'];
  const catalogue = load('components/docs/content.ts');
  for (const version of [catalogue.defaultDocumentationVersion, ...catalogue.documentationVersions.filter((v) => v !== catalogue.defaultDocumentationVersion)]) {
    const d = JSON.parse(readFileSync(`content/docs/${version}.json`, 'utf8'));
    assert.ok(text.includes(d.version.commit));
    for (const page of d.pages) {
      const target = `/docs/${version}/${page.slug}/index.md`;
      targets.push(target);
      assert.ok(index.links.some(([href, label]) => href === target && label === page.title));
      assert.equal((await load('app/docs/[version]/[slug]/index.md/route.ts').GET(new Request('https://example.invalid' + target), { params: Promise.resolve({ version, slug: page.slug }) })).status, 200);
    }
  }
  assert.deepEqual(index.links.filter(([href]) => href.endsWith('/index.md')).map(([href]) => href), targets);
  // Keep the original future-selector regression independent of the live corpus:
  // the selected RC9 snapshot may now legitimately be the published body.
  const contentPath = path.resolve('components/docs/content.ts');
  const futureSelector = { schemaVersion: 1, source: { repository: 'pubky/paykit-rs', kind: 'tag', ref: 'v0.1.1-rc9' }, defaultVersion: 'coming-soon' };
  const content = sourceLoader({ [path.resolve('docs-generation.json')]: futureSelector })(contentPath);
  const publishedFixture = structuredClone(await content.getDocumentation('coming-soon'));
  publishedFixture.version = { ...publishedFixture.version,
    label: 'Previously published fixture', tag: 'synthetic-previous', ref: 'synthetic-previous', kind: 'tag',
    qualifiedRef: 'refs/tags/synthetic-previous', commit: 'a'.repeat(40),
    sourceUrl: `https://github.com/pubky/paykit-rs/tree/${'a'.repeat(40)}` };
  const previousLoad = sourceLoader({ [contentPath]: { ...content,
    getDocumentation: async (version) => version === 'coming-soon' ? publishedFixture : content.getDocumentation(version) } });
  const previousText = await (await previousLoad(route).GET()).text();
  assert.ok(previousText.includes(publishedFixture.version.commit));
  assert.ok(previousText.includes(publishedFixture.version.label));
  assert.ok(!previousText.includes('v0.1.1-rc9'), 'the configured future selector is not the published body identity');
});

test('route and layout injected qualifications are included, not only article children', async () => {
  const contentPath = path.resolve('components/docs/content.ts');
  const base = sourceLoader()(contentPath);
  const routePath = path.resolve('app/docs/[version]/[slug]/page.tsx');
  const actualRoute = sourceLoader()(routePath);
  const load = sourceLoader({
    [routePath]: { ...actualRoute, __esModule: true, default: async (props) => React.createElement('div', null,
      React.createElement('p', null, 'Synthetic route qualification: ~~may~~ &copy; [not a link](x).'),
      await actualRoute.default(props)) },
    [path.resolve('app/docs/layout.tsx')]: { __esModule: true, default: ({ children }) => React.createElement('div', null,
      children, React.createElement('aside', { role: 'note' }, 'Synthetic layout qualification: only after review.',
        React.createElement('a', { href: 'https://example.invalid/a?q=x&copy;=y#fragment' }, 'Original context'))) },
    [contentPath]: base,
  });
  const response = await load('app/docs/[version]/[slug]/markdown/route.ts').GET(new Request('https://example.invalid'), { params: Promise.resolve({ version: 'coming-soon', slug: 'overview' }) });
  const markdown = await response.text();
  const [expected, actual] = observations([renderToStaticMarkup(await renderedDocumentation(load)), marked.parse(markdown)]);
  assert.deepEqual(actual, expected);
  assert.ok(actual.text.includes('Synthetic route qualification: ~~may~~ &copy; [not a link](x).'));
  assert.ok(actual.text.includes('Synthetic layout qualification: only after review.'));
});

test('exact existing code boundary retains blank lines, CRLF, tabs, spaces, fences and adjacent inline code', async () => {
  const original = JSON.parse(readFileSync('content/docs/coming-soon.json', 'utf8'));
  const values = ['', 'alpha', ' leading and trailing  ', '\talpha  \r\n\r\nbeta\t', '```\n*literal* & <tag>\n~~~\n', 'one\n', 'two\n\n'];
  const document = structuredClone(original);
  document.pages[0].sections = [{ id: 'literal-fixture', title: 'Synthetic code boundary', evidence: [],
    paragraphs: ['Adjacent: `alpha``*beta*` and literal &copy; ~~may~~ [not a link](x).'],
    code: values.map((code) => ({ language: 'text', code })),
    links: [{ label: 'Literal &copy; ~~not strike~~', href: 'https://example.invalid/a?x=1&copy;=2#fragment' }],
  }];
  const contentPath = path.resolve('components/docs/content.ts');
  const content = sourceLoader()(contentPath);
  const load = sourceLoader({ [contentPath]: { ...content, getDocumentation: async () => document } });
  const tree = await renderedDocumentation(load);
  const markdown = load('components/docs/markdown.ts').documentationMarkdown(tree);
  const [expected, actual] = observations([renderToStaticMarkup(tree), marked.parse(markdown)]);
  assert.deepEqual(actual, expected);
  assert.deepEqual(actual.codes.slice(-values.length), values);
  assert.ok(actual.codes.includes('*beta*'));
});

test('unexamined element types refuse instead of silently dropping content; invalid routes keep not-found behavior', async () => {
  const load = sourceLoader();
  assert.throws(() => load('components/docs/markdown.ts').documentationMarkdown(React.createElement('table', null, 'new content')), /Unsupported published documentation element/);
  for (const [version, slug] of [['unknown', 'overview'], ['released', '../wallets'], ['coming-soon', 'not-a-page']]) {
    await assert.rejects(load('app/docs/[version]/[slug]/markdown/route.ts').GET(new Request('https://example.invalid'), { params: Promise.resolve({ version, slug }) }), /404/);
  }
});


test('docs routes omit global source/readiness boxes and switcher while retaining actual provenance', async () => {
  const load = sourceLoader();
  const content = load('components/docs/content.ts');
  const layout = load('app/docs/layout.tsx').default;
  for (const version of [content.defaultDocumentationVersion, ...content.documentationVersions.filter((v) => v !== content.defaultDocumentationVersion)]) {
    const doc = await content.getDocumentation(version);
    for (const page of doc.pages) {
      const tree = await renderedDocumentation(load, version, page.slug);
      const html = renderToStaticMarkup(tree);
      const forbidden = [
        ['source aside', 'data-testid="docs-version-marker"'],
        ['source landmark', 'aria-label="Documentation source"'],
        ['selected-source box', 'aria-label="Selected source version"'],
        ['readiness box', 'data-testid="readiness-notice"'],
        ['version switcher', 'aria-label="Documentation version"'],
      ].filter(([, marker]) => html.includes(marker)).map(([name]) => name);
      assert.deepEqual(forbidden, [], `${version}/${page.slug}: retired global UI`);
      const covered = doc.version.tag ?? doc.version.ref ?? doc.version.label;
      const [observed] = observations([html]);
      assert.ok(observed.text.includes(`Covers Paykit ${covered}`));
      assert.ok(html.includes(`dateTime="${doc.lastUpdated}"`));
      for (const link of page.sourceLinks) assert.ok(observed.links.some(([href]) => href === link.href));
      assert.doesNotMatch(observed.text, /Source commit|View exact source/);
      assert.ok(html.includes(`aria-current="page">${page.title}</a>`));
      assert.doesNotMatch(html, /<strong>(?:Released|Selected source)<\/strong>/);
      const markdown = load('components/docs/markdown.ts').documentationMarkdown(tree);
      assert.ok(!markdown.includes(doc.version.readiness.notice));
      assert.doesNotMatch(markdown, /Source commit|View exact source/);
      assert.ok(!markdown.includes('Selected source snapshot—check its ref and commit before integrating.'));
    }
  }
  const entry = renderToStaticMarkup(layout({ children: await load('app/docs/page.tsx').default() }));
  assert.doesNotMatch(entry, /docs-version-marker|aria-label="Documentation source"|readiness-notice|aria-label="Selected source version"|aria-label="Documentation version"/);
});
