'use strict';
// Shared state and small helpers. The screen is plain scripts loaded in order (see index.html); they share these globals.
const $ = id => document.getElementById(id);
let token, project, manifest, selected, sections = [], wholeSettings = [], pageIndex = 0, dirty = false, busy = false;
let inputs = null, meta = null, wholeChanged = false, panelChanged = false, pendingProduce = null, runningJob = null, genState = {};
const isWhole = () => project?.render_mode === 'whole';
const el = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };
const asset = path => `/asset?project=${encodeURIComponent(project.id)}&path=${encodeURIComponent(path)}&v=${project.revision}`;
function status(text, error = false) { $('status').textContent = text; $('status').className = error ? 'error' : ''; }
// Which things are unsaved, in words the user can read (shown in the side panel and in the "discard?" questions).
const dirtyItems = new Set();
const generalItems = new Set();   // edits of the all-works system prompts (saved on their own, not with the work)
function describeField(node) {
  const label = node.closest('label');
  let name = node.getAttribute('aria-label') || '';
  if (label) name = [...label.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim() || label.textContent.trim() || name;
  const group = node.closest('fieldset')?.querySelector('legend');
  if (group) name = [...group.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join('').trim() + '：' + name;
  name = name || node.placeholder || node.id || '入力欄';
  let area = '';
  const card = node.closest('.character-card');
  if (node.closest('#panel-editor')) area = `コマ ${current()?.id ?? ''}` + (card ? ` · ${card.querySelector('strong')?.textContent ?? ''}` : '');
  else if (node.closest('#whole-editor')) area = `ページ ${pageIndex + 1}（ページ一括）`;
  else if (node.closest('#global-system-fields')) area = '汎用システムプロンプト（全作品）';
  else if (node.closest('#system-details')) area = '作品専用システムプロンプト';
  else if (node.closest('#style-details')) area = '絵柄';
  else if (node.closest('#source-details')) area = '作品名・原作';
  else if (node.closest('.revision-tools')) area = '改稿の依頼';
  else {
    const entries = [node.closest('.outfit-edit'), node.closest('details.catalog-edit')].filter(Boolean);
    area = entries.reverse().map(d => d.querySelector('summary')?.textContent.trim()).filter(Boolean).join(' › ');
  }
  return area ? `${area} › ${name}` : name;
}
const anyDirty = () => dirty || generalItems.size > 0;
function showDirtyItems() {
  const items = [...dirtyItems, ...generalItems], box = $('dirty-list');
  box.hidden = !anyDirty();
  box.replaceChildren(...(anyDirty() ? [el('strong', '未保存の項目'), ...items.slice(0, 8).map(text => el('div', text)),
    ...(items.length > 8 ? [el('div', `ほか ${items.length - 8} 件`)] : []), ...(items.length ? [] : [el('div', '（項目を特定できない変更）')])] : []));
  $('side-dirty').textContent = anyDirty() ? '未保存' : '保存済み';
  $('side-dirty').classList.toggle('unsaved', anyDirty());
  $('side-dirty').title = items.join(String.fromCharCode(10));
  updateSaveAll();
}
function updateSaveAll() { $('save-all').disabled = busy || !anyDirty(); }
// `dirty` is about the open work (what save() writes); the all-works prompts are tracked separately in generalItems.
function setDirty(value) {
  dirty = value;
  if (!value) dirtyItems.clear();
  $('dirty').textContent = $('whole-dirty').textContent = value ? '未保存' : '保存済み';
  showDirtyItems();
}
function markGeneral(event) {
  generalItems.add(describeField(event.target));
  showDirtyItems();
}
function clearGeneral() { generalItems.clear(); showDirtyItems(); }
// markDirty(event) records the edited field; markDirty('reason') records a described action; markDirty() only flags it.
function markDirty(source) {
  const text = typeof source === 'string' ? source : (source?.target instanceof Element ? describeField(source.target) : '');
  if (text) dirtyItems.add(text);
  setDirty(true);
}
function unsavedSummary() { return dirtyItems.size ? [...dirtyItems].map(x => '・' + x).join('\n') : '・（項目を特定できない変更）'; }
// Ask before throwing away unsaved edits, and say which edits they are.
function confirmDiscard(question) { return !dirty || confirm(`${question}\n\n未保存の項目:\n${unsavedSummary()}`); }
// Everything is locked while a job runs, except the stop button and the navigation controls (tabs, library, zoom).
function setBusy(value) {
  busy = value;
  document.querySelectorAll('button,input,select,textarea').forEach(b => { if (b.id !== 'cancel-production' && !b.hasAttribute('data-cancel')) b.disabled = value || b.hasAttribute('data-unavailable'); });
  document.querySelectorAll('[data-nav]').forEach(b => { b.disabled = false; });
  updatePager(); updateSaveAll();
  if (typeof syncLibraryControls === 'function') syncLibraryControls();
}
async function api(path, data) {
  if (path === '/api/job' && data) data = {...data, llm_provider: llmProvider()};
  const r = await fetch(path, data === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Studio-Token': token}, body: JSON.stringify(data)});
  const result = await r.json(); if (!r.ok) throw new Error(result.error || '処理に失敗しました'); return result;
}
// While fn runs the status says 処理中…; if fn never says anything (e.g. choosing a panel), the earlier message comes back (an earlier error is not repeated).
async function action(fn) { if (busy) return; const before = $('status').className === 'error' ? '準備完了' : $('status').textContent; setBusy(true); status('処理中…'); try { await fn(); if ($('status').textContent === '処理中…') status(before); } catch(e) { status(e.message, true); } finally { setBusy(false); } }
// Stored times are ISO UTC; show them in the viewer's local time.
const localTime = iso => { const d = new Date(iso); return Number.isNaN(d.getTime()) ? String(iso || '') : d.toLocaleString('ja-JP', {year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'}); };
async function budget() { const b = await api('/api/ledger'); $('budget').textContent = `検証 ${b.spent} / ${b.limit} Anlas${b.unknown ? ' · 要確認' : ''}`; if (b.halted) status(b.halted, true); return b; }
const metaReady = api('/api/session').then(m => { meta = m; token = m.token; return m; });
function current() { return project.pages.flatMap(p => p.panels).find(p => p.id === selected); }
const storedValue = key => { try { return localStorage.getItem(key); } catch (_) { return null; } };
const storeValue = (key, value) => { try { localStorage.setItem(key, value); } catch (_) {} };
