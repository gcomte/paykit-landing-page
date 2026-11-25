import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { access, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

const run = promisify(execFile);
const capture = fileURLToPath(new URL('../automation/capture-visual-review.mjs', import.meta.url));
const packageJson = fileURLToPath(new URL('../package.json', import.meta.url));
const page = script => `<!doctype html><meta charset="utf-8">${script}<main><article><h1>Local visual fixture</h1></article></main>`;

async function withFixture(html, answer, check) {
  const temp = await mkdtemp(path.join(tmpdir(), 'paykit-visual-capture-'));
  const hits = new Map();
  const server = createServer((request, response) => {
    hits.set(request.url, (hits.get(request.url) ?? 0) + 1);
    if (answer(request, response)) return;
    response.setHeader('Content-Type', 'text/html; charset=utf-8');
    response.end(html);
  });
  try {
    await new Promise((resolve, reject) => {
      server.once('error', reject);
      server.listen(0, '127.0.0.1', resolve);
    });
    const base = `http://127.0.0.1:${server.address().port}`;
    const routes = ['/docs', '/docs/coming-soon/overview'];
    const htmlSha = createHash('sha256').update(html).digest('hex');
    const index = path.join(temp, 'reader-index.json');
    await writeFile(index, JSON.stringify({ kind: 'rendered-public-reader-input', version: 'coming-soon',
      entryUrl: `${base}/docs`, pageRoutes: routes.slice(1),
      documents: routes.map(route => ({ route, url: base + route, html: { sha256: htmlSha } })) }));
    await check({ temp, index, output: path.join(temp, 'visual'), hits });
  } finally {
    if (server.listening) {
      const closing = new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
      server.closeAllConnections();
      await closing;
    }
    await rm(temp, { recursive: true });
  }
}

async function expectRefusal(script, index, output, reason) {
  await assert.rejects(run(process.execPath, [script, index, output], { timeout: 25000 }), error => {
    assert.equal(error.killed, false, error.stderr);
    assert.match(error.stderr, reason);
    return true;
  });
  await assert.rejects(access(path.join(output, 'index.json')), { code: 'ENOENT' });
}

test('refuses an unsettled request owned by a popup, not the capture page', async () => {
  const html = page('<script>window.addEventListener("load", () => window.open("/popup", "_blank"))</script>');
  await withFixture(html, (request, response) => {
    if (request.url === '/popup') {
      response.setHeader('Content-Type', 'text/html; charset=utf-8');
      response.end('<!doctype html><script>fetch("/popup-pending").catch(() => {})</script>');
      return true;
    }
    return request.url === '/popup-pending'; // Leave only the popup's own request open.
  }, async ({ index, output, hits }) => {
    await expectRefusal(capture, index, output, /Browser resources did not settle before capture/);
    assert.equal(hits.get('/popup'), 1);
    assert.equal(hits.get('/popup-pending'), 1);
  });
});

test('a real new request during context close invalidates the screenshot seal', async () => {
  await withFixture(page(''), (request, response) => {
    if (request.url !== '/teardown-probe') return false;
    response.setHeader('Content-Type', 'text/plain');
    response.end('successful local teardown request');
    return true;
  }, async ({ temp, index, output, hits }) => {
    // Instrument exactly the Chromium import in a disposable copy. The actual capture and
    // assertion code stays byte-for-byte identical and uses a real BrowserContext.
    const source = await readFile(capture, 'utf8');
    const original = "import { chromium } from '@playwright/test';";
    const shim = "import { chromium } from './playwright-close-probe.mjs';";
    assert.equal(source.split(original).length, 2);
    const instrumented = source.replace(original, shim);
    assert.equal(instrumented.replace(shim, original), source);
    const script = path.join(temp, 'capture-under-test.mjs');
    await writeFile(script, instrumented);
    await writeFile(path.join(temp, 'playwright-close-probe.mjs'), `
import { createRequire } from 'node:module';
const upstream = createRequire(${JSON.stringify(packageJson)})('@playwright/test').chromium;
export const chromium = {
  async launch(...options) {
    const browser = await upstream.launch(...options);
    const newContext = browser.newContext.bind(browser);
    browser.newContext = async (...args) => {
      const context = await newContext(...args);
      const close = context.close.bind(context);
      context.close = async (...closeArgs) => {
        try {
          await context.pages()[0].evaluate(async () => {
            const response = await fetch('/teardown-probe');
            if (!response.ok) throw new Error('teardown fixture request failed');
            await response.text();
          });
        } finally {
          await close(...closeArgs);
        }
      };
      return context;
    };
    return browser;
  }
};
`);
    await expectRefusal(script, index, output, /Browser context changed or failed during close; visual index not sealed/);
    assert.equal(hits.get('/teardown-probe'), 1);
  });
});
