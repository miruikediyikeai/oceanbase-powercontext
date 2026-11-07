import { env, pipeline } from '@huggingface/transformers';
import { MODEL } from '../shared/config.js';
env.allowRemoteModels = false;
env.allowLocalModels = true;
env.localModelPath = '/models/';
env.useBrowserCache = true;
env.backends.onnx.wasm.numThreads = 1;
env.backends.onnx.wasm.wasmPaths = '/runtime/';
let extractor, backend, precision, loadMs, fallbackReason;
async function init({ dtype = 'auto', forceWasm = false }) {
  if (extractor) return { backend, precision, loadMs, fallbackReason, cached: true };
  const start = performance.now();
  let gpu = false;
  if (!forceWasm && self.navigator.gpu) {
    try { gpu = !!(await self.navigator.gpu.requestAdapter()); } catch { /* auto fallback */ }
  }
  const create = (device, selectedDtype) => pipeline('feature-extraction', MODEL.id, {
    device, dtype: selectedDtype, revision: MODEL.revision,
    progress_callback: p => postMessage({ event: 'progress', data: { file: p.file, status: p.status, progress: p.progress } }),
  });
  if (gpu) {
    try { precision = dtype === 'auto' ? 'fp16' : dtype; extractor = await create('webgpu', precision); backend = 'webgpu'; }
    catch (e) { fallbackReason = 'WebGPU 模型初始化失败：' + e.message; }
  } else fallbackReason = forceWasm ? '已强制 WASM（降级验证）' : '浏览器未提供可用 WebGPU adapter';
  if (!extractor) { precision = dtype === 'auto' ? 'q8' : dtype; extractor = await create('wasm', precision); backend = 'wasm'; }
  extractor.tokenizer.model_max_length = MODEL.maxLength;
  loadMs = performance.now() - start;
  const modelResources = performance.getEntriesByType('resource').filter(r => r.name.includes('/models/')).map(r => ({
    file: r.name.split('/models/')[1], durationMs: r.duration, transferBytes: r.transferSize,
    decodedBytes: r.decodedBodySize, startTime: r.startTime, responseEnd: r.responseEnd,
  }));
  const modelFetchWallMs = modelResources.length ? Math.max(...modelResources.map(r => r.responseEnd)) - Math.min(...modelResources.map(r => r.startTime)) : 0;
  return { backend, precision, loadMs, fallbackReason, cached: false, modelFetchWallMs, modelResources };
}
async function handle({ id, action, data }) {
  try {
    let result;
    if (action === 'init') result = await init(data);
    else if (action === 'embed') {
      if (!extractor) throw new Error('请先加载模型');
      const start = performance.now();
      const texts = data.query ? data.texts.map(x => MODEL.queryPrefix + x) : data.texts;
      const output = await extractor(texts, { pooling: MODEL.pooling, normalize: true,
        truncation: true, max_length: MODEL.maxLength });
      result = { vectors: output.tolist(), embedMs: performance.now() - start, backend, precision };
    } else throw new Error('Unknown worker action');
    postMessage({ id, result });
  } catch (e) { postMessage({ id, error: e.message }); }
}
// Serialize worker requests; ORT sessions and pipeline initialization are shared.
let queue = Promise.resolve();
self.onmessage = e => { queue = queue.then(() => handle(e.data)); };
