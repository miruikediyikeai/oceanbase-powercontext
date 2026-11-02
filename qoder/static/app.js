let workspace = null;
let selectedId = null;
let revealed = false;
let busy = false;
let toastTimer;
let segments = [];
let historyCardId = null;
const $ = id => document.getElementById(id);
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const date = value => new Date(value).toLocaleString('zh-CN', {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});

async function api(path, method = 'GET', body) {
  const response = await fetch(path, {method, headers: {'Content-Type':'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '输入格式不正确，请核对卡片或设置。');
  return data;
}

function toast(message) {
  $('toast').textContent = message; $('toast').hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 4000);
}

const VIEW_NAMES = {today:'今日复习',library:'知识卡片',notes:'笔记转卡片',history:'复习回顾',memory:'学习记忆',settings:'复习偏好'};

function showView(view) {
  document.querySelectorAll('.view').forEach(element => element.hidden = element.id !== `view-${view}`);
  document.querySelectorAll('.nav').forEach(element => element.classList.toggle('active', element.dataset.view === view));
  $('breadcrumb').textContent = VIEW_NAMES[view];
  if (view === 'history' && workspace) loadWeekly();
}

async function refresh() {
  try {
    workspace = await api('/api/workspace');
    $('notice').hidden = true;
    $('connection').textContent = '学习记忆已连接'; $('connection-dot').classList.remove('offline');
    render();
    return true;
  } catch (error) {
    $('notice').textContent = error.message; $('notice').hidden = false;
    $('connection').textContent = '学习记忆暂不可用'; $('connection-dot').classList.add('offline');
    $('plan-count').textContent = '读取失败';
    $('study-area').innerHTML = '<div class="empty"><span class="empty-symbol">◇</span><h2>暂时连不上学习记忆</h2><p>请按运行说明启动 PowerContext，再点击“刷新进度”。</p></div>';
    return false;
  }
}

function render() {
  const {stats, preferences, plan} = workspace;
  for (const key of ['due','weak','reviews','total']) $(`stat-${key}`).textContent = stats[key];
  $('nav-due').textContent = stats.due;
  $('greeting').textContent = `${preferences.name}，从上一次的进度继续。每轮最多 ${preferences.daily_limit} 张，按自己的节奏来。`;
  $('plan-count').textContent = `本轮 ${plan.length} 张`;
  $('learner-name').value = preferences.name; $('daily-limit').value = preferences.daily_limit;
  $('scope-id').textContent = `Scope: ${workspace.scope_id}`;
  $('llm-status').textContent = workspace.llm_configured ? '已配置模型，可自动生成候选' : '未配置模型，使用手工填写候选';
  $('llm-dot').classList.toggle('offline', !workspace.llm_configured);
  if (!plan.some(record => record.card.id === selectedId)) { selectedId = plan[0]?.card.id; revealed = false; }
  renderStudy(); renderLibrary(); renderMemory(); renderHistoryPicker();
  $('plan-list').innerHTML = plan.length ? plan.map((record, index) => `<div class="plan-row"><span class="row-number">${String(index+1).padStart(2,'0')}</span><div class="row-main"><b>${escapeHTML(record.card.question)}</b><small>${escapeHTML(record.reason)}</small></div><span class="tag">${escapeHTML(record.card.topic)}</span><button class="button secondary" data-select="${escapeHTML(record.card.id)}">复习 →</button></div>`).join('') : '<div class="empty"><p>暂无到期卡片。导入新知识，或等下一次复习时间到来。</p></div>';
}

function renderStudy() {
  const record = workspace.plan.find(record => record.card.id === selectedId);
  if (!record) {
    const empty = workspace.stats.total === 0;
    $('study-area').innerHTML = `<div class="empty"><span class="empty-symbol">✳</span><h2>${empty ? '从第一张知识卡片开始' : '这一轮，已经完成。'}</h2><p>${empty ? '准备了 6 张示例卡片，体验一段会被记住的学习。' : '进度已保存。下次回来，会从你的学习记忆继续。'}</p><button class="button primary" id="empty-action">${empty ? '导入示例，开始复习' : '查看我的知识卡片'}</button></div>`;
    return;
  }
  const card = record.card;
  $('study-area').innerHTML = `<div class="card-top"><span class="topic">${escapeHTML(card.topic)}</span><span>${escapeHTML(record.reason)}</span></div><h2>${escapeHTML(card.question)}</h2><div class="source">来源 · ${escapeHTML(card.source)}</div>${revealed ? `<div class="answer">${escapeHTML(card.answer)}</div>` : ''}<div class="study-actions">${revealed ? '<button class="rating again" data-rating="again">还没记住 · 10 分钟</button><button class="rating hard" data-rating="hard">有点模糊 · 明天</button><button class="rating good" data-rating="good">记住了 ✓</button>' : '<button class="button primary" id="reveal-answer">想好了，查看答案 ↓</button>'}<small>${revealed ? '依据自己的回忆情况自评' : '试着先在脑海里回答'}</small></div>`;
}

function renderLibrary() {
  const query = $('search').value.trim().toLowerCase();
  const records = workspace.cards.filter(({card}) => [card.question,card.topic,card.source,card.answer].some(text => text.toLowerCase().includes(query)));
  $('library-list').innerHTML = records.length ? records.map(({card}) => `<article class="library-card"><div class="card-top"><span class="topic">${escapeHTML(card.topic)}</span><span>已复习 ${card.repetitions} 次</span></div><h3>${escapeHTML(card.question)}</h3><details><summary>展开参考答案</summary><p>${escapeHTML(card.answer)}</p></details><div class="source">来源 · ${escapeHTML(card.source)}</div><p>下次复习：${date(card.due_at)}${card.last_rating ? ` · 最近自评：${{again:'还没记住',hard:'有点模糊',good:'记住了'}[card.last_rating]}` : ' · 尚未复习'}</p><div class="library-actions"><button class="button secondary" data-history-card="${escapeHTML(card.id)}">复习历史</button></div></article>`).join('') : '<div class="empty"><h2>还没有匹配的知识卡片</h2><p>导入示例、整理笔记或上传自己的 JSON 文件，从一个小问题开始。</p></div>';
}

function renderMemory() {
  $('memory-list').innerHTML = workspace.memory.length ? workspace.memory.map(entry => `<details class="memory-record"><summary>${escapeHTML(entry.kind)} <span class="tag">版本 ${entry.version}</span></summary><pre>${escapeHTML(JSON.stringify(entry,null,2))}</pre></details>`).join('') : '<div class="empty"><p>还没有学习记忆。导入卡片或保存偏好后，这里会出现真实记录。</p></div>';
}

function renderHistoryPicker() {
  $('history-card-picker').innerHTML = workspace.cards.length ? workspace.cards.map(({card}) => `<article class="library-card compact"><span class="topic">${escapeHTML(card.topic)}</span><h3>${escapeHTML(card.question)}</h3><button class="button secondary" data-history-card="${escapeHTML(card.id)}">查看历史</button></article>`).join('') : '<div class="empty"><p>还没有卡片。导入后即可查看每次复习的历史。</p></div>';
}

async function loadWeekly() {
  const area = $('weekly-area');
  area.innerHTML = '<div class="empty"><p>正在读取 PowerContext 历史版本……</p></div>';
  try {
    const data = await api('/api/weekly');
    if (data.empty) {
      $('weekly-metrics').hidden = true;
      $('weekly-range').textContent = '最近 7 天（UTC）内没有复习记录';
      area.innerHTML = '<div class="empty"><span class="empty-symbol">◔</span><h2>这一周还没有复习</h2><p>开始第一轮复习后，这里会统计真实的复习次数与遗忘变化。</p></div>';
      return;
    }
    const summary = data.summary;
    $('weekly-metrics').hidden = false;
    $('weekly-reviews').textContent = summary.totals.reviews;
    $('weekly-new').textContent = summary.totals.new_cards;
    $('weekly-lapses').textContent = summary.totals.lapses;
    const start = new Date(summary.range.start).toLocaleDateString('zh-CN',{month:'short',day:'numeric'});
    const end = new Date(summary.range.end).toLocaleDateString('zh-CN',{month:'short',day:'numeric'});
    $('weekly-range').textContent = `${start} – ${end} · ${summary.range.timezone} · ${summary.rule}`;
    area.innerHTML = summary.daily.length ? summary.daily.map(day => `<div class="plan-row"><span class="row-number">${day.date.slice(5)}</span><div class="row-main"><b>${day.reviews} 次复习</b><small>${day.lapses ? `遗忘计数 +${day.lapses}` : '无遗忘'}</small></div></div>`).join('') : '<div class="empty"><p>统计范围内有数据，但没有按天拆分的复习记录。</p></div>';
  } catch (error) {
    $('weekly-metrics').hidden = true;
    area.innerHTML = `<div class="empty"><h2>读取历史失败</h2><p>${escapeHTML(error.message)}</p></div>`;
  }
}

async function loadCardHistory(cardId) {
  historyCardId = cardId;
  const area = $('history-timeline');
  area.innerHTML = '<div class="empty"><p>正在读取该卡片的版本历史……</p></div>';
  try {
    const data = await api(`/api/cards/${encodeURIComponent(cardId)}/history`);
    const timeline = data.timeline;
    area.innerHTML = timeline.length ? timeline.map((item, index) => {
      const event = item.event;
      const kindLabel = event?.kind === 'import' ? '导入' : '复习';
      const ratingLabel = event?.rating ? {again:'还没记住',hard:'有点模糊',good:'记住了'}[event.rating] : '';
      const detail = event?.kind === 'import'
        ? `首次导入 · 版本 ${item.card.schema_version}`
        : `自评：${ratingLabel} · ${event.lapses_delta > 0 ? `遗忘计数 +${event.lapses_delta}` : '遗忘计数不变'} · 下次：${date(item.card.due_at)}`;
      return `<div class="plan-row timeline-row"><span class="row-number">${String(index+1).padStart(2,'0')}</span><div class="row-main"><b>${kindLabel} · ${escapeHTML(item.card.topic)}</b><small>${event ? date(event.at) : '—'} · ${escapeHTML(detail)}</small><small class="muted">Memory revision ${item.revision} · ${escapeHTML(item.entry_version_id)}</small></div></div>`;
    }).join('') : '<div class="empty"><p>未找到历史记录。</p></div>';
  } catch (error) {
    area.innerHTML = `<div class="empty"><h2>读取历史失败</h2><p>${escapeHTML(error.message)}</p></div>`;
  }
}

async function mutate(operation) {
  if (busy) return;
  busy = true;
  document.querySelectorAll('button').forEach(button => button.disabled = true);
  try { await operation(); }
  catch (error) { $('notice').textContent = error.message; $('notice').hidden = false; }
  finally { busy = false; document.querySelectorAll('button').forEach(button => button.disabled = false); }
}

async function importCards(data) {
  const result = await api('/api/import', 'POST', data);
  const loaded = await refresh();
  if (loaded) { toast(`已保存 ${result.added} 张卡片，跳过 ${result.skipped} 张已存在的卡片`); showView('today'); }
}

async function splitNotes() {
  const topic = $('notes-topic').value.trim();
  const source = $('notes-source').value.trim();
  const note = $('notes-text').value;
  const result = await api('/api/notes/segments', 'POST', {note, topic, source});
  segments = result.segments.map(segment => ({
    ...segment,
    confirmed: false,
    question: '',
    answer: '',
    generating: false,
  }));
  renderSegments();
}

function renderSegments() {
  const area = $('segments-area');
  if (!segments.length) {
    area.innerHTML = '<div class="empty"><h2>没有可用的段落</h2><p>笔记太短，或分段后每段不足 20 个字符。请粘贴更完整的笔记。</p></div>';
    return;
  }
  area.innerHTML = `
    <div class="section-heading lower"><h2>候选卡片</h2><span>${segments.length} 段 · 勾选确认后才会导入</span></div>
    ${segments.map(segment => `
      <article class="segment-card" data-segment="${segment.index}">
        <div class="card-top"><span class="tag">段落 ${segment.index + 1}</span>${segment.heading ? `<span>${escapeHTML(segment.heading)}</span>` : ''}<label class="confirm-check"><input type="checkbox" data-confirm="${segment.index}" ${segment.confirmed ? 'checked' : ''}> 确认导入</label></div>
        <blockquote>${escapeHTML(segment.text)}</blockquote>
        <div class="segment-fields">
          <label>问题<input data-field="${segment.index}" data-key="question" placeholder="根据段落写下自测问题" value="${escapeHTML(segment.question)}"></label>
          <label>答案<textarea rows="2" data-field="${segment.index}" data-key="answer" placeholder="写下答案，或生成候选后修改">${escapeHTML(segment.answer)}</textarea></label>
        </div>
        <div class="segment-actions">
          ${workspace.llm_configured ? `<button class="button secondary" data-generate="${segment.index}">${segment.generating ? '生成中…' : '用模型生成候选'}</button>` : ''}
        </div>
      </article>`).join('')}
    <div class="notes-import-row"><button class="button primary" id="import-confirmed">导入已确认的卡片</button><small>未勾选的段落不会进入学习记忆。</small></div>`;
}

async function collectConfirmedCards() {
  const topic = $('notes-topic').value.trim();
  const source = $('notes-source').value.trim();
  const cards = [];
  for (const segment of segments) {
    const question = segment.question.trim();
    const answer = segment.answer.trim();
    if (!segment.confirmed || !question || !answer) continue;
    cards.push({id: await stableNoteId(topic, question, answer, source), topic, question, answer, source});
  }
  return cards;
}

// Client-side mirror of the server's content-derived identity.
async function stableNoteId(topic, question, answer, source) {
  const canonical = [topic, question, answer, source].join('\n');
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(canonical));
  return 'note-' + Array.from(new Uint8Array(digest)).slice(0, 8).map(byte => byte.toString(16).padStart(2, '0')).join('');
}

document.addEventListener('click', event => {
  const nav = event.target.closest('[data-view]');
  if (nav) showView(nav.dataset.view);
  const select = event.target.closest('[data-select]');
  if (select && !busy) { selectedId = select.dataset.select; revealed = false; renderStudy(); $('study-area').scrollIntoView({behavior:'smooth',block:'center'}); }
  if (event.target.closest('#reveal-answer')) { revealed = true; renderStudy(); }
  if (event.target.closest('#empty-action')) {
    if (workspace.stats.total === 0) mutate(async () => importCards(await api('/api/sample')));
    else showView('library');
  }
  const rating = event.target.closest('[data-rating]');
  if (rating) mutate(async () => {
    const record = workspace.plan.find(record => record.card.id === selectedId);
    const result = await api('/api/review','POST',{card_id:record.card.id,rating:rating.dataset.rating,entry_version_id:record.citation.entry_version_id});
    revealed = false; selectedId = null;
    if (await refresh()) toast(`复习记忆已保存 · 下次 ${date(result.card.due_at)}`);
  });
  const historyButton = event.target.closest('[data-history-card]');
  if (historyButton) {
    historyCardId = historyButton.dataset.historyCard;
    showView('history');
    mutate(() => loadCardHistory(historyCardId));
  }
  const generate = event.target.closest('[data-generate]');
  if (generate) mutate(async () => {
    const index = Number(generate.dataset.generate);
    const segment = segments[index];
    segment.generating = true;
    renderSegments();
    try {
      const data = await api('/api/notes/candidates','POST',{
        segments: [{index: segment.index, heading: segment.heading, text: segment.text}],
        topic: $('notes-topic').value.trim(),
        source: $('notes-source').value.trim(),
      });
      const candidates = data.candidates.filter(candidate => candidate.segment_index === segment.index);
      if (!candidates.length) throw new Error('模型没有返回可用候选，请手工填写问题与答案。');
      segment.question = candidates[0].question;
      segment.answer = candidates[0].answer;
      toast(`生成了 ${candidates.length} 张候选，取第 1 张，可编辑后确认`);
    } finally {
      segment.generating = false;
      renderSegments();
    }
  });
  if (event.target.closest('#import-confirmed')) mutate(async () => {
    const cards = await collectConfirmedCards();
    if (!cards.length) throw new Error('请先勾选“确认导入”并填写问题与答案。');
    const result = await api('/api/import','POST',{cards});
    const loaded = await refresh();
    if (loaded) {
      toast(`已保存 ${result.added} 张卡片，跳过 ${result.skipped} 张已存在的卡片`);
      segments = [];
      $('segments-area').innerHTML = '';
      $('notes-text').value = '';
      showView('today');
    }
  });
});

document.addEventListener('input', event => {
  const field = event.target.closest('[data-field]');
  if (field) {
    const index = Number(field.dataset.field);
    const key = field.dataset.key;
    const segment = segments.find(segment => segment.index === index);
    if (segment) segment[key] = field.value;
  }
  const confirm = event.target.closest('[data-confirm]');
  if (confirm) {
    const index = Number(confirm.dataset.confirm);
    const segment = segments.find(segment => segment.index === index);
    if (segment) segment.confirmed = confirm.checked;
  }
});

$('refresh').addEventListener('click', () => mutate(refresh));
$('load-sample').addEventListener('click', () => mutate(async () => importCards(await api('/api/sample'))));
$('file-import').addEventListener('change', event => {
  const file = event.target.files[0];
  if (file) mutate(async () => {
    if (file.size > 1024*1024) throw new Error('导入文件不能超过 1 MB。');
    let data;
    try { data = JSON.parse(await file.text()); } catch { throw new Error('文件不是有效的 JSON，请参考示例格式。'); }
    await importCards(data);
  });
  event.target.value = '';
});
$('search').addEventListener('input', () => { if (workspace) renderLibrary(); });
$('preferences-form').addEventListener('submit', event => {
  event.preventDefault();
  mutate(async () => {
    if (!workspace) throw new Error('请先连接 PowerContext。');
    await api('/api/preferences','PUT',{name:$('learner-name').value,daily_limit:Number($('daily-limit').value),entry_version_id:workspace.preferences_version});
    if (await refresh()) toast('学习偏好已保存，下一轮计划会继续使用');
  });
});
$('notes-form').addEventListener('submit', event => {
  event.preventDefault();
  mutate(splitNotes);
});
$('export-memory').addEventListener('click', () => {
  if (!workspace) { toast('请先连接 PowerContext'); return; }
  const blob = new Blob([JSON.stringify({exported_at:new Date().toISOString(),scope_id:workspace.scope_id,stats:workspace.stats,memory:workspace.memory},null,2)],{type:'application/json;charset=utf-8'});
  const url = URL.createObjectURL(blob); const link = document.createElement('a');
  link.href = url; link.download = 'recall-memory-evidence.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url),1000);
});
$('today-date').textContent = new Date().toLocaleDateString('zh-CN',{month:'long',day:'numeric',weekday:'long'}) + ' / A LITTLE, EVERY DAY';
refresh();
