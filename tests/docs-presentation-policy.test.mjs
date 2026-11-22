import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const correctionId = 'docs-global-presentation-cleanup';

test('imports preserve the explicit global-only presentation correction and its technical boundaries', () => {
  const register = JSON.parse(readFileSync('automation/corrections.json', 'utf8'));
  const rule = register.requirements.find(({ id }) => id === correctionId);
  assert.ok(rule, 'the explicit user correction must survive future imports');
  assert.equal(rule.userText, 'User authorizes removing all3 repeated global source/readiness boxes and Released/Selected source switcher.');
  for (const expression of [/global presentation only/i, /actual version labels/i, /no version switcher/i,
    /both retained URL sets/i, /version\/date footers/i, /source references/i, /technical qualifications/i,
    /readiness metadata internal/i, /same generated snapshot\/build/i, /no manual generated edits/i,
    /newest published release/i, /older snapshot/i]) assert.match(rule.requirement, expression);
  for (const id of ['truthful-readiness-metadata', 'llms-txt-valuable-guide-v2']) {
    const historical = register.requirements.find((entry) => entry.id === id);
    assert.equal(historical.supersededBy, correctionId);
    assert.match(historical.supersededPresentationScope, /global presentation only/i);
    assert.ok(historical.requirement, 'historical source/privacy/validation obligations are retained');
  }
  for (const file of ['automation/COORDINATOR.md', 'automation/WEEKLY.md', 'automation/skills/writing.md', 'automation/skills/evaluation.md']) {
    const policy = readFileSync(file, 'utf8');
    assert.ok(policy.includes(correctionId), file);
    for (const expression of [/no version switcher/i, /actual version labels/i, /readiness metadata internal/i,
      /technical qualifications/i, /version\/date footers/i, /both retained URL sets/i,
      /same generated snapshot\/build/i, /no manual generated edits/i]) assert.match(policy, expression, file);
    assert.doesNotMatch(policy, /exact labels\/commits\/source URLs\/readiness notices|exact current labels\/commits\/source URLs\/readiness notices|Preserve exact labels, commits, source URLs and readiness notices|exact source commit\/URL\/readiness notice|carries source-asserted readiness and evidence onto every page/);
  }
});
