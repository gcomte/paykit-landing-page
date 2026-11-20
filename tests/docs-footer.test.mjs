import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync, statSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import ts from 'typescript';
import { renderToStaticMarkup } from 'react-dom/server';
import { spawnSync } from 'node:child_process';
import { marked } from 'marked';

// Same lightweight source loading as docs-markdown.test.mjs; no Next build.
const require = createRequire(import.meta.url);
function sourceLoader(overrides = {}) {
  const modules = new Map();
  function load(file) {
    file = path.resolve(file);
    if (Object.hasOwn(overrides, file)) return overrides[file];
    if (modules.has(file)) return modules.get(file).exports;
    const module = { exports: {} };
    modules.set(file, module);
    const code = ts.transpileModule(readFileSync(file, 'utf8'), {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
    }).outputText;
    new Function('require', 'module', 'exports', code)((name) => {
      if (name === 'server-only' || name.endsWith('.module.css')) return {};
      if (name.startsWith('.') || name.startsWith('@/')) {
        const p = name.startsWith('@/') ? path.resolve(name.slice(2)) : path.resolve(path.dirname(file), name);
        if (p.endsWith('.json')) return JSON.parse(readFileSync(p, 'utf8'));
        return load([p, `${p}.ts`, `${p}.tsx`].find(existsSync));
      }
      return require(name);
    }, module, module.exports);
    return module.exports;
  }
  return load;
}

function fixtureLoader() {
  const contentPath = path.resolve('components/docs/content.ts');
  const content = sourceLoader()(contentPath);
  const documents = Object.fromEntries(['released', 'coming-soon'].map((version, index) => [version, {
    ...JSON.parse(readFileSync(`content/docs/${version}.json`, 'utf8')),
    lastUpdated: ['2026-11-18', '2026-11-19'][index], // Synthetic persisted generation days, not release dates.
  }]));
  const load = sourceLoader({ [contentPath]: { ...content, getDocumentation: async (version) => documents[version] } });
  return { load, documents };
}

async function docsTrees(load, documents) {
  const rows = [];
  const layout = load('app/docs/layout.tsx').default;
  const entry = load('app/docs/[version]/[slug]/page.tsx').default;
  for (const version of ['released', 'coming-soon']) {
    for (const page of documents[version].pages) {
      rows.push({ version, route: `/docs/${version}/${page.slug}`, tree: layout({ children: await entry({ params: Promise.resolve({ version, slug: page.slug }) }) }) });
    }
  }
  rows.push({ version: 'coming-soon', route: '/docs', tree: layout({ children: await load('app/docs/page.tsx').default() }) });
  assert.equal(rows.length, 15);
  return rows;
}

const humanDates = { released: 'Nov 18, 2026', 'coming-soon': 'Nov 19, 2026' };

test('all fifteen docs footers use their actual snapshot tag and persisted UTC generation date', async () => {
  const { load, documents } = fixtureLoader();
  for (const row of await docsTrees(load, documents)) {
    const html = renderToStaticMarkup(row.tree);
    const footer = html.match(/<footer\b[^>]*>([\s\S]*?)<\/footer>/)?.[1];
    assert.ok(footer, row.route);
    const { version, lastUpdated } = documents[row.version];
    assert.ok(footer.includes(`Covers Paykit ${version.tag}`), `${row.route}: covered version belongs in footer`);
    assert.ok(footer.includes(`Last updated: <time dateTime="${lastUpdated}">${humanDates[row.version]}</time>`), `${row.route}: persisted human-readable date`);
    assert.ok(html.includes(version.commit), 'exact source identity remains');
    assert.ok(html.includes(version.readiness.notice), 'readiness warning remains');
  }
});

test('all fifteen Markdown endpoints preserve footer metadata and complete HTML observations', async () => {
  const { load, documents } = fixtureLoader();
  for (const row of await docsTrees(load, documents)) {
    const response = row.route === '/docs'
      ? await load('app/docs/markdown/route.ts').GET()
      : await load('app/docs/[version]/[slug]/markdown/route.ts').GET(new Request(`https://example.invalid${row.route}/markdown`), {
        params: Promise.resolve({ version: row.version, slug: row.route.split('/').at(-1) }),
      });
    assert.equal(response.status, 200);
    const markdown = await response.text();
    const { version } = documents[row.version];
    assert.ok(markdown.includes(`Covers Paykit ${version.tag} · Last updated: ${humanDates[row.version]}`), row.route);
    const observed = spawnSync('python', ['-B', 'tests/docs-markdown-observe.py'], {
      input: JSON.stringify([renderToStaticMarkup(row.tree), marked.parse(markdown)]), encoding: 'utf8',
    });
    assert.equal(observed.status, 0, observed.stderr);
    const [expected, actual] = JSON.parse(observed.stdout);
    assert.deepEqual(actual, expected, row.route);
  }
});

test('build-style rendering on a later UTC day cannot bump persisted dates or write metadata', async () => {
  const files = ['content/docs/released.json', 'content/docs/coming-soon.json', 'public/docs-manifest.json'];
  const snapshot = () => files.map((file) => [file, readFileSync(file, 'utf8'), statSync(file, { bigint: true }).mtimeNs]);
  const before = snapshot();
  const OriginalDate = Date;
  let clock = '2026-11-20T23:59:00Z';
  class RenderClock extends OriginalDate {
    constructor(...args) { super(...(args.length ? args : [clock])); }
    static now() { return new OriginalDate(clock).getTime(); }
  }
  try {
    globalThis.Date = RenderClock;
    const render = async () => {
      const { load, documents } = fixtureLoader();
      return (await docsTrees(load, documents)).map(({ tree }) => [renderToStaticMarkup(tree), load('components/docs/markdown.ts').documentationMarkdown(tree)]);
    };
    const first = await render();
    clock = '2026-11-21T00:01:00Z';
    assert.deepEqual(await render(), first);
    assert.deepEqual(snapshot(), before);
  } finally {
    globalThis.Date = OriginalDate;
  }
});

test('older documents omit unknown dates rather than inventing build-time dates', async () => {
  const { load, documents } = fixtureLoader();
  for (const document of Object.values(documents)) delete document.lastUpdated;
  for (const { tree } of await docsTrees(load, documents)) {
    const html = renderToStaticMarkup(tree);
    assert.ok(html.includes('Covers Paykit'));
    assert.ok(!html.includes('Last updated:'));
    assert.ok(!html.includes('<time'));
  }
});

test('the actual landing React tree remains free of documentation footer metadata', async () => {
  const { load } = fixtureLoader();
  // The real teaser has top-level await; execute that unchanged source in an
  // async module wrapper instead of replacing the landing's documentation tree.
  const teaserPath = path.resolve('components/docs/DocsTeaser.tsx');
  const teaserModule = { exports: {} };
  const teaserCode = ts.transpileModule(readFileSync(teaserPath, 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
  }).outputText;
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
  await new AsyncFunction('require', 'module', 'exports', teaserCode)((name) => {
    if (name === 'server-only' || name.endsWith('.module.css')) return {};
    if (name.endsWith('.json')) return JSON.parse(readFileSync(path.resolve(path.dirname(teaserPath), name), 'utf8'));
    if (name === './content') return load('components/docs/content.ts');
    return require(name);
  }, teaserModule, teaserModule.exports);
  const landingLoad = sourceLoader({ [teaserPath]: teaserModule.exports });
  const Landing = landingLoad('components/Landing.tsx').default;
  const html = renderToStaticMarkup(Landing({ contactEmail: 'example@example.invalid', showConference: false }));
  assert.ok(html.includes('docs-teaser'), 'real landing docs section is present');
  assert.ok(!html.includes('docs-footer-metadata'));
  assert.ok(!html.includes('Covers Paykit'));
  assert.ok(!html.includes('Last updated:'));
});
