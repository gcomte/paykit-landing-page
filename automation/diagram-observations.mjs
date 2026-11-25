// Actual browser image observations for diagram review. Not used on ordinary visual captures.
import { marked } from 'marked';

// Serialize this function into Chromium for both captures. Keep using the actual
// screenshot for visual judgment; this conservative gate rejects unsupported
// clipping and masking instead of claiming checkVisibility measures painted pixels.
function observeImages({ markdownRoute }) {
  if (markdownRoute && document.querySelector('base,picture,img[srcset]')) {
    throw new Error('Unsupported alternate image source in rendered Markdown');
  }
  function renderable(image) {
    if (!image.checkVisibility({ opacityProperty: true, visibilityProperty: true, contentVisibilityAuto: true })) return false;
    let { left, top, right, bottom } = image.getBoundingClientRect();
    for (let node = image; node; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (style.clipPath !== 'none' || style.clip !== 'auto' || style.maskImage !== 'none' || style.filter !== 'none') return false;
      // Full-page capture is not restricted to the root scrolling viewport, but body
      // can be a separate overflow clip (for example, when html explicitly scrolls).
      if (node === image || node === document.documentElement) continue;
      const box = node.getBoundingClientRect();
      if (style.overflowX !== 'visible') {
        left = Math.max(left, box.left + node.clientLeft);
        right = Math.min(right, box.left + node.clientLeft + node.clientWidth);
      }
      if (style.overflowY !== 'visible') {
        top = Math.max(top, box.top + node.clientTop);
        bottom = Math.min(bottom, box.top + node.clientTop + node.clientHeight);
      }
    }
    return right - left >= 1 && bottom - top >= 1;
  }
  return Array.from(markdownRoute ? document.images : document.querySelectorAll('article img'), image => {
    const src = image.getAttribute('src');
    const observation = {
      // The isolated Markdown page never fetches images; resolve relative to the actual route.
      url: markdownRoute ? src ? new URL(src, markdownRoute).href : null : image.currentSrc || null,
      alt: image.alt,
      visible: renderable(image),
    };
    return markdownRoute ? observation : { section: image.closest('section')?.id ?? null, ...observation };
  });
}

export async function htmlDiagramImages(page) {
  return page.evaluate(observeImages, { markdownRoute: null });
}

export async function createMarkdownObserver(browser, viewport) {
  const context = await browser.newContext({ viewport, javaScriptEnabled: false, serviceWorkers: 'block' });
  try {
    await context.route('**/*', route => route.abort());
    await context.routeWebSocket('**/*', socket => socket.close());
    const page = await context.newPage();
    return {
      close: () => context.close(),
      async observe(markdown, routeUrl) {
        // Use the same Marked renderer as the docs QA. Its HTML goes into a fresh,
        // script-disabled, network-disabled page, not the verified candidate page.
        const rendered = marked.parse(markdown, { async: false });
        await page.setContent(`<!doctype html><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">${rendered}`,
                              { waitUntil: 'load', timeout: 15000 });
        if (page.url() !== 'about:blank') throw new Error('Markdown observation left its isolated page');
        return page.evaluate(observeImages, { markdownRoute: routeUrl });
      },
    };
  } catch (error) {
    await context.close();
    throw error;
  }
}
