import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import ts from 'typescript';
import { renderToStaticMarkup } from 'react-dom/server';
import { marked } from 'marked';
import { spawnSync } from 'node:child_process';

const require = createRequire(import.meta.url);
function sourceLoader(overrides = {}) {
  const modules = new Map();
  function load(file) {
    file = path.resolve(file);
    if (Object.hasOwn(overrides, file)) return overrides[file];
    if (file.endsWith('.json')) return JSON.parse(readFileSync(file, 'utf8'));
    if (modules.has(file)) return modules.get(file).exports;
    const module = { exports: {} };
    modules.set(file, module);
    const code = ts.transpileModule(readFileSync(file, 'utf8'), {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
    }).outputText;
    new Function('require', 'module', 'exports', code)((name) => {
      if (name === 'server-only' || name.endsWith('.module.css')) return {};
      if (name === 'next/navigation') return { notFound() { throw new Error('404'); } };
      if (name.startsWith('.') || name.startsWith('@/')) {
        const p = name.startsWith('@/') ? path.resolve(name.slice(2)) : path.resolve(path.dirname(file), name);
        return load([p, `${p}.ts`, `${p}.tsx`].find(existsSync));
      }
      return require(name);
    }, module, module.exports);
    return module.exports;
  }
  return load;
}

test('glossary is a versioned documentation slug with ordinary HTML and Markdown static routes', () => {
  const load = sourceLoader();
  const content = load('components/docs/content.ts');
  assert.equal(content.isDocumentationSlug('glossary'), true);
  assert.deepEqual(content.documentationSlugs, ['overview', 'components', 'flows', 'boundaries', 'wallets', 'point-of-sale', 'glossary', 'resources']);
  assert.deepEqual(load('app/docs/[version]/[slug]/page.tsx').generateStaticParams()
    .filter(({ slug }) => slug === 'glossary'), [
    { version: 'released', slug: 'glossary' }, { version: 'coming-soon', slug: 'glossary' },
  ]);
  assert.equal(typeof load('app/docs/[version]/[slug]/markdown/route.ts').GET, 'function');
});

function glossaryFixture() {
  const content = sourceLoader()('components/docs/content.ts');
  const documents = Object.fromEntries(content.documentationVersions.map((version, index) => {
    const commit = (index ? 'b' : 'a').repeat(40);
    const core = content.documentationSlugs.filter((slug) => slug !== 'glossary').map((slug) => ({
      slug, title: `Synthetic ${slug}`, description: 'Synthetic fixture only.',
      group: slug === 'resources' ? 'Reference' : ['wallets', 'point-of-sale'].includes(slug) ? 'Integrate Paykit' : 'Understand Paykit',
      kind: slug === 'resources' ? 'reference' : ['wallets', 'point-of-sale'].includes(slug) ? 'tutorial' : 'explanation',
      sections: [{ id: 'fixture', title: 'Synthetic fixture', paragraphs: ['Unchanged core fixture.'], evidence: [] }], sourceLinks: [],
    }));
    const glossary = { slug: 'glossary', title: 'Synthetic glossary', description: 'Synthetic terms, not product claims.',
      group: 'Reference', kind: 'reference',
      sections: ['Alpha', 'Beta'].map((title) => ({ id: title.toLowerCase(), title,
        paragraphs: [`${title} definition for the ${version} synthetic snapshot.`], evidence: [`${version}-${title}`],
        links: [{ label: 'Related synthetic guide', href: `/docs/${version}/overview#fixture` }],
      })), sourceLinks: [{ label: 'Synthetic original context', href: `https://example.invalid/${commit}/terms.txt#L1-L2` }],
    };
    return [version, { lastUpdated: index ? '2026-11-19' : '2026-11-18',
      version: { commit, label: `Synthetic ${version}`, tag: `fixture-${index}`, sourceUrl: `https://example.invalid/${commit}`,
        readiness: { notice: 'Synthetic readiness qualification.', sourceUrl: `https://example.invalid/${commit}/readiness` }, packages: {} },
      pages: [...core, glossary] }];
  }));
  return { documents, load: sourceLoader({ [path.resolve('components/docs/content.ts')]: {
    ...content, getDocumentation: async (version) => documents[version],
  } }) };
}

test('generated glossary uses the Reference navigation and page kind through the real versioned route', async () => {
  const { load, documents } = glossaryFixture();
  for (const version of ['released', 'coming-soon']) {
    const params = Promise.resolve({ version, slug: 'glossary' });
    const tree = load('app/docs/layout.tsx').default({ children: await load('app/docs/[version]/[slug]/page.tsx').default({ params }) });
    const html = renderToStaticMarkup(tree);
    assert.match(html, /<h2>Reference<\/h2>[\s\S]*?aria-current="page">Synthetic glossary<\/a>/);
    assert.match(html, /data-testid="docs-page-kind">Reference<\/p>/);
    for (const other of ['released', 'coming-soon']) assert.ok(html.includes(`href="/docs/${other}/glossary"`));
    assert.ok(html.includes(documents[version].version.commit));
    assert.ok(html.includes(`Covers Paykit ${documents[version].version.tag}`));
    assert.ok(html.includes(`dateTime="${documents[version].lastUpdated}"`));
    assert.ok(html.includes('Synthetic readiness qualification.'));
    for (const term of ['alpha', 'beta']) {
      assert.ok(html.includes(`href="#${term}"`));
      assert.ok(html.includes(`<h2 id="${term}-title">`));
      assert.ok(html.includes(`<section id="${term}"`));
    }
    const response = await load('app/docs/[version]/[slug]/markdown/route.ts').GET(new Request('https://example.invalid'), { params });
    assert.equal(response.status, 200);
    const markdown = await response.text();
    const observed = spawnSync('python', ['-B', 'tests/docs-markdown-observe.py'], {
      input: JSON.stringify([html, marked.parse(markdown)]), encoding: 'utf8',
    });
    assert.equal(observed.status, 0, observed.stderr);
    const [expected, actual] = JSON.parse(observed.stdout);
    assert.deepEqual(actual, expected, `${version} glossary full rendered Markdown parity`);
  }
});

test('newest-release selector is accepted as policy without becoming a displayed source identity', async () => {
  const config = { schemaVersion: 1, source: { repository: 'pubky/paykit-rs', selection: 'newest-release' }, defaultVersion: 'coming-soon' };
  const load = sourceLoader({ [path.resolve('docs-generation.json')]: config });
  const content = load('components/docs/content.ts');
  assert.equal(content.defaultDocumentationVersion, 'coming-soon');
  const document = await content.getDocumentation('coming-soon');
  const html = renderToStaticMarkup(await load('app/docs/[version]/[slug]/page.tsx').default({
    params: Promise.resolve({ version: 'coming-soon', slug: 'overview' }),
  }));
  assert.ok(html.includes(document.version.commit));
  assert.ok(html.includes(`Covers Paykit ${document.version.tag ?? document.version.ref ?? document.version.label}`));
  assert.ok(!html.includes('newest-release'));
  const index = await (await load('app/llms.txt/route.ts').GET()).text();
  assert.ok(index.includes(document.version.commit));
  assert.ok(!index.includes('newest-release'));
});

test('selector policy fails closed while legacy fixed-ref fixtures retain their literal validation', () => {
  const config = { schemaVersion: 1, defaultVersion: 'coming-soon' };
  const loadConfig = (source) => sourceLoader({ [path.resolve('docs-generation.json')]: { ...config, source } })('components/docs/content.ts');
  for (const kind of ['branch', 'tag']) {
    assert.equal(loadConfig({ repository: 'pubky/paykit-rs', kind, ref: 'synthetic/fixture-v1' }).defaultDocumentationVersion, 'coming-soon');
    for (const ref of ['bad..ref', '.hidden', 'refs/tags/v1', 'a//b', 'a.lock', 'v1\n', 'v1.', '']) {
      assert.throws(() => loadConfig({ repository: 'pubky/paykit-rs', kind, ref }), /Invalid docs-generation/);
    }
  }
  for (const source of [
    { repository: 'synthetic/other', selection: 'newest-release' },
    { repository: 'pubky/paykit-rs', selection: 'latest' },
    { repository: 'pubky/paykit-rs', selection: 'newest-release', ref: 'v1' },
    { repository: 'pubky/paykit-rs', selection: 'newest-release', kind: 'tag' },
    { repository: 'pubky/paykit-rs', ref: 'v1', kind: 'unknown' },
    { repository: 'pubky/paykit-rs' },
  ]) assert.throws(() => loadConfig(source), /Invalid docs-generation/);
});

test('glossary llms entries expose the actual version-specific Markdown routes, titles and source commits', async () => {
  const { load, documents } = glossaryFixture();
  const text = await (await load('app/llms.txt/route.ts').GET()).text();
  for (const version of ['released', 'coming-soon']) {
    assert.ok(text.includes(documents[version].version.commit));
    assert.ok(marked.parse(text).includes(`<a href="/docs/${version}/glossary/markdown">Synthetic glossary</a>`));
  }
});

test('rendering glossary never mutates the six core page bodies or either snapshot metadata', async () => {
  const { load, documents } = glossaryFixture();
  const before = structuredClone(documents);
  for (const version of ['released', 'coming-soon']) {
    await load('app/docs/[version]/[slug]/page.tsx').default({ params: Promise.resolve({ version, slug: 'glossary' }) });
    await load('app/docs/[version]/[slug]/markdown/route.ts').GET(new Request('https://example.invalid'), {
      params: Promise.resolve({ version, slug: 'glossary' }),
    });
  }
  await load('app/llms.txt/route.ts').GET();
  assert.deepEqual(documents, before);
});

test('both generated corpora ship alphabetical source-mapped glossary terms with related version-local guides', () => {
  const coreSlugs = ['overview', 'components', 'flows', 'boundaries', 'wallets', 'point-of-sale'];
  for (const version of ['released', 'coming-soon']) {
    const document = JSON.parse(readFileSync(`content/docs/${version}.json`, 'utf8'));
    const rows = JSON.parse(readFileSync(`automation/evidence/${version}.json`, 'utf8'));
    const evidence = new Map(rows.map((row) => [row.id, row]));
    assert.deepEqual(document.pages.map((page) => page.slug), [...coreSlugs, 'glossary', 'resources']);
    const page = document.pages.find((page) => page.slug === 'glossary');
    assert.equal(page.group, 'Reference');
    assert.equal(page.kind, 'reference');
    assert.ok(page.sections.length > 1, 'a real glossary provides navigable term sections');
    const titles = page.sections.map((section) => section.title.toLowerCase());
    assert.deepEqual(titles, [...titles].sort());
    assert.equal(new Set(titles).size, titles.length);
    assert.equal(new Set(page.sections.map((section) => section.id)).size, page.sections.length);
    assert.match(document.lastUpdated, /^\d{4}-\d{2}-\d{2}$/);
    assert.ok(page.sourceLinks.length > 0);
    for (const link of page.sourceLinks) assert.ok(link.href.includes(document.version.commit));
    for (const section of page.sections) {
      assert.match(section.id, /^[a-z][a-z0-9-]*$/);
      assert.ok(section.paragraphs?.some((text) => text.trim()), `${section.title} needs its generated definition`);
      assert.ok(section.evidence.length > 0);
      for (const key of section.evidence) {
        const row = evidence.get(key);
        assert.ok(row, `${version}/${section.id}: ${key}`);
        assert.equal(row.version, version);
        assert.equal(row.commit, document.version.commit);
      }
      assert.ok(section.links?.some(({ href }) => {
        const target = new URL(href, 'https://example.invalid');
        const slug = target.pathname.split('/').at(-1);
        if (target.origin !== 'https://example.invalid' || !coreSlugs.includes(slug) || target.pathname !== `/docs/${version}/${slug}`) return false;
        if (!target.hash) return true;
        const related = document.pages.find((entry) => entry.slug === slug);
        return related.sections.some((entry) => [entry.id, `${entry.id}-title`].includes(target.hash.slice(1)));
      }), `${version}/${section.id} needs a related guide in the same snapshot`);
    }
  }
});
