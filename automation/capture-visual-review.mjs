// Capture screenshots from the coordinator's already-verified current-build listener.
import { chromium } from '@playwright/test';
import { createHash } from 'node:crypto';
import { lstat, mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';

const [inputs, output, ...extra] = process.argv.slice(2);
const require = (condition, message) => { if (!condition) throw new Error(message); };
const sha256 = bytes => createHash('sha256').update(bytes).digest('hex');
const views = { desktop: { width: 1440, height: 900 }, phone: { width: 375, height: 812 } };
require(extra.length === 0 && inputs && output && path.isAbsolute(inputs) && path.isAbsolute(output),
        'Usage: node automation/capture-visual-review.mjs /ABS/reader/index.json /ABS/new-visual-directory');
require((await lstat(inputs)).isFile(), 'Reader index must be a regular file');
const raw = await readFile(inputs);
const index = JSON.parse(raw);
const entry = new URL(index.entryUrl);
require(entry.protocol === 'http:' && entry.hostname === '127.0.0.1' && entry.port &&
        entry.href === `${entry.origin}/docs`, 'Capture requires the explicit owned 127.0.0.1 HTTP listener');
require(index.kind === 'rendered-public-reader-input' && ['released', 'coming-soon'].includes(index.version) &&
        Array.isArray(index.pageRoutes) && index.pageRoutes.length && Array.isArray(index.documents), 'Invalid reader index');
const routes = ['/docs', ...index.pageRoutes];
const prefix = `/docs/${index.version}/`;
require(new Set(routes).size === routes.length && index.documents.length === routes.length, 'Incomplete or duplicate route scope');
for (const [i, route] of routes.entries()) {
  require(i === 0 || (typeof route === 'string' && route.startsWith(prefix) &&
          /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(route.slice(prefix.length))), 'Invalid versioned route');
  const row = index.documents[i];
  require(row.route === route && row.url === entry.origin + route && /^[0-9a-f]{64}$/.test(row.html?.sha256), 'Invalid HTML binding');
}

await mkdir(output); // Existing output, including a partial capture, is refused.
const browser = await chromium.launch();
const screenshots = [];
try {
  for (const [view, viewport] of Object.entries(views)) {
    const context = await browser.newContext({ viewport, reducedMotion: 'reduce', serviceWorkers: 'block' });
    // Track the same scope we route. Context events include a popup's first request.
    const pending = new Set();
    const routed = new Set();
    let activity = 0;
    let networkIssue;
    let routeChanged = false;
    let closingActivity;
    const idle = () => pending.size === 0 && routed.size === 0;
    try {
      context.on('request', request => { pending.add(request); activity++; });
      context.on('requestfinished', request => { pending.delete(request); });
      context.on('requestfailed', request => {
        pending.delete(request);
        networkIssue ??= `failed browser ${request.resourceType()}`;
      });
      async function refuse(route, reason) {
        networkIssue ??= reason;
        // The context may already be closing. The capture is invalid either way.
        await route.abort().catch(() => {});
      }
      await context.route('**/*', async route => {
        routed.add(route);
        activity++;
        try {
          if (new URL(route.request().url()).origin !== entry.origin) {
            await refuse(route, 'off-origin request');
            return;
          }
          // Inspect the first response before following any redirect, including subresources.
          const response = await route.fetch({ maxRedirects: 0, timeout: 15000 });
          if (!response.ok()) await refuse(route, `unusable local ${route.request().resourceType()} (${response.status()})`);
          else await route.fulfill({ response });
        } catch {
          await refuse(route, 'could not fetch or deliver local resource');
        } finally {
          routed.delete(route);
        }
      });
      await context.routeWebSocket('**/*', socket => {
        networkIssue ??= 'unexpected WebSocket';
        return socket.close();
      });
      const page = await context.newPage();
      page.setDefaultTimeout(15000);
      let mainDocuments = 0;
      let expectedUrl;
      page.on('framenavigated', frame => {
        // Next can replace history at the same URL during hydration without loading a new document.
        if (expectedUrl && frame === page.mainFrame() && frame.url() !== expectedUrl) routeChanged = true;
      });
      page.on('request', request => {
        if (request.isNavigationRequest() && request.resourceType() === 'document' && request.frame() === page.mainFrame()) mainDocuments++;
      });
      async function settle() {
        const deadline = Date.now() + 5000;
        while (Date.now() < deadline) {
          require(!networkIssue, `Capture refused: ${networkIssue}`);
          const observed = activity;
          const wasIdle = idle();
          await page.waitForTimeout(100);
          if (wasIdle && idle() && observed === activity) return;
        }
        throw new Error('Browser resources did not settle before capture');
      }
      for (const [i, row] of index.documents.entries()) {
        expectedUrl = row.url;
        routeChanged = false;
        const expectedDocument = mainDocuments + 1;
        function assertCaptureState() {
          require(!networkIssue, `Capture refused for ${row.route}: ${networkIssue}`);
          require(!routeChanged && page.url() === row.url && mainDocuments === expectedDocument,
                  `Route changed during capture: ${row.route}`);
        }
        const response = await page.goto(row.url, { waitUntil: 'load' });
        assertCaptureState();
        require(response?.status() === 200 && !response.request().redirectedFrom() && page.url() === row.url,
                `Unexpected response for ${row.route}`);
        require(sha256(await response.body()) === row.html.sha256, `Rendered HTML differs from reader bundle: ${row.route}`);
        require(await page.locator('main article h1').isVisible(), `No visible documentation article: ${row.route}`);
        for (const image of await page.locator('article img').all()) {
          await image.scrollIntoViewIfNeeded();
          await image.evaluate(async element => { await element.decode(); });
        }
        await page.evaluate(async () => { await document.fonts.ready; window.scrollTo(0, 0); });
        let bytes;
        for (let attempt = 0; attempt < 2; attempt++) {
          await settle();
          assertCaptureState();
          const observed = activity;
          const candidate = await page.screenshot({ fullPage: true, animations: 'disabled', scale: 'css', timeout: 15000 });
          await settle();
          assertCaptureState();
          if (activity === observed) { bytes = candidate; break; }
        }
        require(bytes, `Browser resources changed during both screenshot attempts: ${row.route}`);
        const file = `${String(i).padStart(2, '0')}-${view}.png`;
        await writeFile(path.join(output, file), bytes, { flag: 'wx' });
        screenshots.push({ route: row.route, view, viewport, htmlSha256: row.html.sha256,
                          file, bytes: bytes.length, sha256: sha256(bytes) });
      }
      require(!networkIssue && idle() && !routeChanged, 'Unsettled or failed final browser capture');
      closingActivity = activity;
    } finally {
      await context.close();
    }
    // Keep observers attached through close. Even a successful late request invalidates the seal.
    require(!networkIssue && idle() && !routeChanged && activity === closingActivity,
            'Browser context changed or failed during close; visual index not sealed');
  }
} finally {
  await browser.close();
}
const visual = { kind: 'rendered-public-visual-input', inputManifestSha256: sha256(raw), screenshots };
await writeFile(path.join(output, 'index.json'), JSON.stringify(visual, null, 2) + '\n', { flag: 'wx' });
console.log(JSON.stringify({ action: 'captured', screenshots: screenshots.length, visualIndex: path.join(output, 'index.json') }));
