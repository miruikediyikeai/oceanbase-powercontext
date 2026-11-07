import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { browserSession } from './browser.mjs';
import { Store } from '../server/store.mjs';
import { loadEnvFile } from 'node:process';
try { loadEnvFile(); } catch (e) { if (e.code !== 'ENOENT') throw e; }
const { browser, page, errors, external } = await browserSession();
const report = { at: new Date().toISOString(), appUrl: process.env.APP_URL || 'http://127.0.0.1:5173', checks: [] };
await mkdir(new URL('../evidence/', import.meta.url), { recursive: true });
try {
  report.browser = await browser.version();
  const result = await page.evaluate(async () => {
    const { Engine, api } = window.localSeek, engine = new Engine();
    const info = await engine.init({ dtype: 'auto', forceWasm: false });
    const suffix = Date.now(), a = `kb_e2e_a_${suffix}`, b = `kb_e2e_b_${suffix}`;
    await api('/collections', { name: a }); await api('/collections', { name: b });
    await api('/collections', { name: a });
    const first = [{ id: 'first', name: '第一份.md', text: '极光紫丁香号探测器在木星轨道进行磁场测量，任务编号 AURORA-731。' }];
    const second = [{ id: 'second', name: '新增文档.md', text: '青竹白鹭项目利用太阳能淡化海水，项目编号 BAMBOO-928。' }];
    const other = [{ id: 'other', name: '其他知识库.md', text: '银杏渡船计划在山区修建图书馆，项目编号 GINKGO-615。' }];
    const importTiming = await engine.importDocuments(a, first);
    const countFirst = (await api('/collections')).find(x => x.name === a).count;
    await engine.importDocuments(a, first);
    const countRepeat = (await api('/collections')).find(x => x.name === a).count;
    await engine.importDocuments(a, second); await engine.importDocuments(b, other);
    const incremental = await engine.search(a, '青竹白鹭 太阳能 海水');
    const isolated = await engine.search(b, '青竹白鹭 太阳能 海水');
    const original = await engine.search(a, '极光紫丁香 木星 磁场');
    const vectorOnly = await engine.search(a, '木星轨道探测器', 5, 'vector');
    const concurrent = await Promise.all([engine.search(a, '极光紫丁香 木星 磁场'), engine.search(b, '银杏渡船 图书馆')]);
    const e = await engine.embed(['这是一个中文向量维度与归一化测试。']);
    const validation = await fetch(`/api/collections/${a}/search`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: 'test', vector: [0], modelKey: 'wrong' }) });
    engine.dispose();
    return { info, collections: [a, b], countFirst, countRepeat, importTiming, incremental, isolated, original, vectorOnly, concurrent,
      vectorDimension: e.vectors[0].length, vectorNorm: Math.hypot(...e.vectors[0]), invalidStatus: validation.status };
  });
  assert.equal(result.info.backend, 'wasm'); assert.equal(result.countFirst, result.countRepeat);
  assert.equal(result.vectorDimension, 512); assert.ok(Math.abs(result.vectorNorm - 1) < 0.001);
  assert.equal(result.incremental.results[0].metadata.documentId, 'second');
  assert.ok(result.isolated.results.every(x => x.metadata.documentId === 'other'));
  assert.equal(result.original.results[0].metadata.documentId, 'first'); assert.equal(result.invalidStatus, 400);
  assert.equal(result.info.precision, 'q8'); assert.ok(result.info.fallbackReason.includes('未提供可用 WebGPU'));
  assert.equal(result.vectorOnly.results[0].metadata.documentId, 'first');
  assert.equal(result.concurrent[0].results[0].metadata.documentId, 'first');
  assert.equal(result.concurrent[1].results[0].metadata.documentId, 'other');
  const restored = new Store({ host: process.env.SEEKDB_HOST || '127.0.0.1', port: Number(process.env.SEEKDB_PORT || 2881), user: process.env.SEEKDB_USER || 'root', password: process.env.SEEKDB_PASSWORD || '', database: process.env.SEEKDB_DATABASE || 'stepfun' });
  try {
    await restored.initialize(); const catalog = await restored.list();
    assert.equal(catalog.find(x => x.name === result.collections[0]).count, 2);
    assert.equal(catalog.find(x => x.name === result.collections[1]).count, 1);
    report.checks.push('catalog and data recovered through a new DB session');
  } finally { await restored.client.close(); }
  report.checks.push('automatic WASM fallback with unavailable GPU', 'WASM local embedding', '512-dimensional normalized vectors', 'idempotent upsert', 'incremental import visible after refresh', 'collection isolation', 'database hybrid search', 'vector-only search', 'concurrent collection queries', 'invalid vector rejected');
  // Exercise the actual UI with another real Worker and actual seekdb.
  await page.locator('.settings summary').click();
  await page.locator('#force-wasm').check();
  await page.locator('#sample').click();
  await page.waitForFunction(() => document.querySelector('#file-summary').textContent.includes('24 份'));
  await page.locator('#doc-title').fill('界面验收.md');
  await page.locator('#doc-text').fill('界面验收文档：浏览器通过 Web Worker 在本地完成向量化，所有知识保存在 seekdb。');
  await page.locator('#import').click();
  await page.waitForFunction(() => document.querySelector('#progress-label').textContent.includes('已导入并刷新'), null, { timeout: 180000 });
  await page.locator('#query').fill('浏览器如何在本地完成向量化？'); await page.locator('#search').click();
  await page.waitForSelector('.result', { timeout: 60000 });
  assert.equal(await page.locator('#backend').textContent(), 'WASM');
  await page.locator('#refresh').click();
  await page.waitForFunction(() => !document.querySelector('#refresh').disabled);
  await page.evaluate(() => document.querySelector('#toast').hidden = true);
  const originCheck = await fetch(new URL('/api/collections', process.env.APP_URL || 'http://127.0.0.1:5173'), {
    method: 'POST', headers: { 'Content-Type': 'application/json', Origin: 'https://example.invalid' }, body: JSON.stringify({ name: 'kb_forbidden_origin' }),
  });
  assert.equal(originCheck.status, 403);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.evaluate(() => new Promise(requestAnimationFrame));
  await mkdir(new URL('../evidence/', import.meta.url), { recursive: true });
  await page.screenshot({ path: new URL('../evidence/application-desktop.png', import.meta.url).pathname.replace(/^\/([A-Z]:)/, '$1'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  await page.screenshot({ path: new URL('../evidence/application-mobile.png', import.meta.url).pathname.replace(/^\/([A-Z]:)/, '$1'), fullPage: true });
  report.checks.push('UI sample and pasted document import and search', 'manual index refresh', 'cross-origin API writes rejected', 'backend visible', 'mobile viewport without overflow', 'all external hosts blocked');
  report.result = result; report.errors = errors; report.externalRequests = external;
  assert.deepEqual(errors, []); assert.deepEqual(external, []);
  report.passed = true; console.log(JSON.stringify(report, null, 2));
} finally { await writeFile(new URL('../evidence/e2e.json', import.meta.url), JSON.stringify(report, null, 2)); await browser.close(); }
