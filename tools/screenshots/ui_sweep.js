/**
 * Full UI sweep of the running gallery: every main surface, console/page
 * errors, failed network, and screenshots for visual review.
 *
 * Usage (proxy on :8899 serving gallery + /api):
 *   cd /tmp/pw && node /path/to/tools/screenshots/ui_sweep.js
 *
 * Env:
 *   SWEEP_URL   default http://127.0.0.1:8899/
 *   SWEEP_OUT   default /tmp/webcam_ui_sweep
 *   SWEEP_LABEL optional label for this run (e.g. Webcam21)
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const URL = process.env.SWEEP_URL || 'http://127.0.0.1:8899/';
const OUT = process.env.SWEEP_OUT || '/tmp/webcam_ui_sweep';
const LABEL = process.env.SWEEP_LABEL || 'app';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

fs.mkdirSync(OUT, { recursive: true });

function prefix(name) {
  return `${LABEL}-${name}`;
}

async function settle(page) {
  await page.waitForLoadState('domcontentloaded').catch(() => {});
  await sleep(1500);
  await page
    .addStyleTag({
      content: `*, *::before, *::after { animation: none !important; transition: none !important; }
     [class*=spin],[class*=loading],.loader,.spinner{display:none!important;}`,
    })
    .catch(() => {});
  await sleep(200);
}

async function freezeTimers(page) {
  await page
    .evaluate(() => {
      const hi = setTimeout(() => {}, 0);
      for (let i = 0; i <= hi; i++) {
        clearTimeout(i);
        clearInterval(i);
      }
    })
    .catch(() => {});
}

async function preloadLazy(page) {
  await page
    .evaluate(async () => {
      const sc = document.scrollingElement || document.body;
      const h = sc.scrollHeight;
      for (let y = 0; y <= h; y += 700) {
        sc.scrollTop = y;
        await new Promise((r) => setTimeout(r, 80));
      }
      sc.scrollTop = 0;
    })
    .catch(() => {});
  await page.waitForLoadState('domcontentloaded').catch(() => {});
  await sleep(500);
  await freezeTimers(page);
}

async function collectDomIssues(page) {
  return page.evaluate(() => {
    const issues = [];
    // Visible error-ish text nodes (heuristic)
    const bodyText = document.body ? document.body.innerText : '';
    for (const needle of [
      'Something went wrong',
      'TypeError',
      'ReferenceError',
      'Uncaught',
      'Failed to fetch',
      'is not defined',
    ]) {
      if (bodyText.includes(needle)) issues.push(`body_text:${needle}`);
    }
    // Broken images in viewport-ish (complete + naturalWidth)
    const imgs = [...document.querySelectorAll('img')].slice(0, 80);
    let broken = 0;
    for (const img of imgs) {
      if (img.complete && img.naturalWidth === 0 && img.src && !img.src.startsWith('data:')) {
        broken++;
      }
    }
    if (broken > 0) issues.push(`broken_images:${broken}`);
    // Empty main content?
    const main = document.querySelector('main, #app, .gallery, .content, body');
    const hasCards =
      document.querySelectorAll('.card, .visit-card, .list-row, [class*="card"]').length > 0;
    const hasEmpty =
      /no (images|visits|results|detections)/i.test(bodyText) ||
      document.querySelector('.empty-state, .timeline-empty, [class*="empty"]');
    return {
      title: document.title,
      issues,
      hasCards,
      hasEmpty: !!hasEmpty,
      bodyLen: bodyText.length,
      imgCount: document.querySelectorAll('img').length,
      buttons: [...document.querySelectorAll('button')]
        .map((b) => (b.innerText || b.getAttribute('aria-label') || b.dataset.filter || b.dataset.view || '').trim())
        .filter(Boolean)
        .slice(0, 40),
    };
  });
}

async function run() {
  const report = {
    label: LABEL,
    url: URL,
    started: new Date().toISOString(),
    pageErrors: [],
    consoleErrors: [],
    requestFailures: [],
    surfaces: [],
    shots: [],
  };

  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
    reducedMotion: 'reduce',
  });
  const page = await ctx.newPage();

  page.on('pageerror', (e) => {
    report.pageErrors.push(e.message.split('\n')[0]);
    console.log('PAGEERR:', e.message.split('\n')[0]);
  });
  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      const t = msg.text();
      // Filter noisy favicon / SSE noise if any
      if (/favicon|net::ERR_ABORTED/i.test(t)) return;
      report.consoleErrors.push(t.slice(0, 300));
      console.log('CONSOLEERR:', t.slice(0, 200));
    }
  });
  page.on('requestfailed', (req) => {
    const u = req.url();
    if (/\/api\/events|favicon/i.test(u)) return;
    report.requestFailures.push({
      url: u.slice(0, 200),
      error: (req.failure() && req.failure().errorText) || 'failed',
    });
  });

  const snap = async (name) => {
    const file = path.join(OUT, `${prefix(name)}.png`);
    try {
      await page.screenshot({
        path: file,
        fullPage: false,
        animations: 'disabled',
        caret: 'hide',
        timeout: 30000,
      });
      report.shots.push(file);
      console.log('shot', file);
    } catch (e) {
      console.log('SHOT_FAIL', name, e.message.split('\n')[0]);
      report.surfaces.push({ name, ok: false, error: e.message.split('\n')[0] });
    }
  };

  const surface = async (name, action) => {
    const entry = { name, ok: true, issues: [], dom: null };
    try {
      if (action) await action();
      await settle(page);
      await freezeTimers(page);
      entry.dom = await collectDomIssues(page);
      entry.issues = [...(entry.dom.issues || [])];
      await snap(name);
      if (entry.issues.length) entry.ok = false;
      console.log(
        'surface',
        name,
        entry.ok ? 'OK' : 'ISSUES',
        entry.issues.join('; ') || ''
      );
    } catch (e) {
      entry.ok = false;
      entry.error = e.message.split('\n')[0];
      console.log('surface FAIL', name, entry.error);
      await snap(`${name}-error`).catch(() => {});
    }
    report.surfaces.push(entry);
    return entry;
  };

  // ---- Load app ----
  await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await settle(page);

  await surface('01-timeline-default');

  await surface('02-objects-tab', async () => {
    await page.click('button[data-filter="objects"]').catch(async () => {
      await page.click('text=Objects').catch(() => {});
    });
    await preloadLazy(page);
  });

  await surface('03-all-tab-medium', async () => {
    await page.click('button[data-filter="all"]').catch(async () => {
      await page.click('text=All').catch(() => {});
    });
    await page.click('button[data-view="medium"]').catch(() => {});
    await preloadLazy(page);
  });

  for (const view of ['large', 'list', 'small']) {
    await surface(`04-all-view-${view}`, async () => {
      await page.click('button[data-filter="all"]').catch(() => {});
      await page.click(`button[data-view="${view}"]`).catch(() => {});
      await preloadLazy(page);
    });
  }

  await surface('05-insights', async () => {
    // gear / AI / status panel — try several selectors used in the SPA
    const opened =
      (await page.click('button[title*="status" i], button[aria-label*="status" i], #status-btn, .status-btn').then(() => true).catch(() => false)) ||
      (await page.click('text=Insights').then(() => true).catch(() => false)) ||
      (await page.click('[data-lucide="info"], button:has(i[data-lucide="activity"])').then(() => true).catch(() => false));
    if (!opened) {
      // open any panel that looks like insights
      await page.evaluate(() => {
        const btns = [...document.querySelectorAll('button')];
        const b = btns.find((x) => /insight|status|pipeline|AI/i.test(x.innerText + (x.title || '')));
        if (b) b.click();
      });
    }
    await sleep(1000);
  });

  await surface('06-settings', async () => {
    await page.keyboard.press('Escape').catch(() => {});
    await sleep(300);
    const opened =
      (await page.click('button[title*="Settings" i], button[aria-label*="Settings" i], #settings-btn').then(() => true).catch(() => false)) ||
      (await page.click('text=Settings').then(() => true).catch(() => false)) ||
      (await page.evaluate(() => {
        const b = [...document.querySelectorAll('button')].find((x) =>
          /settings|gear/i.test(x.innerText + (x.title || '') + (x.getAttribute('aria-label') || ''))
        );
        if (b) {
          b.click();
          return true;
        }
        return false;
      }));
    if (!opened) console.log('settings button not found');
    await sleep(1000);
  });

  await surface('07-lightbox', async () => {
    await page.keyboard.press('Escape').catch(() => {});
    await sleep(300);
    await page.click('button[data-filter="all"]').catch(() => {});
    await page.click('button[data-view="medium"]').catch(() => {});
    await preloadLazy(page);
    const card = await page.$('.card-img, .card img, .card');
    if (card) {
      await card.click().catch(() => {});
      await sleep(1200);
    } else {
      throw new Error('no card to open lightbox');
    }
  });

  await page.keyboard.press('Escape').catch(() => {});
  await sleep(400);

  // Timeline again after navigation stress
  await surface('08-timeline-return', async () => {
    await page.click('button[data-filter="timeline"], button[data-filter="events"]').catch(async () => {
      await page.click('text=Timeline').catch(() => {});
    });
    await sleep(800);
  });

  // Mobile viewport pass (key surfaces only)
  await ctx.close();
  const mctx = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    reducedMotion: 'reduce',
  });
  const m = await mctx.newPage();
  m.on('pageerror', (e) => report.pageErrors.push(`mobile:${e.message.split('\n')[0]}`));
  m.on('console', (msg) => {
    if (msg.type() === 'error') report.consoleErrors.push(`mobile:${msg.text().slice(0, 300)}`);
  });

  // rebind helpers to mobile page
  const mSurface = async (name, action) => {
    const entry = { name: `mobile-${name}`, ok: true, issues: [], dom: null };
    try {
      if (action) await action(m);
      await settle(m);
      await freezeTimers(m);
      entry.dom = await m.evaluate(() => ({
        title: document.title,
        bodyLen: (document.body && document.body.innerText.length) || 0,
        imgCount: document.querySelectorAll('img').length,
        hasCards: document.querySelectorAll('.card, .visit-card, .list-row').length > 0,
      }));
      const file = path.join(OUT, `${prefix('mobile-' + name)}.png`);
      await m.screenshot({
        path: file,
        fullPage: false,
        animations: 'disabled',
        timeout: 30000,
      });
      report.shots.push(file);
      console.log('shot', file);
    } catch (e) {
      entry.ok = false;
      entry.error = e.message.split('\n')[0];
      console.log('mobile FAIL', name, entry.error);
    }
    report.surfaces.push(entry);
  };

  await m.goto(URL, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await settle(m);
  await mSurface('01-timeline');
  await mSurface('02-all', async (p) => {
    await p.click('button[data-filter="all"]').catch(() => {});
    await preloadLazy(p);
  });
  await mSurface('03-objects', async (p) => {
    await p.click('button[data-filter="objects"]').catch(() => {});
    await preloadLazy(p);
  });

  await mctx.close();
  await browser.close();

  report.finished = new Date().toISOString();
  report.ok =
    report.pageErrors.length === 0 &&
    report.surfaces.every((s) => s.ok !== false) &&
    report.requestFailures.filter((r) => !/\/api\/events/.test(r.url)).length === 0;

  // Soft: console errors alone don't fail if no pageerror (many SPAs log benign stuff)
  const summaryPath = path.join(OUT, `${prefix('report')}.json`);
  fs.writeFileSync(summaryPath, JSON.stringify(report, null, 2));
  console.log('REPORT', summaryPath);
  console.log(
    'SUMMARY pageErrors=%d consoleErrors=%d reqFails=%d surfaces=%d shots=%d ok=%s',
    report.pageErrors.length,
    report.consoleErrors.length,
    report.requestFailures.length,
    report.surfaces.length,
    report.shots.length,
    report.ok
  );
  if (report.pageErrors.length) process.exitCode = 2;
}

run().catch((e) => {
  console.error('FATAL', e);
  process.exit(1);
});
