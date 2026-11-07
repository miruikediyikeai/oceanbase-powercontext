import { MODEL_KEY } from '../shared/config.js';
import { chunkText, digest } from '../shared/text.js';
export async function api(url, body) {
  const response = await fetch('/api' + url, body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}
export class Engine {
  constructor(progress = () => {}) {
    this.worker = new Worker(new URL('./embedding.worker.js', import.meta.url), { type: 'module' });
    this.pending = new Map(); this.sequence = 0; this.progress = progress;
    this.worker.onmessage = ({ data }) => {
      if (data.event) return progress(data.data);
      const request = this.pending.get(data.id); if (!request) return;
      this.pending.delete(data.id); data.error ? request.reject(new Error(data.error)) : request.resolve(data.result);
    };
    this.worker.onerror = e => { for (const p of this.pending.values()) p.reject(new Error(e.message)); this.pending.clear(); };
  }
  call(action, data) { return new Promise((resolve, reject) => { const id = ++this.sequence; this.pending.set(id, { resolve, reject }); this.worker.postMessage({ id, action, data }); }); }
  init(options) { return this.call('init', options); }
  embed(texts, query = false) { return this.call('embed', { texts, query }); }
  async importDocuments(collection, docs, batchSize = 8, refreshEveryBatch = false, onProgress = () => {}) {
    const totalStart = performance.now(), chunks = [];
    for (const doc of docs) {
      const documentId = doc.id || await digest(doc.name + '\n' + doc.text);
      let i = 0;
      for (const text of chunkText(doc.text)) {
        const chunk = i++;
        chunks.push({ id: await digest(documentId + ':' + chunk + ':' + text), text,
          metadata: { source: doc.name, chunk, documentId, importedAt: new Date().toISOString() } });
      }
    }
    if (!chunks.length) throw new Error('文档中没有可导入的文本');
    let embedMs = 0, writeMs = 0, refreshMs = 0;
    for (let i = 0; i < chunks.length; i += batchSize) {
      const batch = chunks.slice(i, i + batchSize), encoded = await this.embed(batch.map(c => c.text));
      embedMs += encoded.embedMs;
      const write = await api(`/collections/${collection}/chunks`, { modelKey: MODEL_KEY,
        chunks: batch.map((c, j) => ({ ...c, vector: encoded.vectors[j] })) });
      writeMs += write.writeMs;
      if (refreshEveryBatch) refreshMs += (await api(`/collections/${collection}/refresh`, {})).refreshMs;
      onProgress({ done: Math.min(i + batchSize, chunks.length), total: chunks.length });
    }
    if (!refreshEveryBatch) refreshMs += (await api(`/collections/${collection}/refresh`, {})).refreshMs;
    return { chunks: chunks.length, embedMs, writeMs, refreshMs, totalMs: performance.now() - totalStart, throughput: chunks.length / (embedMs / 1000) };
  }
  async search(collection, text, k = 5, mode = 'hybrid') {
    const start = performance.now(), embedding = await this.embed([text], true);
    const response = await api(`/collections/${collection}/search`, { modelKey: MODEL_KEY, text, vector: embedding.vectors[0], k, mode });
    return { ...response, queryEmbedMs: embedding.embedMs, e2eMs: performance.now() - start };
  }
  dispose() { this.worker.terminate(); for (const p of this.pending.values()) p.reject(new Error('Worker disposed')); this.pending.clear(); }
}
