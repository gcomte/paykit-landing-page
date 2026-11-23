import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { renderToStaticMarkup } from 'react-dom/server';
import { marked } from 'marked';
import { sourceLoader, renderedDocumentation, observations } from './docs-markdown.test.mjs';

const versions = ['released', 'coming-soon'];

test('Resources static routes cover both retained versions without weakening 404 guards', async () => {
  const load = sourceLoader();
  const content = load('components/docs/content.ts');
  assert.equal(content.isDocumentationSlug('resources'), true);
  const entry = load('app/docs/[version]/[slug]/page.tsx');
  const params = entry.generateStaticParams();
  assert.equal(params.length, 16);
  assert.equal(entry.dynamicParams, false);
  assert.deepEqual(params.filter(({ slug }) => slug === 'resources'), versions.map((version) => ({ version, slug: 'resources' })));
  assert.equal(typeof load('app/docs/[version]/[slug]/markdown/route.ts').GET, 'function');
  for (const [version, slug] of [['unknown', 'resources'], ['released', '../resources'], ['coming-soon', 'not-a-page']]) {
    await assert.rejects(entry.default({ params: Promise.resolve({ version, slug }) }), /404/);
    await assert.rejects(load('app/docs/[version]/[slug]/markdown/route.ts').GET(new Request('https://example.invalid'), {
      params: Promise.resolve({ version, slug }),
    }), /404/);
  }
});

function syntheticResources() {
  const content = sourceLoader()('components/docs/content.ts');
  const documents = Object.fromEntries(versions.map((version, index) => {
    const commit = (index ? 'b' : 'a').repeat(40);
    return [version, { lastUpdated: index ? '2026-11-19' : '2026-11-18',
      version: { commit, label: `Synthetic ${version}`, tag: `synthetic-${index}`, sourceUrl: `https://github.com/pubky/paykit-rs/tree/${commit}`,
        readiness: { notice: 'Synthetic qualification, not product advice.', sourceUrl: `https://github.com/pubky/paykit-rs/blob/${commit}/README.md` } },
      pages: content.documentationSlugs.map((slug) => ({ slug, title: slug === 'resources' ? 'Resources' : `Synthetic ${slug}`,
        description: 'Synthetic fixture, not product claims.', group: ['glossary', 'resources'].includes(slug) ? 'Reference' :
          ['wallets', 'point-of-sale'].includes(slug) ? 'Integrate Paykit' : 'Understand Paykit',
        kind: ['glossary', 'resources'].includes(slug) ? 'reference' : ['wallets', 'point-of-sale'].includes(slug) ? 'tutorial' : 'explanation',
        sections: ['Start here', 'Further context'].map((title, section) => ({ id: `synthetic-${section}`, title,
          paragraphs: [`Synthetic ${version} reading context.`], evidence: ['synthetic'], links: [{ label: 'Synthetic primary source',
            href: `https://github.com/pubky/paykit-rs/blob/${commit}/README.md#L1` }] })),
        sourceLinks: [{ label: 'Synthetic primary source', href: `https://github.com/pubky/paykit-rs/blob/${commit}/README.md#L1` }],
      })) }];
  }));
  return { documents, load: sourceLoader({ [path.resolve('components/docs/content.ts')]: {
    ...content, getDocumentation: async (version) => documents[version],
  } }) };
}

test('synthetic Resources uses real route navigation TOC versioned Markdown llms and persisted footer', async () => {
  const { load, documents } = syntheticResources();
  const before = structuredClone(documents);
  const index = observations([marked.parse(await (await load('app/llms.txt/route.ts').GET()).text())])[0];
  for (const version of versions) {
    const document = documents[version];
    const page = document.pages.find(({ slug }) => slug === 'resources');
    const html = renderToStaticMarkup(await renderedDocumentation(load, version, 'resources'));
    assert.match(html, /<h2>Reference<\/h2>[\s\S]*?aria-current="page">Resources<\/a>/);
    assert.match(html, /data-testid="docs-page-kind">Reference<\/p>/);
    assert.ok(html.includes(document.version.commit));
    assert.ok(!html.includes(document.version.readiness.notice));
    assert.doesNotMatch(html, /docs-version-marker|aria-label="Documentation source"|Source commit|View exact source/);
    const sources = html.match(/<nav aria-label="Page sources">([\s\S]*?)<\/nav>/)?.[1];
    for (const link of document.pages.find(({ slug }) => slug === 'resources').sourceLinks) {
      assert.ok(sources.includes(`href="${link.href}"`));
    }
    assert.ok(html.includes(`Covers Paykit ${document.version.tag}`));
    assert.ok(html.includes(`dateTime="${document.lastUpdated}"`));
    assert.ok(html.includes(`href="/docs/${version}/resources"`));
    assert.ok(!html.includes('aria-label="Documentation version"'));
    const route = `/docs/${version}/resources`;
    assert.ok(html.includes(`href="${route}/markdown"`));
    assert.ok(index.links.some(([href, title]) => href === `${route}/index.md` && title === 'Resources'));
    const response = await load('app/docs/[version]/[slug]/markdown/route.ts').GET(new Request(`https://example.invalid${route}/markdown`), {
      params: Promise.resolve({ version, slug: 'resources' }),
    });
    assert.equal(response.status, 200);
    const markdown = await response.text();
    const [expected, actual] = observations([html, marked.parse(markdown)]);
    assert.deepEqual(actual, expected, `${version}: full Resources HTML/Markdown parity`);
    for (const section of page.sections) {
      assert.ok(html.includes(`href="#${section.id}"`));
      assert.ok(html.includes(`<section id="${section.id}"`));
      assert.ok(html.includes(`<h2 id="${section.id}-title">${section.title}</h2>`));
      assert.ok(markdown.includes(`<a id="${section.id}"></a>`));
      for (const link of section.links) assert.ok(actual.links.some(([href]) => href === link.href));
    }
  }
  assert.deepEqual(documents, before, 'rendering must not mutate either snapshot');
});

test('both real generated Resources pages have commit-matched evidence and pinned primary source links', async () => {
  const load = sourceLoader();
  const llms = observations([marked.parse(await (await load('app/llms.txt/route.ts').GET()).text())])[0];
  for (const version of versions) {
    const document = JSON.parse(readFileSync(`content/docs/${version}.json`, 'utf8'));
    const blueprint = JSON.parse(readFileSync(`automation/blueprints/${version}.json`, 'utf8'));
    const evidence = new Map(JSON.parse(readFileSync(`automation/evidence/${version}.json`, 'utf8')).map((row) => [row.id, row]));
    const pages = document.pages.filter(({ slug }) => slug === 'resources');
    assert.equal(pages.length, 1, `${version}: generate exactly one actual Resources page`);
    const page = pages[0];
    assert.equal(document.pages.length, 8);
    assert.equal(page.title, 'Resources');
    assert.equal(page.group, 'Reference');
    assert.equal(page.kind, 'reference');
    assert.ok(page.sections.length > 1);
    assert.equal(blueprint.sourceCommit, document.version.commit);
    assert.deepEqual(page, blueprint.pages.find(({ slug }) => slug === 'resources'));
    assert.match(document.version.commit, /^[a-f0-9]{40}$/);
    assert.match(document.lastUpdated, /^\d{4}-\d{2}-\d{2}$/);
    const anchors = new Set(['docs-content', 'docs-title']);
    const titles = new Set();
    const pinned = `https://github.com/pubky/paykit-rs/blob/${document.version.commit}/`;
    const checkLink = ({ label, href }) => {
      assert.ok(label.trim());
      if (href.startsWith(pinned)) {
        const suffix = href.slice(pinned.length);
        assert.match(suffix, /^[A-Za-z0-9_.-]+(?:\/[A-Za-z0-9_.-]+)*(?:#L[1-9][0-9]*(?:-L[1-9][0-9]*)?)?$/);
        assert.ok(!suffix.split('#')[0].split('/').some((part) => ['.', '..'].includes(part)));
        return true;
      }
      const target = new URL(href, 'https://example.invalid');
      assert.equal(target.origin, 'https://example.invalid');
      assert.equal(target.search, '');
      const related = document.pages.find(({ slug }) => `/docs/${version}/${slug}` === target.pathname);
      assert.ok(related, `matching-version target: ${href}`);
      if (target.hash) assert.ok(['docs-content', 'docs-title', ...related.sections.flatMap(({ id }) => [id, `${id}-title`])].includes(target.hash.slice(1)));
      return false;
    };
    assert.ok(page.sourceLinks.length > 0);
    page.sourceLinks.forEach(checkLink);
    for (const section of page.sections) {
      assert.match(section.id, /^[a-z][a-z0-9-]*$/);
      assert.ok(!anchors.has(section.id) && !anchors.has(`${section.id}-title`));
      anchors.add(section.id); anchors.add(`${section.id}-title`);
      assert.ok(section.title.trim() && !titles.has(section.title.toLowerCase()));
      titles.add(section.title.toLowerCase());
      assert.ok(section.paragraphs?.length > 0 && section.paragraphs.every((text) => text.trim()));
      assert.ok(section.evidence.length > 0);
      for (const key of section.evidence) {
        const row = evidence.get(key);
        assert.ok(row, `${version}/${section.id}: ${key}`);
        assert.equal(row.version, version);
        assert.equal(row.commit, document.version.commit);
        assert.ok(row.path && row.excerpt);
        assert.match(row.sourceHash, /^[a-f0-9]{64}$/);
        assert.ok(Number.isInteger(row.startLine) && row.startLine >= 1 && row.endLine >= row.startLine);
      }
      assert.ok(section.links?.map(checkLink).some(Boolean), `${version}/${section.id}: primary snapshot link required`);
    }
    const html = renderToStaticMarkup(await renderedDocumentation(load, version, 'resources'));
    assert.ok(html.includes(`Covers Paykit ${document.version.tag ?? document.version.ref ?? document.version.label}`));
    assert.ok(html.includes(`dateTime="${document.lastUpdated}"`));
    assert.ok(html.includes(document.version.commit));
    assert.ok(html.includes(`href="/docs/${version}/resources/markdown"`));
    assert.ok(llms.links.some(([href, title]) => href === `/docs/${version}/resources/index.md` && title === page.title));
    const response = await load('app/docs/[version]/[slug]/markdown/route.ts').GET(new Request('https://example.invalid'), {
      params: Promise.resolve({ version, slug: 'resources' }),
    });
    assert.equal(response.status, 200);
    const [expected, actual] = observations([html, marked.parse(await response.text())]);
    assert.deepEqual(actual, expected, `${version}: real Resources full Markdown parity`);
  }
});
