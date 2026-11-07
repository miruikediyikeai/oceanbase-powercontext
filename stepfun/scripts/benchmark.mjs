import { mkdir, writeFile, readFile } from 'node:fs/promises';
import { browserSession } from './browser.mjs';
import { median } from '../shared/text.js';
const dataset = JSON.parse(await readFile(new URL('../public/data/evaluation.json', import.meta.url), 'utf8'));
const runs = Number(process.env.BENCH_RUNS || 3);
const configs = [
  { name: 'FP32 WASM baseline', dtype: 'fp32', forceWasm: true, batchSize: 1, refreshEveryBatch: true },
  { name: 'INT8 WASM unoptimized', dtype: 'q8', forceWasm: true, batchSize: 1, refreshEveryBatch: true },
  { name: 'INT8 WASM batched', dtype: 'q8', forceWasm: true, batchSize: 8, refreshEveryBatch: true },
  { name: 'INT8 WASM optimized', dtype: 'q8', forceWasm: true, batchSize: 8, refreshEveryBatch: false },
  { name: 'FP16 WebGPU preferred', dtype: 'fp16', forceWasm: false, batchSize: 8, refreshEveryBatch: false, gpu: true },
];
const collectionName = `kb_benchmark_${Date.now()}`;
const output = { at: new Date().toISOString(), runs, collectionName, datasetDocuments: dataset.documents.length, queries: dataset.queries.length, configs: [] };
await mkdir(new URL('../evidence/', import.meta.url), { recursive: true });
for (const config of configs) {
  if (process.env.BENCH_FILTER && !config.name.includes(process.env.BENCH_FILTER)) continue;
  console.log('Benchmark:', config.name);
  const session = await browserSession({ gpu: config.gpu });
  try {
    const result = await session.page.evaluate(async ({ config, dataset, runs, collectionName }) => {
      const { Engine, api } = window.localSeek;
      const engine = new Engine(); const load = await engine.init(config);
      const hardware = { userAgent: navigator.userAgent, concurrency: navigator.hardwareConcurrency,
        deviceMemory: navigator.deviceMemory, gpu: null };
      if (navigator.gpu) { const a = await navigator.gpu.requestAdapter(); if (a) hardware.gpu = { vendor: a.info?.vendor, architecture: a.info?.architecture, device: a.info?.device, description: a.info?.description, features: [...a.features] }; }
      const name = collectionName; await api('/collections', { name });
      const imports = [];
      // First-ever inference is kept separately; subsequent imports are warm.
      const firstInference = await engine.embed([dataset.documents[0].text]);
      for (let i = 0; i < runs; i++) imports.push(await engine.importDocuments(name, dataset.documents, config.batchSize, config.refreshEveryBatch));
      const queries = [];
      const coldQuery = await engine.search(name, dataset.queries[0].text);
      for (let repetition = 0; repetition < runs; repetition++) {
        for (const query of dataset.queries) {
          const start = performance.now(), found = await engine.search(name, query.text, 5);
          // Include a real DOM update and an animation frame in display latency.
          const node = document.querySelector('#results'); node.textContent = found.results.map(x => x.text).join('\n');
          await new Promise(requestAnimationFrame);
          const displayMs = performance.now() - start;
          const ids = [...new Set(found.results.map(x => x.metadata.documentId))].slice(0, 5);
          const truth = new Set(query.relevant), recall = ids.filter(x => truth.has(x)).length / truth.size;
          const dcg = ids.reduce((s, x, i) => s + (truth.has(x) ? 1 / Math.log2(i + 2) : 0), 0);
          const ideal = Array.from({ length: Math.min(5, truth.size) }, (_, i) => 1 / Math.log2(i + 2)).reduce((a, b) => a + b, 0);
          queries.push({ repetition, query: query.text, ids, relevant: query.relevant, recall, ndcg: dcg / ideal,
            queryEmbedMs: found.queryEmbedMs, searchMs: found.searchMs, e2eMs: found.e2eMs, displayMs });
        }
      }
      engine.dispose(); return { load, hardware, firstInferenceMs: firstInference.embedMs, coldQuery, imports, queries };
    }, { config, dataset, runs, collectionName });
    const average = key => result.queries.reduce((s, q) => s + q[key], 0) / result.queries.length;
    output.configs.push({ config, ...result, browserVersion: await session.browser.version(), externalRequests: session.external, browserErrors: session.errors,
      summary: { throughput: median(result.imports.map(x => x.throughput)), importTotalMs: median(result.imports.map(x => x.totalMs)),
        embedMs: median(result.imports.map(x => x.embedMs)), writeMs: median(result.imports.map(x => x.writeMs)),
        refreshMs: median(result.imports.map(x => x.refreshMs)), queryEmbedMs: median(result.queries.map(x => x.queryEmbedMs)),
        searchMs: median(result.queries.map(x => x.searchMs)), e2eMs: median(result.queries.map(x => x.e2eMs)),
        displayMs: median(result.queries.map(x => x.displayMs)), recall5: average('recall'), ndcg5: average('ndcg') } });
    console.log(JSON.stringify(output.configs.at(-1).summary));
  } catch (e) { output.configs.push({ config, error: e.message }); console.error(e.message); }
  finally { await session.browser.close(); await writeFile(new URL('../evidence/benchmark-raw.json', import.meta.url), JSON.stringify(output, null, 2)); }
}
if (output.configs.some(x => x.error || x.externalRequests?.length || x.browserErrors?.length)) process.exitCode = 1;
