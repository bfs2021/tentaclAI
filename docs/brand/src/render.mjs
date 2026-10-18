// Renders the pages written by gen.py (in /b) to PNGs – runs inside the Playwright container (scripts/build-brand.sh)
import { chromium } from 'playwright';
const b = await chromium.launch();
let p = await b.newPage({ viewport: { width: 1280, height: 640 } });
await p.goto('file:///b/social.html'); await p.evaluate(() => document.fonts.ready); await p.waitForTimeout(500);
await p.screenshot({ path: '/b/social-preview.png' }); await p.close();
for (const [mode, t] of [['dark', '#eef2f8'], ['light', '#14181f']]) {
  p = await b.newPage({ viewport: { width: 1000, height: 220 }, deviceScaleFactor: 2 });
  await p.goto('file:///b/word.html'); await p.addStyleTag({ content: `:root{--t:${t}} html,body{background:transparent}` });
  await p.evaluate(() => document.fonts.ready); await p.waitForTimeout(300);
  await p.screenshot({ path: `/b/wordmark-on-${mode}.png`, omitBackground: true }); await p.close();
}
await b.close(); console.log('ok');
