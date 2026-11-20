import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

test('Resources remain a generated, version-bound part of all eight selected-source pages', () => {
  const corrections = JSON.parse(readFileSync('automation/corrections.json', 'utf8'));
  const requirement = corrections.requirements.find((row) => row.id === 'generated-version-aware-resources');
  assert.ok(requirement, 'the newly approved Resources increment needs a durable correction');
  assert.match(requirement.requirement, /eight/);
  assert.match(requirement.requirement, /immutable/);
  assert.match(requirement.requirement, /independent/);
  assert.match(requirement.requirement, /never manually/i);
  for (const target of ['automation/refresh_resources.py', 'automation/WEEKLY.md', 'tests/docs-resources.test.mjs']) {
    assert.ok(requirement.enforcedBy.includes(target), target);
  }
});

test('the operative author and reviewer flow includes both reference pages on every selected-source refresh', () => {
  for (const target of ['automation/COORDINATOR.md', 'automation/WEEKLY.md', 'automation/skills/writing.md', 'automation/skills/evaluation.md']) {
    const text = readFileSync(target, 'utf8');
    assert.ok(text.includes('glossary and Resources'), `${target} must maintain both reference pages`);
  }
  const weekly = readFileSync('automation/WEEKLY.md', 'utf8');
  assert.match(weekly, /all eight existing pages/);
  assert.match(weekly, /immutable/);
  assert.match(weekly, /refresh_resources/);
});
