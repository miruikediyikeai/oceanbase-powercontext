import { SeekdbClient, Schema, VectorIndexConfig, FulltextIndexConfig } from 'seekdb';
import { MODEL, MODEL_KEY } from '../shared/config.js';

// hybridSearch uses SET @search_parm followed by GET_SQL. Serialize complete
// operations on this single SDK connection to prevent session-variable races.
export class Store {
  constructor(options) { this.client = new SeekdbClient(options); this.tail = Promise.resolve(); }
  serial(fn) { const run = this.tail.then(fn); this.tail = run.catch(() => {}); return run; }
  async initialize() {
    const [{ version }] = await this.client.execute('SELECT VERSION() AS version');
    const match = /seekdb[-v ]*(\d+)\.(\d+)/i.exec(version);
    if (!match || Number(match[1]) < 1 || (Number(match[1]) === 1 && Number(match[2]) < 4)) {
      throw new Error('需要 seekdb 1.4+，当前 SQL 实例版本：' + version);
    }
    await this.client.execute(`CREATE TABLE IF NOT EXISTS kb_catalog (
      name VARCHAR(64) PRIMARY KEY, model_key VARCHAR(255) NOT NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)`);
  }
  async collection(name) {
    const rows = await this.client.execute('SELECT model_key FROM kb_catalog WHERE name = ?', [name]);
    if (!rows.length) throw Object.assign(new Error('知识库不存在，请先创建。'), { status: 404 });
    if (rows[0].model_key !== MODEL_KEY) throw new Error('模型版本不匹配，请创建新知识库。');
    return this.client.getCollection({ name, embeddingFunction: null });
  }
  list() { return this.serial(async () => {
    const rows = await this.client.execute('SELECT name, created_at FROM kb_catalog ORDER BY created_at, name');
    return Promise.all(rows.map(async r => ({ ...r, count: await (await this.collection(r.name)).count() })));
  }); }
  create(name) { return this.serial(async () => {
    const existing = await this.client.execute('SELECT model_key FROM kb_catalog WHERE name = ?', [name]);
    if (existing.length && existing[0].model_key !== MODEL_KEY) throw new Error('模型版本不匹配，请创建新知识库。');
    // getOrCreateCollection in SDK 1.4 reopens an existing collection without
    // forwarding null embeddingFunction. Reopen explicitly to forbid default EF.
    if (await this.client.hasCollection(name)) await this.client.getCollection({ name, embeddingFunction: null });
    else await this.client.createCollection({ name, schema: new Schema({
        fulltextIndex: new FulltextIndexConfig('ik', { ik_mode: 'smart' }),
        vectorIndex: new VectorIndexConfig({ hnsw: { dimension: MODEL.dimension, distance: 'cosine' }, embeddingFunction: null }),
      }) });
    await this.client.execute('INSERT IGNORE INTO kb_catalog (name, model_key) VALUES (?, ?)', [name, MODEL_KEY]);
    return { name };
  }); }
  write(name, chunks) { return this.serial(async () => {
    const col = await this.collection(name), start = performance.now();
    await col.upsert({ ids: chunks.map(c => c.id), documents: chunks.map(c => c.text),
      embeddings: chunks.map(c => c.vector), metadatas: chunks.map(c => c.metadata) });
    return { writeMs: performance.now() - start, count: await col.count() };
  }); }
  refresh(name) { return this.serial(async () => {
    const col = await this.collection(name), start = performance.now();
    await col.refresh_index();
    return { refreshMs: performance.now() - start, count: await col.count() };
  }); }
  search(name, text, vector, k, mode = 'hybrid') { return this.serial(async () => {
    const col = await this.collection(name), start = performance.now();
    const result = mode === 'vector'
      ? await col.query({ queryEmbeddings: [vector], nResults: k, include: ['documents', 'metadatas', 'distances'] })
      : await col.hybridSearch({
        query: { whereDocument: { $contains: text }, nResults: Math.max(k, 30) },
        knn: { queryEmbeddings: [vector], nResults: Math.max(k, 30) },
        rank: { rrf: { rankWindowSize: 30, rankConstant: 60 } }, nResults: k,
        include: ['documents', 'metadatas', 'distances'],
      });
    return { searchMs: performance.now() - start, mode,
      results: (result.ids[0] || []).map((id, i) => ({ id, text: result.documents?.[0]?.[i],
        metadata: result.metadatas?.[0]?.[i], score: result.distances?.[0]?.[i] })) };
  }); }
}
