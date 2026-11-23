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


test('imports preserve the footer-only version presentation follow-up without deleting technical or llms metadata', () => {
  const register = JSON.parse(readFileSync('automation/corrections.json', 'utf8'));
  const followUp = register.requirements.find(({ id }) => id === 'docs-footer-only-version-presentation');
  assert.ok(followUp, 'persist the explicit follow-up rather than relying on chat');
  assert.equal(followUp.userText, 'It\'s not helpful. The comment "Covers Paykit v0.1.1-rc9" in the footer is enough.');
  for (const expression of [/remove the shared Documentation source\/provenance header box entirely/i,
    /Source commit\/View exact source/i, /do not hide it or relocate/i,
    /actual version labels.*existing Covers Paykit version\/date footers/i,
    /persisted generation date unchanged/i, /both retained URL sets/i, /technical Source references/i,
    /compact llms\/index version\/source\/commit\/date catalogue/i, /components\/docs\/llms.ts remains unchanged/i,
    /public-source\/privacy\/review boundaries/i, /same generated snapshot\/build/i, /no manual generated edits/i]) {
    assert.match(followUp.requirement, expression);
  }
  const historical = register.requirements.find(({ id }) => id === correctionId);
  assert.equal(historical.supersededBy, followUp.id);
  assert.match(historical.supersededPresentationScope, /Only the visible Documentation source\/provenance panel expectation/i);
  for (const file of ['automation/COORDINATOR.md', 'automation/WEEKLY.md', 'automation/skills/writing.md', 'automation/skills/evaluation.md']) {
    const policy = readFileSync(file, 'utf8');
    assert.ok(policy.includes(followUp.id), file);
    for (const expression of [/actual version labels.*existing Covers Paykit version\/date footers/i,
      /removes the Documentation source\/provenance header box entirely/i, /Source commit\/View exact source/i,
      /do not hide it or relocate/i, /persisted generation date unchanged/i,
      /compact llms\/index version\/source\/commit\/date catalogue byte-for-byte/i,
      /public-source\/privacy\/review boundaries/i]) assert.match(policy, expression, file);
  }
});
