import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

test('browser checks cannot silently reuse a stale local production server', () => {
  const configuration = readFileSync('playwright.config.ts', 'utf8');
  const reuse = configuration.match(/reuseExistingServer:\s*([^,\n]+)/);
  assert.ok(reuse, 'the runner must state its server ownership policy');
  assert.equal(reuse[1].trim(), 'false', 'always start the tested build or fail on an occupied port');
});
