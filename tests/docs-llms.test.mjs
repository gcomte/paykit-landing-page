import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { marked } from 'marked';
import { sourceLoader, observations } from './docs-markdown.test.mjs';

test('llms guide follows proposal v2 with compact generated technical value', async () => {
  const load = sourceLoader();
  const response = await load('app/llms.txt/route.ts').GET();
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('content-type'), 'text/plain; charset=utf-8');
  assert.equal(response.headers.get('x-content-type-options'), 'nosniff');
  const text = await response.text();
  assert.match(text, /^# Paykit\n\n> [^\n]+\n\n/);
  // 8 KB is this project's usefulness budget, not a proposal requirement.
  assert.ok(Buffer.byteLength(text) < 8192, 'keep detail behind links');
  const tokens = marked.lexer(text);
  assert.equal(tokens.filter((t) => t.type === 'heading' && t.depth === 1).length, 1);
  assert.ok(tokens.filter((t) => t.type === 'heading').every((t) => t.depth <= 2));
  const firstList = tokens.findIndex((t) => t.type === 'heading' && t.depth === 2);
  const guidance = marked.parser(tokens.slice(0, firstList));
  const content = load('components/docs/content.ts');
  const versions = [content.defaultDocumentationVersion, ...content.documentationVersions.filter((v) => v !== content.defaultDocumentationVersion)];
  const [index] = observations([marked.parse(text)]);
  const targets = ['/docs/index.md'];
  let previous = -1;
  for (const version of versions) {
    const doc = await content.getDocumentation(version);
    for (const value of [doc.version.label, doc.version.commit, doc.lastUpdated]) {
      const [observed] = observations([guidance]);
      assert.ok(observed.text.includes(value), `interpretation guidance includes ${value}`);
    }
    assert.ok(guidance.includes(doc.version.sourceUrl));
    assert.ok(!guidance.includes(doc.version.readiness.sourceUrl));
    assert.ok(!index.text.includes(doc.version.readiness.notice));
    const section = index.headings.findIndex(([level, title]) => level === 2 && title.includes(doc.version.label));
    assert.ok(section > previous, 'default documented snapshot comes first');
    previous = section;
    for (const page of doc.pages) {
      const target = `/docs/${version}/${page.slug}/index.md`;
      targets.push(target);
      assert.ok(index.links.some(([href, title]) => href === target && title === page.title));
      assert.ok(index.text.includes(page.description), `${version}/${page.slug} generated description`);
    }
  }
  assert.deepEqual(index.links.filter(([href]) => href.startsWith('/')).map(([href]) => href), targets);
  for (const list of tokens.filter((t) => t.type === 'list')) {
    for (const item of list.items) assert.match(item.text, /^\[[^\n]+\]\([^)]+\): .+/, 'useful link notes');
  }
  assert.doesNotMatch(text, /View as Markdown|Pay\. Get paid\.|Good payments start|Get in touch|mailto:|\/api\/calendar|youtube|copyright|llms-full|\]\(\/markdown\)/i);
});

test('root index.md is the same compact guide with Markdown response semantics', async () => {
  const load = sourceLoader();
  const response = await load('app/index.md/route.ts').GET();
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('content-type'), 'text/markdown; charset=utf-8');
  assert.equal(response.headers.get('x-content-type-options'), 'nosniff');
  assert.equal(await response.text(), await (await load('app/llms.txt/route.ts').GET()).text());
  assert.equal(await (await load('app/index.md/route.ts').GET()).text(), await load('components/docs/llms.ts').documentationGuide());
});

test('docs index.md aliases preserve exact existing bodies, version, date, source and 404 behavior', async () => {
  const load = sourceLoader();
  const entry = await load('app/docs/index.md/route.ts').GET();
  const legacyEntry = await load('app/docs/markdown/route.ts').GET();
  assert.equal(entry.status, legacyEntry.status);
  assert.deepEqual([...entry.headers], [...legacyEntry.headers]);
  assert.equal(await entry.text(), await legacyEntry.text());
  const content = load('components/docs/content.ts');
  const alias = load('app/docs/[version]/[slug]/index.md/route.ts');
  const legacy = load('app/docs/[version]/[slug]/markdown/route.ts');
  for (const version of content.documentationVersions) {
    const doc = await content.getDocumentation(version);
    for (const page of doc.pages) {
      const args = [new Request(`https://example.invalid/docs/${version}/${page.slug}/index.md`), { params: Promise.resolve({ version, slug: page.slug }) }];
      const response = await alias.GET(...args);
      const original = await legacy.GET(...args);
      assert.equal(response.status, 200);
      assert.deepEqual([...response.headers], [...original.headers]);
      const body = await response.text();
      assert.equal(body, await original.text(), `${version}/${page.slug} exact compatibility`);
      const [observed] = observations([marked.parse(body)]);
      assert.ok(observed.text.includes(`Covers Paykit ${doc.version.tag ?? doc.version.ref ?? doc.version.label}`));
      const date = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' })
        .format(new Date(`${doc.lastUpdated}T00:00:00Z`));
      assert.ok(observed.text.includes(date));
      assert.doesNotMatch(observed.text, /Source commit|View exact source/);
      for (const link of page.sourceLinks) assert.ok(observed.links.some(([href]) => href === link.href));
    }
  }
  for (const [version, slug] of [['unknown', 'overview'], ['released', '../wallets'], ['coming-soon', 'not-a-page']]) {
    await assert.rejects(alias.GET(new Request('https://example.invalid'), { params: Promise.resolve({ version, slug }) }), /404/);
  }
});

test('HTTP discovery uses later same-origin alternate rules without replacing global describedby', async () => {
  const rules = await sourceLoader()('next.config.ts').default.headers();
  const describedby = '</llms.txt>; rel="describedby"';
  const global = rules.find((rule) => rule.source === '/:path*');
  assert.ok(global.headers.some((header) => header.key === 'Link' && header.value === describedby));
  for (const [source, target] of [['/', '/index.md'], ['/docs', '/docs/index.md'], ['/docs/:version/:slug', '/docs/:version/:slug/index.md']]) {
    const specific = rules.find((rule) => rule.source === source);
    assert.ok(specific, `${source} discovery rule`);
    assert.ok(rules.indexOf(specific) > rules.indexOf(global), 'later matching Link header takes precedence in Next');
    assert.deepEqual(specific.headers, [{ key: 'Link', value: `<${target}>; rel="alternate"; type="text/markdown", ${describedby}` }]);
  }
  const links = rules.flatMap((rule) => rule.headers.filter((header) => header.key === 'Link'));
  assert.equal(links.length, 4);
  assert.ok(links.every(({ value }) => !value.includes('https:') && !value.includes('http:')));
});

test('future generated catalogue metadata stays literal without a maintained parallel copy', async () => {
  const contentPath = path.resolve('components/docs/content.ts');
  const content = sourceLoader()(contentPath);
  const fixtures = Object.fromEntries(await Promise.all(content.documentationVersions.map(async (version) => {
    const doc = structuredClone(await content.getDocumentation(version));
    const literal = `${version}: [not](https://example.invalid/injected) *literal* ~~only~~ &copy; <tag> \\text`;
    doc.version.label = `Synthetic label ${literal}`;
    doc.version.readiness.notice = `Synthetic readiness ${literal}`; // Omitted metadata is a negative control.
    doc.version.commit = (version === 'released' ? 'c' : 'd').repeat(40);
    doc.version.sourceUrl = `https://example.invalid/${version}/${doc.version.commit}`;
    doc.lastUpdated = version === 'released' ? '2026-01-02' : '2026-01-03';
    doc.pages[0].title = `Synthetic title ${literal}`;
    doc.pages[0].description = `Synthetic description ${literal}`;
    return [version, doc];
  })));
  const load = sourceLoader({ [contentPath]: { ...content, getDocumentation: async (version) => fixtures[version] } });
  const body = await (await load('app/llms.txt/route.ts').GET()).text();
  const [observed] = observations([marked.parse(body)]);
  for (const [version, doc] of Object.entries(fixtures)) {
    assert.ok(observed.text.includes(doc.version.label));
    assert.ok(!observed.text.includes(doc.version.readiness.notice));
    assert.ok(observed.text.includes(doc.version.commit));
    assert.ok(observed.text.includes(doc.lastUpdated));
    assert.ok(observed.links.some(([href]) => href === doc.version.sourceUrl));
    assert.ok(observed.text.includes(doc.pages[0].description));
    assert.ok(observed.links.some(([href, title]) => href === `/docs/${version}/overview/index.md` && title === doc.pages[0].title));
  }
  assert.ok(!observed.links.some(([href]) => href === 'https://example.invalid/injected'));
  assert.doesNotMatch(marked.parse(body), /<del>|<tag>/);
  assert.equal(await (await load('app/index.md/route.ts').GET()).text(), body);
});


test('compact guide omits global readiness boilerplate without losing snapshot provenance or catalogue', async () => {
  const load = sourceLoader();
  const body = await (await load('app/llms.txt/route.ts').GET()).text();
  const [observed] = observations([marked.parse(body)]);
  const content = load('components/docs/content.ts');
  for (const version of content.documentationVersions) {
    const doc = await content.getDocumentation(version);
    assert.ok(!observed.text.includes(doc.version.readiness.notice), `${version}: internal readiness is not guide prose`);
    assert.ok(!observed.links.some(([href]) => href === doc.version.readiness.sourceUrl), `${version}: no duplicated readiness attribution`);
    for (const value of [doc.version.label, doc.version.commit, doc.lastUpdated]) assert.ok(observed.text.includes(value));
    assert.ok(observed.links.some(([href]) => href === doc.version.sourceUrl));
    for (const page of doc.pages) {
      assert.ok(observed.links.some(([href, title]) => href === `/docs/${version}/${page.slug}/index.md` && title === page.title));
      assert.ok(observed.text.includes(page.description));
    }
  }
  assert.doesNotMatch(body, /production readiness|Readiness source|Documentation policy:/i);
  assert.equal(await (await load('app/index.md/route.ts').GET()).text(), body);
});
