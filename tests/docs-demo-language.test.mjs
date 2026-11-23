import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const id = 'docs-plain-language-demos';
const roles = ['automation/skills/writing.md', 'automation/skills/evaluation.md'];
const json = (path) => JSON.parse(readFileSync(path, 'utf8'));
const links = (page) => page.sections.flatMap((section) => section.links ?? []);

function assertDemoName(text, field) {
  assert.match(text, /\bdemos?\b/i, `${field} must name the demo: ${text}`);
  assert.doesNotMatch(text, /\bfixtures?\b/i, `${field} calls the demo a fixture: ${text}`);
}

test('future imports retain the plain-language demo rule registration', () => {
  const rule = json('automation/corrections.json').requirements.find((item) => item.id === id);
  assert.ok(rule, `missing correction: ${id}`);
  for (const path of [...roles, 'tests/docs-demo-language.test.mjs']) {
    assert.ok(rule.enforcedBy?.includes(path), `${id} does not list ${path}`);
  }
  for (const path of roles) {
    assert.ok(readFileSync(path, 'utf8').includes(id), `${path} does not reference ${id}`);
  }
});

test('selected docs name the demo downloads and overview entry points', () => {
  const { pages } = json('content/docs/coming-soon.json');
  const overview = pages.find(({ slug }) => slug === 'overview');
  assert.ok(overview, 'missing overview');
  // The overview's short description introduces the demos. Contextual body prose
  // (including genuine test setup) belongs to the evaluator, not a word scan.
  assertDemoName(overview.description, 'overview description');
  for (const slug of ['wallets', 'point-of-sale']) {
    const guide = pages.find((page) => page.slug === slug);
    assert.ok(guide, `missing guide: ${slug}`);
    const download = links(guide).find(({ href }) => href.endsWith('.tar.gz'));
    assert.ok(download, `${slug} guide is missing its download`);
    assertDemoName(download.label, `${slug} download label`);

    const entry = links(overview).find(({ href }) => href === `/docs/coming-soon/${slug}`);
    assert.ok(entry, `overview is missing its link to ${slug}`);
    assertDemoName(entry.label, `overview ${slug} link label`);
  }
});
