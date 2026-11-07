import './style.css';
import { Engine, api } from './engine.js';
const $ = id => document.getElementById(id);
let files = [], busy = false, loaded = false;
const logs = [], measurements = { loadMs: null, embedMs: null, writeMs: null, refreshMs: null, e2eMs: null };
function log(message, data) { logs.push({ at: new Date().toISOString(), message, ...data }); $('log').textContent = logs.slice(-25).map(x => `${x.at.slice(11, 19)}  ${x.message}`).join('\n'); $('log').scrollTop = $('log').scrollHeight; }
function notify(message, error = false) { $('toast').textContent = message; $('toast').className = error ? 'error' : ''; $('toast').hidden = false; clearTimeout(notify.timer); notify.timer = setTimeout(() => $('toast').hidden = true, error ? 15000 : 5000); }
const engine = new Engine(p => { $('progress-label').textContent = `${p.status || '加载'} ${p.file || ''} ${p.progress ? p.progress.toFixed(0) + '%' : ''}`; });
function renderMetrics() {
  const value = n => n === null ? '—' : Math.round(n).toLocaleString();
  const times = [measurements.loadMs, measurements.embedMs, measurements.writeMs === null ? null : measurements.writeMs + measurements.refreshMs, measurements.e2eMs];
  $('metrics').querySelectorAll('b').forEach((el, i) => { el.textContent = value(times[i]); el.append(Object.assign(document.createElement('small'), { textContent: ' ms' })); });
}
async function run(fn) {
  if (busy) return; busy = true;
  document.querySelectorAll('button, select, input, textarea').forEach(el => el.disabled = true);
  try { await fn(); } catch (e) { log('失败：' + e.message); notify(e.message, true); $('progress-label').textContent = '操作失败；已写入的片段保留，可重试或刷新索引。'; }
  finally { busy = false; document.querySelectorAll('button, select, input, textarea').forEach(el => el.disabled = false); if (loaded) { $('dtype').disabled = true; $('force-wasm').disabled = true; } renderMetrics(); }
}
async function initializeModel() {
  if (loaded) return;
  $('progress-label').textContent = '正在加载本地模型…';
  const info = await engine.init({ dtype: $('dtype').value, forceWasm: $('force-wasm').checked });
  loaded = true; measurements.loadMs = info.loadMs;
  $('backend').textContent = info.backend.toUpperCase(); $('precision-label').textContent = info.precision === 'q8' ? 'INT8' : info.precision.toUpperCase();
  $('progress-label').textContent = info.fallbackReason || 'WebGPU 加速已就绪';
  log(`模型就绪 · ${info.backend.toUpperCase()} / ${info.precision} · ${Math.round(info.loadMs)} ms${info.fallbackReason ? ' · ' + info.fallbackReason : ''}`, info);
}
async function collections(selected) {
  const list = await api('/collections'), previous = selected || $('collection').value || localStorage.getItem('localseek.collection');
  $('collection').replaceChildren(...list.map(c => Object.assign(document.createElement('option'), { value: c.name, textContent: c.name })));
  if (list.some(c => c.name === previous)) $('collection').value = previous;
  const active = list.find(c => c.name === $('collection').value); $('count').textContent = active?.count || 0;
  if (active) localStorage.setItem('localseek.collection', active.name);
}
$('collection').onchange = () => { localStorage.setItem('localseek.collection', $('collection').value); $('results').replaceChildren(); $('results-info').textContent = '已切换知识库，请重新检索'; run(() => collections()); };
$('create').onclick = () => { const raw = prompt('知识库名称（小写字母、数字或下划线）', 'notes'); if (!raw) return; const name = raw.startsWith('kb_') ? raw : 'kb_' + raw; run(async () => { await api('/collections', { name }); await collections(name); $('results').replaceChildren(); log('创建知识库 ' + name); }); };
$('files').onchange = e => { files = [...e.target.files]; $('file-summary').textContent = `已选择 ${files.length} 个文件`; };
for (const type of ['dragover', 'dragleave', 'drop']) $('dropzone').addEventListener(type, e => { e.preventDefault(); $('dropzone').classList.toggle('over', type === 'dragover'); if (type === 'drop' && !busy) { files = [...e.dataTransfer.files]; $('file-summary').textContent = `已选择 ${files.length} 个文件`; } });
let sampleDocs;
$('sample').onclick = () => run(async () => { sampleDocs = (await (await fetch('/data/evaluation.json')).json()).documents; $('file-summary').textContent = `${sampleDocs.length} 份示例文档待导入`; log('示例语料已就绪'); });
$('load').onclick = () => run(initializeModel);
const refreshButton = document.createElement('button'); refreshButton.id = 'refresh'; refreshButton.className = 'button secondary'; refreshButton.textContent = '刷新索引';
document.querySelector('.activity-actions').prepend(refreshButton);
refreshButton.onclick = () => run(async () => { const name = $('collection').value; if (!name) throw new Error('请选择知识库'); const result = await api(`/collections/${name}/refresh`, {}); await collections(name); log(`索引刷新完成 · ${Math.round(result.refreshMs)} ms`, result); notify('索引刷新完成'); });
for (const id of ['dtype', 'force-wasm']) $(id).onchange = () => { if (loaded) { notify('请刷新页面后加载新配置。'); $(id).value = id === 'dtype' ? $('precision-label').textContent.toLowerCase().replace('int8', 'q8') : $(id).value; } };
$('import').onclick = () => run(async () => {
  const collection = $('collection').value;
  if (!collection) throw new Error('请先创建知识库');
  if (files.length > 50 || files.some(f => f.size > 5 * 1024 * 1024)) throw new Error('最多 50 个文件，每个文件限 5 MB');
  const docs = [...(sampleDocs || []), ...await Promise.all(files.map(async f => ({ name: f.name, text: await f.text() })))];
  if ($('doc-text').value.trim()) docs.push({ name: $('doc-title').value.trim() || '粘贴的文档', text: $('doc-text').value });
  if (!docs.length) throw new Error('请选择文件、粘贴文本或载入示例');
  await initializeModel();
  const timing = await engine.importDocuments(collection, docs, Number($('batch').value), false, p => {
    $('progress-bar').style.width = `${p.done / p.total * 100}%`; $('progress-label').textContent = `本地向量化并写入 ${p.done} / ${p.total} · 等待索引刷新`; });
  Object.assign(measurements, timing); await collections(collection);
  $('progress-label').textContent = `${timing.chunks} 个片段已导入并刷新索引 · ${timing.throughput.toFixed(1)} 片段/秒`;
  log(`导入完成 · ${timing.chunks} 片段 · 向量化 ${Math.round(timing.embedMs)} ms · 写入 ${Math.round(timing.writeMs)} ms · 刷新 ${Math.round(timing.refreshMs)} ms`, { timing });
  files = []; sampleDocs = null; $('files').value = ''; $('file-summary').textContent = '已完成导入'; $('doc-text').value = ''; notify('导入完成，现在可以检索新增内容。');
});
function showResults(result) {
  $('results').replaceChildren();
  for (const [i, item] of result.results.entries()) {
    const article = document.createElement('article'); article.className = 'result';
    const heading = document.createElement('div'); heading.className = 'result-heading';
    const rank = document.createElement('span'); rank.className = 'result-rank'; rank.textContent = String(i + 1).padStart(2, '0');
    const title = document.createElement('h3'); title.textContent = item.metadata?.source || item.id;
    const score = document.createElement('span'); score.className = 'score'; score.textContent = `${result.mode === 'hybrid' ? 'RRF' : '距离'} ${Number(item.score).toFixed(4)}`;
    heading.append(rank, title, score); const text = document.createElement('p'); text.textContent = item.text;
    const meta = document.createElement('small'); meta.textContent = `片段 ${(item.metadata?.chunk || 0) + 1} · ${item.id.slice(0, 12)}`;
    article.append(heading, text, meta); $('results').append(article);
  }
  if (!result.results.length) $('results').textContent = '没有找到相关内容，请确认当前知识库已有文档。';
  $('results-info').textContent = `${result.results.length} 条结果 · 查询向量化 ${Math.round(result.queryEmbedMs)} ms · seekdb ${Math.round(result.searchMs)} ms · 端到端 ${Math.round(result.e2eMs)} ms`;
}
$('search-form').onsubmit = e => { e.preventDefault(); run(async () => {
  const collection = $('collection').value, text = $('query').value.trim(); if (!collection || !text) throw new Error('请选择知识库并输入问题');
  await initializeModel(); const start = performance.now();
  const result = await engine.search(collection, text, Number($('topk').value), $('mode').value);
  showResults(result); await new Promise(requestAnimationFrame); result.e2eDisplayMs = performance.now() - start;
  measurements.e2eMs = result.e2eDisplayMs; log(`检索完成 · ${result.results.length} 条 · seekdb ${Math.round(result.searchMs)} ms · 展示 ${Math.round(result.e2eDisplayMs)} ms`, { timing: result });
}); };
$('example-query').onclick = () => { $('query').value = '怎样在浏览器中保护文档隐私？'; $('query').focus(); };
$('export').onclick = () => { const blob = new Blob([JSON.stringify({ userAgent: navigator.userAgent, hardwareConcurrency: navigator.hardwareConcurrency, logs }, null, 2)], { type: 'application/json' }); const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = 'localseek-log.json'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); };
// The same browser engine is used by the reproducible benchmark page automation.
window.localSeek = { Engine, api, engine };
run(async () => { const health = await api('/health'); $('db-dot').classList.add('connected'); $('db-status').textContent = 'seekdb 已连接'; log('数据库连接成功 · ' + health.database.version); if (!(await api('/collections')).length) await api('/collections', { name: 'kb_personal' }); await collections(); }).catch(e => notify(e.message, true));
