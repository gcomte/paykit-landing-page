import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';

test('static diagram renderer rejects active markup and renders real Mermaid SVG', async () => {
  assert.ok(existsSync('automation/render-diagrams.mjs'), 'static Mermaid renderer is missing');
  const { renderSvg } = await import('../automation/render-diagrams.mjs');
  await assert.rejects(() => renderSvg('flowchart TD\nA["<script>bad</script>"] --> B[Rail]', 'unsafe'), /unsafe/i);
  const svg = await renderSvg('flowchart TD\nA[Wallet] --> B[Payment rail]', 'fixture');
  assert.match(svg, /<svg/);
  assert.match(svg, /<svg[^>]* width="[0-9.]+"[^>]* height="[0-9.]+"/);
  const text = svg.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ');
  assert.match(text, /Wallet/);
  assert.match(text, /Payment rail/);
  assert.doesNotMatch(svg, /<foreignObject|<script|javascript:|(?:href|src)=["']https?:/i);
});
