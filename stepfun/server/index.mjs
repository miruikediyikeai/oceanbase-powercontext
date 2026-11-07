import express from 'express';
import { z } from 'zod';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { existsSync } from 'node:fs';
import { loadEnvFile } from 'node:process';
import { Store } from './store.mjs';
import { MODEL, MODEL_KEY } from '../shared/config.js';
const root = fileURLToPath(new URL('../', import.meta.url));
if (existsSync(path.join(root, '.env'))) loadEnvFile(path.join(root, '.env'));
const store = new Store({ host: process.env.SEEKDB_HOST || '127.0.0.1', port: Number(process.env.SEEKDB_PORT || 2881),
  user: process.env.SEEKDB_USER || 'root', password: process.env.SEEKDB_PASSWORD || '', database: process.env.SEEKDB_DATABASE || 'stepfun' });
await store.initialize();
const app = express();
app.disable('x-powered-by');
app.use((req, res, next) => {
  // Local application: prevent other websites from driving the local write API.
  const origin = req.headers.origin;
  if (origin && origin !== `${req.protocol}://${req.headers.host}`) return res.status(403).json({ error: 'Cross-origin request rejected' });
  next();
});
app.use(express.json({ limit: '12mb' }));
const nameSchema = z.string().regex(/^kb_[a-z0-9_]{1,48}$/);
const vectorSchema = z.array(z.number().finite()).length(MODEL.dimension).refine(v => v.some(x => x !== 0), 'Zero vector');
const identity = z.literal(MODEL_KEY);
app.get('/api/health', async (req, res) => {
  const info = await store.serial(() => store.client.execute('SELECT VERSION() AS version, 1+1 AS sqlCheck'));
  res.json({ ok: true, database: info[0], model: MODEL, modelKey: MODEL_KEY });
});
app.get('/api/collections', async (req, res) => res.json(await store.list()));
app.post('/api/collections', async (req, res) => {
  const { name } = z.object({ name: nameSchema }).parse(req.body); res.status(201).json(await store.create(name));
});
app.post('/api/collections/:name/chunks', async (req, res) => {
  const name = nameSchema.parse(req.params.name);
  const body = z.object({ modelKey: identity, chunks: z.array(z.object({
    id: z.string().regex(/^[a-f0-9]{64}$/), text: z.string().min(1).max(2000), vector: vectorSchema,
    metadata: z.object({ source: z.string().max(255), chunk: z.number().int().nonnegative(),
      documentId: z.string().max(128), importedAt: z.string().max(40) }),
  })).min(1).max(64) }).parse(req.body);
  res.json(await store.write(name, body.chunks));
});
app.post('/api/collections/:name/refresh', async (req, res) => res.json(await store.refresh(nameSchema.parse(req.params.name))));
app.post('/api/collections/:name/search', async (req, res) => {
  const body = z.object({ modelKey: identity, text: z.string().trim().min(1).max(500), vector: vectorSchema,
    k: z.number().int().min(1).max(20).default(5), mode: z.enum(['hybrid', 'vector']).default('hybrid') }).parse(req.body);
  res.json(await store.search(nameSchema.parse(req.params.name), body.text, body.vector, body.k, body.mode));
});
app.use('/api', (req, res) => res.status(404).json({ error: 'API route not found' }));
app.use('/models', express.static(path.join(root, 'public/models'), { maxAge: '1y', immutable: true }));
app.use('/runtime', express.static(path.join(root, 'node_modules/onnxruntime-web/dist'), { maxAge: '1y' }));
if (process.argv.includes('--production')) {
  app.use(express.static(path.join(root, 'dist')));
} else {
  const { createServer } = await import('vite');
  const vite = await createServer({ root, server: { middlewareMode: true }, appType: 'spa' });
  app.use(vite.middlewares);
}
app.use((err, req, res, next) => {
  console.error(err.message);
  res.status(err instanceof z.ZodError ? 400 : err.status || 500).json({ error: err instanceof z.ZodError ? z.prettifyError(err) : err.message });
});
const server = app.listen(Number(process.env.PORT || 5173), process.env.HOST || '127.0.0.1', () => console.log('LocalSeek: http://localhost:' + (process.env.PORT || 5173)));
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => server.close(async () => { await store.client.close(); process.exit(0); }));
