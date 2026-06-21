const { chromium } = require('playwright');
const fs = require('fs');
const OUT = '/tmp/webcam_shots';
const URL = 'http://127.0.0.1:8899/';

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function settle(page) {
  // let images/lucide icons/charts paint
  await page.waitForLoadState('networkidle').catch(() => {});
  await sleep(1800);
  // Freeze animations (incl. ::before/::after pseudo-elements — the universal
  // selector alone does NOT match pseudo-elements, e.g. .skeleton-card::after)
  // and hide transient loading spinners so the compositor settles.
  await page.addStyleTag({ content:
    `*, *::before, *::after { animation: none !important; transition: none !important; }
     [class*=spin],[class*=loading],.loader,.spinner{display:none!important;}` }).catch(()=>{});
  await sleep(300);
}

// Kill all JS timers (status panel re-renders every 5s, polling, etc.) so the
// page stops mutating and the compositor settles — otherwise card-grid views
// repaint forever and screenshots time out.
async function freezeTimers(page) {
  await page.evaluate(() => {
    const hi = setTimeout(() => {}, 0);
    for (let i = 0; i <= hi; i++) { clearTimeout(i); clearInterval(i); }
  }).catch(() => {});
}

// Scroll through to trigger lazy-image loads, then back to top.
async function preloadLazy(page) {
  await page.evaluate(async () => {
    const sc = document.scrollingElement || document.body;
    const h = sc.scrollHeight;
    for (let y = 0; y <= h; y += 600) { sc.scrollTop = y; await new Promise(r => setTimeout(r, 120)); }
    sc.scrollTop = 0;
  }).catch(() => {});
  await page.waitForLoadState('networkidle').catch(() => {});
  await sleep(800);
  await freezeTimers(page);
  await sleep(200);
}

async function run() {
  const browser = await chromium.launch({ headless: true });
  const shots = [];
  const snap = async (page, name) => {
    const path = `${OUT}/${name}.png`;
    try {
      // 25s: the Timeline view renders up to 300 visit rows w/ thumbnails, so
      // captureScreenshot is slow-but-finite (~12s) — not a repaint hang.
      await page.screenshot({ path, fullPage: false, animations: 'disabled', caret: 'hide', timeout: 25000 });
      shots.push(name);
      console.log('shot', name);
    } catch (e) {
      console.log('SKIP', name, '-', e.message.split('\n')[0]);
    }
  };

  // ---------- DESKTOP 1440x900 ----------
  const dctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, reducedMotion: 'reduce' });
  const d = await dctx.newPage();
  d.on('pageerror', e => console.log('PAGEERR:', e.message.split('\n')[0]));
  await d.goto(URL, { waitUntil: 'domcontentloaded' });
  await settle(d);
  await freezeTimers(d);
  await snap(d, 'desktop-01-timeline-default');

  // Objects filter tab
  await d.click('button[data-filter="objects"]').catch(()=>{});
  await preloadLazy(d); await snap(d, 'desktop-02-objects-tab');

  // All tab (full gallery grid)
  await d.click('button[data-filter="all"]').catch(()=>{});
  await preloadLazy(d); await snap(d, 'desktop-03-all-grid-medium');

  // view toggles: large grid
  await d.click('button[data-view="large"]').catch(()=>{});
  await preloadLazy(d); await snap(d, 'desktop-04-all-large');
  // list view
  await d.click('button[data-view="list"]').catch(()=>{});
  await preloadLazy(d); await snap(d, 'desktop-05-all-list');
  // small
  await d.click('button[data-view="small"]').catch(()=>{});
  await preloadLazy(d); await snap(d, 'desktop-06-all-small');

  // Insights
  await d.click('text=Insights').catch(()=>{});
  await sleep(1200); await freezeTimers(d); await snap(d, 'desktop-07-insights');

  // Settings
  await d.click('text=Settings').catch(()=>{});
  await sleep(1200); await freezeTimers(d); await snap(d, 'desktop-08-settings');
  // close settings (Escape)
  await d.keyboard.press('Escape').catch(()=>{});
  await sleep(500);

  // Lightbox: click first card image
  await d.click('button[data-filter="all"]').catch(()=>{});
  await d.click('button[data-view="medium"]').catch(()=>{});
  await preloadLazy(d);
  const card = await d.$('.card-img, .card');
  if (card) { await card.click().catch(()=>{}); await sleep(1200); await freezeTimers(d); await snap(d, 'desktop-09-lightbox'); await d.keyboard.press('Escape').catch(()=>{}); }

  await dctx.close();

  // ---------- MOBILE 390x844 ----------
  const mctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, reducedMotion: 'reduce' });
  const m = await mctx.newPage();
  await m.goto(URL, { waitUntil: 'domcontentloaded' });
  await settle(m);
  await freezeTimers(m);
  await snap(m, 'mobile-01-timeline');

  await m.click('button[data-filter="all"]').catch(()=>{});
  await preloadLazy(m); await snap(m, 'mobile-02-all-grid');

  await m.click('text=Insights').catch(()=>{});
  await sleep(1200); await freezeTimers(m); await snap(m, 'mobile-03-insights');

  await m.click('text=Settings').catch(()=>{});
  await sleep(1200); await freezeTimers(m); await snap(m, 'mobile-04-settings');
  await m.keyboard.press('Escape').catch(()=>{});

  // mobile lightbox
  await m.click('button[data-filter="all"]').catch(()=>{});
  await preloadLazy(m);
  const mc = await m.$('.card-img, .card');
  if (mc) { await mc.click().catch(()=>{}); await sleep(1200); await freezeTimers(m); await snap(m, 'mobile-05-lightbox'); }

  await mctx.close();
  await browser.close();
  fs.writeFileSync(`${OUT}/manifest.json`, JSON.stringify(shots, null, 2));
  console.log('DONE', shots.length, 'shots');
}
run().catch(e => { console.error('FATAL', e); process.exit(1); });
