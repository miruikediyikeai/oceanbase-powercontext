import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { browserSession } from './browser.mjs';
const { browser, page, errors, external } = await browserSession({ gpu: true });
const report = { at: new Date().toISOString(), appUrl: process.env.APP_URL || 'http://127.0.0.1:5173', browser: await browser.version() };
try {
  await page.evaluate(async () => {
    await window.localSeek.api('/collections', { name: 'kb_webgpu_demo' });
    localStorage.setItem('localseek.collection', 'kb_webgpu_demo');
  });
  await page.reload(); await page.waitForFunction(() => window.localSeek && !document.querySelector('#load').disabled);
  await page.locator('#sample').click();
  await page.waitForFunction(() => document.querySelector('#file-summary').textContent.includes('24 份'));
  await page.locator('#import').click();
  await page.waitForFunction(() => document.querySelector('#progress-label').textContent.includes('已导入并刷新'), null, { timeout: 180000 });
  report.backend = await page.locator('#backend').textContent();
  report.precision = await page.locator('#precision-label').textContent();
  assert.equal(report.backend, 'WEBGPU'); assert.equal(report.precision, 'FP16');
  await page.locator('#query').fill('浏览器不支持 WebGPU 时怎么办？'); await page.locator('#search').click();
  await page.waitForSelector('.result', { timeout: 60000 });
  report.firstSource = await page.locator('.result h3').first().textContent();
  assert.equal(report.firstSource, 'WebGPU 降级策略.md');
  report.timings = await page.locator('#results-info').textContent();
  report.logs = await page.locator('#log').textContent();
  await page.evaluate(() => { document.querySelector('#toast').hidden = true; window.scrollTo(0, 0); });
  await page.evaluate(() => new Promise(requestAnimationFrame));
  await page.screenshot({ path: fileURLToPath(new URL('../evidence/application-webgpu.png', import.meta.url)), fullPage: true });
  report.errors = errors; report.externalRequests = external;
  assert.deepEqual(errors, []); assert.deepEqual(external, []); report.passed = true;
  console.log(JSON.stringify(report, null, 2));
} finally { await writeFile(new URL('../evidence/webgpu.json', import.meta.url), JSON.stringify(report, null, 2)); await browser.close(); }
