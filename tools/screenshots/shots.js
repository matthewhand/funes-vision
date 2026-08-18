const { chromium } = require('playwright');
const fs = require('fs');
const OUT = process.env.SHOTS_OUT || '/tmp/webcam_shots';
const URL = process.env.SHOTS_URL || 'http://127.0.0.1:8899/';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
fs.mkdirSync(OUT, { recursive: true });

async function settle(page) {
  await page.waitForLoadState('networkidle').catch(() => {});
  await sleep(1200);
  await page.addStyleTag({
    content: `*, *::before, *::after { animation: none !important; transition: none !important; }
     [class*=spin],[class*=loading],.loader,.spinner{display:none!important;}`,
  }).catch(() => {});
  await sleep(200);
}

async function freezeTimers(page) {
  await page.evaluate(() => {
    const hi = setTimeout(() => {}, 0);
    for (let i = 0; i <= hi; i++) {
      clearTimeout(i);
      clearInterval(i);
    }
  }).catch(() => {});
}

async function preloadLazy(page) {
  await page.evaluate(async () => {
    const sc = document.scrollingElement || document.body;
    const h = sc.scrollHeight;
    for (let y = 0; y <= h; y += 600) {
      sc.scrollTop = y;
      await new Promise((r) => setTimeout(r, 80));
    }
    sc.scrollTop = 0;
  }).catch(() => {});
  await page.waitForLoadState('networkidle').catch(() => {});
  await sleep(400);
  await freezeTimers(page);
}

async function run() {
  const browser = await chromium.launch({ headless: true });
  const shots = [];
  const snap = async (page, name) => {
    const path = `${OUT}/${name}.png`;
    try {
      await page.screenshot({
        path,
        fullPage: false,
        animations: 'disabled',
        caret: 'hide',
        timeout: 25000,
      });
      shots.push(name);
      console.log('shot', name);
    } catch (e) {
      console.log('SKIP', name, '-', e.message.split('\n')[0]);
    }
  };

  const dctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
    reducedMotion: 'reduce',
  });
  const d = await dctx.newPage();
  d.on('pageerror', (e) => console.log('PAGEERR:', e.message.split('\n')[0]));
  await d.goto(URL, { waitUntil: 'domcontentloaded' });
  await settle(d);
  await freezeTimers(d);
  await snap(d, 'desktop-01-timeline-visits');

  // Visit flipbook if a playable row exists
  const visit = await d.$('[aria-label^="Play "], .visit-card, .event-row');
  if (visit) {
    await visit.click().catch(() => {});
    await sleep(800);
    await freezeTimers(d);
    await snap(d, 'desktop-02-visit-player');
    await d.keyboard.press('Escape').catch(() => {});
    await sleep(300);
  }

  await d.click('button[data-filter="objects"]').catch(() => {});
  await preloadLazy(d);
  await snap(d, 'desktop-03-objects-tab');

  await d.click('button[data-filter="all"]').catch(() => {});
  await preloadLazy(d);
  await snap(d, 'desktop-04-all-grid');

  await d.click('#btn-filters, [aria-label*="Filter"]').catch(() => {});
  await sleep(500);
  await freezeTimers(d);
  await snap(d, 'desktop-05-filters');
  await d.keyboard.press('Escape').catch(() => {});

  await d.click('#btn-manage-blacklist').catch(() => {});
  await sleep(600);
  await freezeTimers(d);
  await snap(d, 'desktop-06-settings');
  await d.keyboard.press('Escape').catch(() => {});

  await d.click('#btn-system-status').catch(() => {});
  await sleep(800);
  await freezeTimers(d);
  await snap(d, 'desktop-07-status');
  await d.keyboard.press('Escape').catch(() => {});

  const card = await d.$('.card-img, .image-card img, .image-card');
  if (card) {
    await card.click().catch(() => {});
    await sleep(800);
    await freezeTimers(d);
    await snap(d, 'desktop-08-lightbox');
    await d.keyboard.press('Escape').catch(() => {});
  }

  await dctx.close();

  const mctx = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    reducedMotion: 'reduce',
  });
  const m = await mctx.newPage();
  await m.goto(URL, { waitUntil: 'domcontentloaded' });
  await settle(m);
  await freezeTimers(m);
  await snap(m, 'mobile-01-timeline');

  await m.click('button[data-filter="all"]').catch(() => {});
  await preloadLazy(m);
  await snap(m, 'mobile-02-all-grid');

  await m.click('#btn-system-status').catch(() => {});
  await sleep(700);
  await freezeTimers(m);
  await snap(m, 'mobile-03-status');
  await m.keyboard.press('Escape').catch(() => {});

  await m.click('#btn-manage-blacklist').catch(() => {});
  await sleep(600);
  await freezeTimers(m);
  await snap(m, 'mobile-04-settings');

  await mctx.close();
  await browser.close();
  fs.writeFileSync(`${OUT}/manifest.json`, JSON.stringify(shots, null, 2));
  console.log('DONE', shots.length, 'shots');
}

run().catch((e) => {
  console.error('FATAL', e);
  process.exit(1);
});
