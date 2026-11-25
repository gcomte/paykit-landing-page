import assert from 'node:assert/strict';
import { test } from 'node:test';
import { chromium } from '@playwright/test';
import { createMarkdownObserver, htmlDiagramImages } from '../automation/diagram-observations.mjs';

const origin = 'http://127.0.0.1:48131'; // Every request is intercepted; no network listener is needed.
const route = origin + '/docs/coming-soon/overview';
const imagePath = '/docs-assets/coming-soon/sample.svg';
const viewport = { width: 375, height: 812 };
const svg = '<svg xmlns="http://www.w3.org/2000/svg" width="50" height="30"><rect width="50" height="30"/></svg>';

test('diagram HTML observations use actual visible DOM and effective browser image sources', async () => {
  const browser = await chromium.launch();
  try {
    const ctx = await browser.newContext({ viewport, deviceScaleFactor: 1, serviceWorkers: 'block' });
    let html;
    await ctx.route('**/*', async request => {
      const url = new URL(request.request().url());
      assert.equal(url.origin, origin);
      return request.fulfill(url.pathname.endsWith('.svg') ? { contentType: 'image/svg+xml', body: svg }
        : { contentType: 'text/html', body: html });
    });
    const page = await ctx.newPage();
    const image = `<img src="${imagePath}" alt="Actor">`;
    async function observe(figure, head = '') {
      html = `<!doctype html>${head}<article><section id="flow">${figure}</section></article>`;
      await page.goto(route, { waitUntil: 'load' });
      return htmlDiagramImages(page);
    }
    assert.deepEqual(await observe(image), [{ section: 'flow', url: origin + imagePath, alt: 'Actor', visible: true }]);
    assert.deepEqual(await observe(`<template></noscript>${image}</template>`), []);
    const responsive = await observe(`<img src="${imagePath}" srcset="/alternate.svg 1x" alt="Actor">`);
    assert.equal(responsive[0].url, origin + '/alternate.svg');
    const movedBase = await observe('<img src="sample.svg" alt="Actor">', '<base href="/alternate/">');
    assert.equal(movedBase[0].url, origin + '/alternate/sample.svg');
    assert.equal((await observe(`<div hidden>${image}</div>`))[0].visible, false);
    assert.equal((await observe(`<div style="clip-path: inset(50%)">${image}</div>`))[0].visible, false);
    assert.equal((await observe(`<div style="width:0;height:0;overflow:hidden">${image}</div>`))[0].visible, false);
    assert.equal((await observe(`<div style="mask-image:linear-gradient(transparent,transparent)">${image}</div>`))[0].visible, false);
    const bodyClip = '<style>html{overflow:auto}body{height:0;overflow:hidden}</style>';
    assert.equal((await observe(image, bodyClip))[0].visible, false);
    await ctx.close();
  } finally {
    await browser.close();
  }
});

test('diagram Markdown observations parse structure and formatted alt in isolated Chromium', async () => {
  const browser = await chromium.launch();
  try {
    const observe = await createMarkdownObserver(browser, viewport);
    try {
      const image = `![**Actor**](<${imagePath}>)`;
      assert.deepEqual(await observe.observe(image, route), [{ url: origin + imagePath, alt: 'Actor', visible: true }]);
      assert.deepEqual(await observe.observe(`<div\n>\n${image}\n</div>`, route), []);
      assert.deepEqual(await observe.observe(`<!--\n${image}\n-->`, route), []);
      const doubled = await observe.observe(`![Other](</docs-assets/released/sample.svg>) ${image}`, route);
      assert.deepEqual(doubled.map(x => x.url), [origin + '/docs-assets/released/sample.svg', origin + imagePath]);
      const script = `<script>document.images[0].alt='changed by script'</script>`;
      assert.equal((await observe.observe(`${image}\n\n${script}`, route))[0].alt, 'Actor');
      const clipped = `<div style="clip-path:inset(50%)"><img src="${imagePath}" alt="Actor"></div>`;
      assert.equal((await observe.observe(clipped, route))[0].visible, false);
      const bodyClipped = `<style>html{overflow:auto}body{height:0;overflow:hidden}</style><img src="${imagePath}" alt="Actor">`;
      assert.equal((await observe.observe(bodyClipped, route))[0].visible, false);
    } finally {
      await observe.close();
    }
  } finally {
    await browser.close();
  }
});
