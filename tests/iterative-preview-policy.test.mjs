import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';

test('incremental preview preference is recorded without granting normal acceptance or execution claims', () => {
  const rules = JSON.parse(readFileSync('automation/corrections.json', 'utf8'));
  const entry = rules.requirements.find((entry) => entry.id === 'iterative-small-public-preview-first');
  assert.ok(entry, 'record the user preference in the existing correction schema');
  assert.match(entry.requirement, /early.*incremental/i);
  assert.match(entry.requirement, /independent review/);
  assert.match(entry.requirement, /source mapping/);
  assert.match(entry.requirement, /not_run_deferred/);
  assert.match(entry.requirement, /never advance successful inputs/);
  assert.match(entry.requirement, /scheduling stays OFF/);
  for (const file of entry.enforcedBy) assert.ok(existsSync(file), file);
  const note = readFileSync('automation/ITERATIVE-PREVIEW.md', 'utf8');
  assert.match(note, /13 of 14/);
  assert.match(note, /homepage Markdown.*pending/i);
  assert.match(note, /tested_not_approved/);
  assert.match(note, /parent.*build.*browser.*publication/i);
  assert.match(note, /not_run_deferred/);
});
