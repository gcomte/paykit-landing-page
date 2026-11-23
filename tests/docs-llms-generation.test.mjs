import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import { sourceLoader } from './docs-markdown.test.mjs';

test('llms representations include the persisted generation date of every consumed snapshot', async () => {
  const contentPath = path.resolve('components/docs/content.ts');
  const content = sourceLoader()(contentPath);
  const snapshots = Object.fromEntries(await Promise.all(content.documentationVersions.map(async (version, index) => {
    const doc = structuredClone(await content.getDocumentation(version));
    doc.lastUpdated = index ? '2024-06-07' : '2024-03-04';
    return [version, doc];
  })));
  const load = sourceLoader({ [contentPath]: { ...content, getDocumentation: async (version) => snapshots[version] } });
  for (const route of ['app/llms.txt/route.ts', 'app/index.md/route.ts']) {
    const body = await (await load(route).GET()).text();
    for (const snapshot of Object.values(snapshots)) assert.ok(body.includes(snapshot.lastUpdated), `${route}: persisted date ${snapshot.lastUpdated}, never request-time freshness`);
  }
});

import { readFileSync } from 'node:fs';
import * as filesystem from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { tmpdir } from 'node:os';
import { renderToStaticMarkup } from 'react-dom/server';
import { marked } from 'marked';
import { observations } from './docs-markdown.test.mjs';

const regenerationRequirement = 'of course everything including the llms.txt must always be regenerated whenever the docs are regenerated';

test('active import rules require automatic same-snapshot regeneration of every derived representation', async (t) => {
  const corrections = JSON.parse(readFileSync('automation/corrections.json', 'utf8'));
  const correction = corrections.requirements.find(({ id }) => id === 'llms-txt-valuable-guide-v2');
  assert.ok(correction, 'preserve the current superseding correction');
  const policies = [['llms-txt-valuable-guide-v2', correction.requirement],
    ...['automation/COORDINATOR.md', 'automation/WEEKLY.md', 'automation/skills/writing.md', 'automation/skills/evaluation.md']
      .map((file) => [file, readFileSync(file, 'utf8')])];
  for (const [name, policy] of policies) await t.test(name, () => {
    assert.ok(policy.includes(regenerationRequirement), 'persist the exact user requirement');
    for (const clause of [
      /all derived HTML\/Markdown representations, including llms\.txt, \/index\.md and per-page Markdown/,
      /same generated snapshot\/build on every actual docs regeneration/,
      /no manual copy or independent updater/,
      /version\/source\/date\/title\/description/,
      /no-change skips remain skips/,
      /identical regenerated inputs may yield identical bytes/,
      /stored generation dates remain truthful, never build\/request freshness/,
      /older snapshots remain unchanged/,
      /real producer-to-reader regression/,
    ]) assert.match(policy, clause, `missing generation/import contract in ${name}`);
  });
  assert.ok(correction.enforcedBy.includes('tests/docs-llms-generation.test.mjs'));
});

// Synthetic mechanics only: the existing six-page refresh fixture packs recipes
// without network/Cargo/SDK execution. Only the retained release uses public bytes.
const refreshFixture = String.raw`
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

site = Path(sys.argv[1])
sys.path.insert(0, str(site / 'automation/tests'))
from test_refresh_development import DevelopmentRefreshTests, OLD, NEW, SLUGS

class FixedDay(datetime):
    instant = datetime(2024, 2, 3, 0, 5, tzinfo=timezone.utc)
    @classmethod
    def now(cls, tz=None):
        assert tz is timezone.utc
        return cls.instant

def raw(file):
    return file.read_bytes().decode('utf-8')

def put(file, value):
    file.write_bytes((json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode('utf-8'))

def enrich(pages, phase, commit):
    for page in pages:
        slug = page['slug']
        page.update(title=f'Synthetic {phase} {slug}',
                    description=f'Synthetic {phase} {slug} — fixture metadata only.',
                    group='Integrate Paykit' if slug in ('wallets', 'point-of-sale') else 'Understand Paykit',
                    kind='tutorial' if slug in ('wallets', 'point-of-sale') else 'explanation',
                    sourceLinks=[{'label': 'Synthetic source', 'href': f'https://github.com/pubky/paykit-rs/tree/{commit}'}])
        for section in page['sections']:
            section['title'] = f'Synthetic {phase} {slug} section'

def state(root):
    return {str(p.relative_to(root)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            for p in root.rglob('*') if p.is_file()}

case = DevelopmentRefreshTests()
try:
    root, sources, producer = case.fixture()
    document_path = root / 'content/docs/coming-soon.json'
    release_path = root / 'content/docs/released.json'
    released = (site / 'content/docs/released.json').read_bytes()
    release_path.write_bytes(released)
    release_stat = release_path.stat().st_mtime_ns
    before = json.loads(raw(document_path))
    old_source = f'https://github.com/pubky/paykit-rs/tree/{OLD}'
    before['version'].update(label='synthetic-before', tag='synthetic-before', ref='synthetic-before',
        kind='tag', qualifiedRef='refs/tags/synthetic-before', sourceUrl=old_source,
        readiness={'notice': 'Synthetic previous readiness only.', 'sourceUrl': old_source + '/README.md',
                   'attribution': 'documentation-policy'})
    before['lastUpdated'] = '2024-01-02'
    enrich(before['pages'], 'before', OLD)
    put(document_path, before)
    blueprint_path = root / 'automation/blueprints/coming-soon.json'
    blueprint = json.loads(raw(blueprint_path))
    enrich(blueprint['pages'], 'refreshed', NEW)
    put(blueprint_path, blueprint)
    inputs = {'sourceIndex': raw(sources / 'index.json'), 'blueprint': raw(blueprint_path),
              'sourceFiles': {str(p.relative_to(sources)): raw(p) for p in sources.rglob('*') if p.is_file()}}
    before_raw = raw(document_path)
    with patch.object(producer, 'datetime', FixedDay):
        first = producer.refresh_development(root, sources)
        after_raw = raw(document_path)
        first_state = state(root)
        repeat = producer.refresh_development(root, sources)
        assert repeat['changedFiles'] == []
        assert state(root) == first_state, 'same-day recomputation must not write any fixture output'
        FixedDay.instant = datetime(2024, 2, 4, 0, 5, tzinfo=timezone.utc)
        next_day = producer.refresh_development(root, sources)
        next_raw = raw(document_path)
        next_state = state(root)
        repeat_next_day = producer.refresh_development(root, sources)
        assert repeat_next_day['changedFiles'] == []
        assert state(root) == next_state
    assert release_path.read_bytes() == released
    assert release_path.stat().st_mtime_ns == release_stat
    payload = {'scope': 'synthetic six-page producer/reader regression; no SDK/network execution',
               'inputs': inputs, 'before': before_raw, 'after': after_raw, 'nextDay': next_raw,
               'released': released.decode('utf-8'),
               'results': {'first': first, 'repeat': repeat, 'nextDay': next_day, 'repeatNextDay': repeat_next_day}}
    payload['sha256'] = {name: hashlib.sha256(payload[name].encode('utf-8')).hexdigest()
                         for name in ('before', 'after', 'nextDay', 'released')}
    # Preserve exact generated strings in stdout before TemporaryDirectory cleanup.
    print(json.dumps(payload, ensure_ascii=False))
finally:
    case.doCleanups()
`;

const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex');

test('real refresh producer bytes automatically feed the same real reader, guide and Markdown/HTML handlers', async () => {
  const processResult = spawnSync('python', ['-B', '-c', refreshFixture, process.cwd()], {
    encoding: 'utf8', timeout: 30_000, maxBuffer: 4 * 1024 * 1024,
  });
  assert.equal(processResult.error, undefined);
  assert.equal(processResult.status, 0, processResult.stderr);
  const produced = JSON.parse(processResult.stdout);
  const fixture = await filesystem.mkdtemp(path.join(tmpdir(), 'paykit-docs-regeneration-'));
  const evidence = { producer: produced, views: [], reads: [] };
  try {
    const actualPaths = new Map(['released', 'coming-soon'].map((version) => [
      path.resolve('content/docs', `${version}.json`), path.join(fixture, `${version}.json`),
    ]));
    const releasedBytes = Buffer.from(produced.released, 'utf8');
    assert.equal(sha256(releasedBytes), produced.sha256.released);
    assert.deepEqual(releasedBytes, readFileSync('content/docs/released.json'));
    await filesystem.writeFile(actualPaths.get(path.resolve('content/docs/released.json')), releasedBytes);
    // Override only readFile for the two known content paths. In particular,
    // getDocumentation, React.cache, the guide, routes and views remain real.
    const load = sourceLoader({ 'node:fs/promises': { ...filesystem, async readFile(file, ...args) {
      const redirected = actualPaths.get(file);
      if (!redirected) return filesystem.readFile(file, ...args);
      const value = await filesystem.readFile(redirected, ...args);
      evidence.reads.push({ file, sha256: sha256(value) });
      return value;
    } } });
    const content = load('components/docs/content.ts');
    const reader = content.getDocumentation;
    const guide = load('components/docs/llms.ts').documentationGuide;
    const handlers = Object.fromEntries(Object.entries({
      llms: 'app/llms.txt/route.ts', index: 'app/index.md/route.ts',
      entryAlias: 'app/docs/index.md/route.ts', entryLegacy: 'app/docs/markdown/route.ts',
      articleAlias: 'app/docs/[version]/[slug]/index.md/route.ts',
      articleLegacy: 'app/docs/[version]/[slug]/markdown/route.ts',
    }).map(([name, file]) => [name, load(file).GET]));
    const entry = load('app/docs/page.tsx');
    const article = load('app/docs/[version]/[slug]/page.tsx');
    const layout = load('app/docs/layout.tsx').default;
    const initial = JSON.parse(produced.before);
    const retained = JSON.parse(produced.released);
    assert.equal(content.defaultDocumentationVersion, 'coming-soon');
    assert.equal(initial.pages.length, 6);
    for (const stage of ['before', 'after', 'nextDay']) {
      const bytes = Buffer.from(produced[stage], 'utf8');
      assert.equal(sha256(bytes), produced.sha256[stage], 'transport exact producer UTF8 bytes');
      await filesystem.writeFile(actualPaths.get(path.resolve('content/docs/coming-soon.json')), bytes);
      const document = JSON.parse(produced[stage]);
      assert.equal(content.getDocumentation, reader, 'same loaded real cache-wrapped reader');
      assert.deepEqual(await reader('coming-soon'), document);
      assert.deepEqual(await reader('released'), retained);
      const params = () => ({ params: Promise.resolve({ version: 'coming-soon', slug: 'overview' }) });
      const request = new Request('https://example.invalid/docs/coming-soon/overview/index.md');
      const bodies = {};
      for (const [name, handler] of Object.entries(handlers)) {
        const response = await handler(request, params());
        assert.equal(response.status, 200, `${stage}: ${name}`);
        bodies[name] = await response.text();
      }
      assert.equal(bodies.llms, await guide(), 'same loaded actual builder');
      assert.equal(bodies.index, bodies.llms);
      assert.equal(bodies.entryAlias, bodies.entryLegacy);
      assert.equal(bodies.articleAlias, bodies.articleLegacy);
      const html = renderToStaticMarkup(layout({ children: await entry.default() }));
      const articleHtml = renderToStaticMarkup(layout({ children: await article.default(params()) }));
      const [catalogue, entryView, entryMarkdown, articleView, articleMarkdown] = observations([
        marked.parse(bodies.llms), html, marked.parse(bodies.entryAlias), articleHtml, marked.parse(bodies.articleAlias),
      ]);
      const view = { stage, bodies, html, articleHtml };
      evidence.views.push(view);
      assert.deepEqual(entryMarkdown, entryView, `${stage}: default HTML/Markdown parity`);
      assert.deepEqual(articleMarkdown, articleView, `${stage}: versioned HTML/Markdown parity`);
      for (const snapshot of [document, retained]) {
        for (const value of [snapshot.version.label, snapshot.version.commit, snapshot.lastUpdated]) {
          assert.ok(catalogue.text.includes(value), `${stage}: guide consumes persisted ${value}`);
        }
        assert.ok(catalogue.links.some(([href]) => href === snapshot.version.sourceUrl));
        assert.ok(!catalogue.text.includes(snapshot.version.readiness.notice), 'readiness remains internal after real generation');
        assert.ok(!catalogue.links.some(([href]) => href === snapshot.version.readiness.sourceUrl));
        for (const page of snapshot.pages) {
          assert.ok(catalogue.text.includes(page.description), `${stage}: description ${page.slug}`);
          assert.ok(catalogue.links.some(([href, label]) => href === `/docs/${snapshot === document ? 'coming-soon' : 'released'}/${page.slug}/index.md` && label === page.title));
        }
      }
      assert.ok(catalogue.text.indexOf(document.version.label) < catalogue.text.indexOf(retained.version.label), 'default snapshot first');
      const overview = document.pages.find(({ slug }) => slug === 'overview');
      const humanDate = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' })
        .format(new Date(`${document.lastUpdated}T00:00:00Z`));
      const covered = document.version.tag ?? document.version.ref ?? document.version.label;
      for (const output of [entryView, articleView]) {
        for (const value of [overview.title, overview.description, overview.sections[0].title,
          `Covers Paykit ${covered}`, humanDate]) {
          assert.ok(output.text.includes(value), `${stage}: technical view consumes ${value}`);
        }
        assert.ok(output.links.some(([href, label]) => href === 'https://github.com/pubky/paykit-rs/releases' && label === covered), `${stage}: technical view links its version to GitHub Releases`);
        for (const link of overview.sourceLinks) {
          assert.ok(output.links.some(([href]) => href === link.href), `${stage}: technical view consumes page source ${link.href}`);
        }
        assert.doesNotMatch(output.text, /Source commit|View exact source/);
        assert.ok(!output.text.includes(document.version.readiness.notice));
      }
      // React SSR spells the HTML attribute dateTime; HTML names are case-insensitive.
      for (const output of [html, articleHtml]) {
        assert.equal(output.match(/<time\b[^>]*\bdatetime="([^"]+)"/i)?.[1], document.lastUpdated);
      }
      const metadata = await entry.generateMetadata();
      assert.equal(metadata.title, `${overview.title} | Paykit Docs`);
      assert.equal(metadata.description, overview.description);
      if (stage !== 'before') {
        for (const stale of [initial.version.label, initial.version.commit, initial.version.sourceUrl,
          initial.lastUpdated, initial.pages[0].title, initial.pages[0].description]) {
          assert.ok(!catalogue.text.includes(stale) && !bodies.llms.includes(stale), `${stage}: no stale default guide identity ${stale}`);
          assert.ok(!entryView.text.includes(stale), `${stage}: no stale technical view identity ${stale}`);
        }
      }
      assert.deepEqual(await filesystem.readFile(actualPaths.get(path.resolve('content/docs/released.json'))), releasedBytes);
    }
    const first = JSON.parse(produced.after);
    assert.equal(first.version.ref, 'v-test');
    assert.equal(first.version.commit, 'b'.repeat(40));
    assert.equal(first.lastUpdated, '2024-02-03');
    assert.ok(first.pages[4].sections[0].code[0].code.includes('b'.repeat(40)), 'actual producer expanded the recipe artifact');
    assert.ok(produced.results.first.changedFiles.includes('content/docs/coming-soon.json'));
    assert.deepEqual(produced.results.repeat.changedFiles, []);
    assert.deepEqual(produced.results.repeatNextDay.changedFiles, []);
    const nextDay = JSON.parse(produced.nextDay);
    assert.equal(nextDay.lastUpdated, '2024-02-04');
    assert.deepEqual({ ...nextDay, lastUpdated: first.lastUpdated }, first, 'same-input next-day regeneration changes only the persisted day');
    assert.deepEqual(produced.results.nextDay.changedFiles, ['content/docs/coming-soon.json', 'public/docs-manifest.json']);
    assert.notEqual(evidence.views[0].bodies.llms, evidence.views[1].bodies.llms);
    assert.notEqual(evidence.views[1].bodies.llms, evidence.views[2].bodies.llms, 'actual later regeneration, not request-time freshness');
    const unchangedGuide = await guide();
    assert.equal(unchangedGuide, evidence.views[2].bodies.llms, 'no arbitrary edits to force different output');
    for (const [file, redirected] of actualPaths) {
      assert.ok(evidence.reads.some((read) => read.file === file));
      assert.ok(redirected.startsWith(fixture + path.sep));
    }
    for (const stage of ['before', 'after', 'nextDay']) assert.ok(evidence.reads.some(({ file, sha256: hash }) =>
      file === path.resolve('content/docs/coming-soon.json') && hash === produced.sha256[stage]));
  } finally {
    try {
      if (process.env.PAYKIT_REGENERATION_EVIDENCE) {
        await filesystem.writeFile(process.env.PAYKIT_REGENERATION_EVIDENCE, JSON.stringify(evidence, null, 2) + '\n', { flag: 'wx' });
      }
    } finally {
      await filesystem.rm(fixture, { recursive: true, force: true });
    }
  }
});
