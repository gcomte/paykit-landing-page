import { chromium } from '@playwright/test';
import { spawn } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';

const origin = 'http://127.0.0.1:3173';
const server = spawn(process.execPath, ['node_modules/next/dist/bin/next', 'start', '--hostname', '127.0.0.1', '--port', '3173'], { stdio: 'ignore', detached: true });
let browser;
try {
  let ready = false;
  for (let attempt = 0; attempt < 100; attempt++) {
    try { if ((await fetch(origin)).ok) { ready = true; break; } } catch {}
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  if (!ready) throw new Error('Temporary QA server failed to start');
  await mkdir('.automation/screenshots', { recursive: true });
  browser = await chromium.launch();
  const page = await browser.newPage();
  const evidence = [];
  for (const version of ['released', 'coming-soon']) {
    for (const width of [1440, 375]) {
      await page.setViewportSize({ width, height: 1000 });
      for (const slug of ['overview', 'components', 'flows']) {
        await page.goto(`${origin}/docs/${version}/${slug}`);
        if (slug === 'overview') await page.screenshot({ path: `.automation/screenshots/${version}-overview-${width}.png` });
        for (const image of await page.locator('article img').all()) {
          await image.scrollIntoViewIfNeeded();
          const box = await image.boundingBox();
          const src = await image.getAttribute('src');
          const svg = await (await page.request.get(origin + src)).text();
          const view = svg.match(/viewBox="([^"]+)"/)[1].split(' ').map(Number);
          const labelPixels = 16 * box.width / view[2];
          if (labelPixels < 12 || labelPixels > 24) throw new Error(`Unhelpful diagram scale ${labelPixels}px: ${src} at ${width}`);
          if (box.width > width || await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error('Diagram overflow');
          evidence.push({ version, width, slug, src, renderedWidth: box.width, renderedHeight: box.height, labelPixels });
        }
        if (slug !== 'overview') await page.locator('article figure').first().screenshot({ path: `.automation/screenshots/${version}-${slug}-${width}.png` });
      }
    }
  }
  await writeFile('.automation/screenshots/geometry.json', JSON.stringify(evidence, null, 2));
  console.log(JSON.stringify(evidence, null, 2));
} finally {
  if (browser) await browser.close();
  try { process.kill(-server.pid, 'SIGTERM'); } catch {}
}
