import { mkdir, writeFile, rename, stat, readFile } from 'node:fs/promises';
import { createWriteStream, createReadStream } from 'node:fs';
import { pipeline } from 'node:stream/promises';
import { createHash } from 'node:crypto';
import https from 'node:https';
import { MODEL } from '../shared/config.js';
const root = new URL('../public/models/' + MODEL.id + '/', import.meta.url);
const files = ['config.json', 'tokenizer.json', 'tokenizer_config.json', 'special_tokens_map.json', 'vocab.txt', 'quantize_config.json',
  'onnx/model.onnx', 'onnx/model_quantized.onnx', 'onnx/model_fp16.onnx'];
const manifest = { model: MODEL, generatedAt: new Date().toISOString(), files: [] };
// Git LFS SHA-256 at the pinned revision; Xet/CDN ETags are not blob hashes.
const hashes = { 'onnx/model.onnx': '69a0b846f4f116b5e6aabf9546ea6754d02264f3211a13a1bd69b31b8040749a',
  'onnx/model_quantized.onnx': '15b717c382bcb518ba457b93ea6850ede7f4f1cd8937454aa06972366cd19bcc',
  'onnx/model_fp16.onnx': '60d0fe5eb8d4e251d4b24dff010a2625c10a013503f0d09f625e8a9b0311315f' };
function open(url, redirects = 0) {
  return new Promise((resolve, reject) => {
    const req = https.get(url, { headers: { 'Accept-Encoding': 'identity' } }, response => {
      if (response.statusCode >= 300 && response.statusCode < 400 && response.headers.location) {
        response.resume();
        if (redirects > 8) return reject(new Error('Too many model redirects'));
        resolve(open(new URL(response.headers.location, url), redirects + 1));
      } else if (response.statusCode !== 200) { response.resume(); reject(new Error(`HTTP ${response.statusCode} downloading model`)); }
      else resolve(response);
    });
    req.setTimeout(120000, () => req.destroy(new Error('Model download timeout')));
    req.on('error', reject);
  });
}
async function hashFile(file) { const hash = createHash('sha256'); for await (const block of createReadStream(file)) hash.update(block); return hash.digest('hex'); }
for (const file of files) {
  const target = new URL(file, root); await mkdir(new URL('./', target), { recursive: true });
  const url = `https://huggingface.co/${MODEL.id}/resolve/${MODEL.revision}/${file}`;
  const head = await fetch(url, { method: 'HEAD', headers: { 'Accept-Encoding': 'identity' }, signal: AbortSignal.timeout(90000) });
  if (!head.ok) throw new Error(`Model HEAD ${head.status}: ${file}`);
  const expected = Number(head.headers.get('content-length'));
  const existing = await stat(target).catch(() => null);
  if (!existing || (expected && existing.size !== expected)) {
    console.log('Downloading', file);
    const response = await open(url);
    const partial = new URL(file + '.partial', root);
    await pipeline(response, createWriteStream(partial));
    if (expected && (await stat(partial)).size !== expected) throw new Error('Incomplete download: ' + file);
    await rename(partial, target);
  }
  const sha256 = await hashFile(target), bytes = (await stat(target)).size;
  if (hashes[file] && sha256 !== hashes[file]) throw new Error('Model checksum mismatch: ' + file);
  manifest.files.push({ file, bytes, sha256 }); console.log(file, bytes, sha256);
}
const config = JSON.parse(await readFile(new URL('config.json', root), 'utf8'));
if (config.hidden_size !== MODEL.dimension) throw new Error(`Model dimension ${config.hidden_size} differs from app ${MODEL.dimension}`);
const common = manifest.files.filter(x => !x.file.endsWith('.onnx')).reduce((s, x) => s + x.bytes, 0);
manifest.sizes = Object.fromEntries(['model.onnx', 'model_quantized.onnx', 'model_fp16.onnx'].map(name => [name, common + manifest.files.find(x => x.file === 'onnx/' + name).bytes]));
manifest.compressionRatio = manifest.sizes['model.onnx'] / manifest.sizes['model_quantized.onnx'];
await writeFile(new URL('../public/models/manifest.json', import.meta.url), JSON.stringify(manifest, null, 2));
await mkdir(new URL('../evidence/', import.meta.url), { recursive: true });
await writeFile(new URL('../evidence/model-manifest.json', import.meta.url), JSON.stringify(manifest, null, 2));
console.log('Model files verified. INT8 compression ratio:', manifest.compressionRatio);
