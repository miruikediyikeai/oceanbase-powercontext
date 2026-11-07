import { chromium } from 'playwright';
export async function browserSession({ gpu = false } = {}) {
  const browser = await chromium.launch({ channel: process.env.BROWSER_CHANNEL || undefined, headless: true, args: gpu
    ? ['--enable-unsafe-webgpu', '--ignore-gpu-blocklist', '--enable-features=Vulkan']
    : ['--disable-gpu'] });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
  const page = await context.newPage();
  const errors = [], external = [];
  page.on('pageerror', e => errors.push(e.message));
  // A real network guard: inference and retrieval must work with all remote hosts blocked.
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (!['localhost', '127.0.0.1'].includes(url.hostname)) { external.push(url.href); return route.abort(); }
    return route.continue();
  });
  page.on('console', msg => { if (msg.type() === 'error') console.error('Browser:', msg.text().slice(0, 350)); });
  await page.goto(process.env.APP_URL || 'http://127.0.0.1:5173');
  await page.waitForFunction(() => window.localSeek && !document.querySelector('#load').disabled);
  return { browser, context, page, errors, external };
}
