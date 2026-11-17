import { chromium } from '@playwright/test';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

export async function renderSvg(code, seed, existingBrowser) {
  if (/</.test(code) || /click\s|https?:|javascript:|%%\{/i.test(code)) throw new Error('Unsafe Mermaid input');
  const browser = existingBrowser || await chromium.launch();
  const page = await browser.newPage();
  try {
    await page.addScriptTag({ path: resolve('node_modules/mermaid/dist/mermaid.min.js') });
    const svg = await page.evaluate(async ({ code, seed }) => {
      window.mermaid.initialize({ startOnLoad: false, securityLevel: 'strict', htmlLabels: false, look: 'classic', theme: 'dark',
        deterministicIds: true, deterministicIDSeed: seed,
        fontFamily: 'Arial, sans-serif', themeVariables: { fontSize: '16px', primaryColor: '#181c25',
          primaryTextColor: '#f4f5f8', lineColor: '#ff855e', primaryBorderColor: '#a0a7b5',
          mainBkg: '#181c25', edgeLabelBackground: '#11141b' },
        flowchart: { htmlLabels: false, useMaxWidth: true, curve: 'linear', nodeSpacing: 24, rankSpacing: 36 } });
      return (await window.mermaid.render(`diagram-${seed}`, code)).svg;
    }, { code, seed });
    if (/<foreignObject|<script|\son\w+=|(?:href|src)=["'](?:https?:|data:|javascript:)/i.test(svg)) throw new Error('Unsafe SVG output');
    const viewBox = svg.match(/viewBox="[-0-9.]+ [-0-9.]+ ([0-9.]+) ([0-9.]+)"/);
    if (!viewBox) throw new Error('SVG has no numeric intrinsic size');
    return svg.replace('width="100%"', `width="${viewBox[1]}" height="${viewBox[2]}"`);
  } finally {
    await page.close();
    if (!existingBrowser) await browser.close();
  }
}

async function main() {
  const inputs = JSON.parse(await readFile('.automation/diagram-input.json', 'utf8'));
  const browser = await chromium.launch();
  try {
    for (const diagram of inputs) {
      if (!/^[a-z-]+$/.test(diagram.version) || !/^[a-z-]+$/.test(diagram.id)) throw new Error('Unsafe diagram path');
      const directory = `public/docs-assets/${diagram.version}`;
      await mkdir(directory, { recursive: true });
      const svg = await renderSvg(diagram.mermaid, `${diagram.version}-${diagram.id}`, browser);
      await writeFile(`${directory}/${diagram.id}.svg`, svg);
      await writeFile(`${directory}/${diagram.id}.mmd`, diagram.mermaid + '\n');
    }
  } finally { await browser.close(); }
  console.log(`Rendered ${inputs.length} verified static Mermaid diagrams`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) await main();
