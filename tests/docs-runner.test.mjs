import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
const require = createRequire(import.meta.url);
async function loadTs(file, dependencies) {
  const code = ts.transpileModule(readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const module = { exports: {} };
  await new (Object.getPrototypeOf(async function(){}).constructor)('require', 'module', 'exports', code)(
    (name) => Object.hasOwn(dependencies, name) ? dependencies[name] : require(name), module, module.exports);
  return module.exports;
}
const selectedDoc = { version: { label: 'v0.1.1-rc9', ref: 'v0.1.1-rc9', kind: 'tag', commit: 'b'.repeat(40), sourceUrl: 'https://github.com/pubky/paykit-rs/tree/' + 'b'.repeat(40), readiness: { notice: 'Synthetic operator caution', sourceUrl: 'https://example.invalid/readme' } }, pages: ['overview', 'flows', 'wallets', 'point-of-sale'].map((slug) => ({ slug, title: `Selected ${slug}`, description: `Selected description ${slug}`, group: 'Understand Paykit', sections: [], sourceLinks: [] })) };
const stableDoc = { ...selectedDoc, pages: selectedDoc.pages.map((page) => ({ ...page, title: `Stable ${page.slug}`, description: `Stable description ${page.slug}` })) };
const content = { defaultDocumentationVersion: 'coming-soon', getDocumentation: async (version) => version === 'coming-soon' ? selectedDoc : stableDoc, documentationPath: (version, slug) => `/docs/${version}/${slug}` };

test('default Docs entry and metadata use the selected source, not the older stable snapshot', async () => {
  const entry = await loadTs('app/docs/page.tsx', {
    '@/components/docs/content': content,
    '@/components/docs/metadata': { documentationMetadata: (page, route) => ({ title: page.title, description: page.description, route }) },
    '@/components/docs/DocsPage': ({ documentation, version, page }) => React.createElement('main', { 'data-doc-version': version }, page.title, documentation.version.label),
  });
  const html = renderToStaticMarkup(await entry.default());
  assert.match(html, /data-doc-version="coming-soon"/);
  assert.match(html, /Selected overview/);
  assert.equal((await entry.generateMetadata()).description, 'Selected description overview');
  assert.equal((await entry.generateMetadata()).route, '/docs');
});

test('homepage main and exactly three generated featured links follow selected documentation', async () => {
  const teaser = await loadTs('components/docs/DocsTeaser.tsx', {
    'server-only': {}, '../../automation/corrections.json': JSON.parse(readFileSync('automation/corrections.json', 'utf8')),
    './content': content, './DocsTeaser.module.css': {},
  });
  const html = renderToStaticMarkup(React.createElement(teaser.default));
  assert.match(html, /href="\/docs"/);
  assert.equal((html.match(/href="\/docs\/coming-soon\//g) ?? []).length, 3);
  assert.doesNotMatch(html, /\/docs\/released\//);
  for (const slug of ['flows', 'wallets', 'point-of-sale']) {
    assert.ok(html.includes(`href="/docs/coming-soon/${slug}"`));
    assert.ok(html.includes(`Selected ${slug}`));
    assert.ok(html.includes(`Selected description ${slug}`));
  }
});

test('version chrome identifies selected tag truthfully and retains the separate stable view', async () => {
  const page = await loadTs('components/docs/DocsPage.tsx', { './content': content, './docs.module.css': {} });
  const html = renderToStaticMarkup(React.createElement(page.default, { documentation: selectedDoc, page: selectedDoc.pages[0], version: 'coming-soon' }));
  assert.match(html, /Selected source/);
  assert.match(html.replace(/<[^>]*>/g, ''), /Tag: v0\.1\.1-rc9/);
  assert.match(html, /href="\/docs\/released\/overview"/);
  assert.match(html, /href="\/docs\/coming-soon\/overview"/);
  assert.match(html, /Synthetic operator caution/);
  assert.doesNotMatch(html, /Unreleased development version|Coming soon/);
});

test('server selector rejects unsafe literals rather than normalizing or falling back', async () => {
  const selector = JSON.parse(readFileSync('docs-generation.json', 'utf8'));
  const dependencies = { 'server-only': {}, react: { cache: (fn) => fn }, '../../docs-generation.json': selector };
  const valid = await loadTs('components/docs/content.ts', dependencies);
  assert.equal(valid.defaultDocumentationVersion, 'coming-soon');
  for (const ref of ['bad..ref', 'a.lock', 'path/.hidden', 'refs/tags/v0.1.1-rc9', 'v0.1.1-rc9\n', 'a//b']) {
    await assert.rejects(loadTs('components/docs/content.ts', { ...dependencies, '../../docs-generation.json': { ...selector, source: { ...selector.source, ref } } }), /Invalid docs-generation/);
  }
});

test('browser checks cannot silently reuse a stale local production server', () => {
  const configuration = readFileSync('playwright.config.ts', 'utf8');
  const reuse = configuration.match(/reuseExistingServer:\s*([^,\n]+)/);
  assert.ok(reuse, 'the runner must state its server ownership policy');
  assert.equal(reuse[1].trim(), 'false', 'always start the tested build or fail on an occupied port');
});
