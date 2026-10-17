'use strict';
// Loading a work into every part of the screen, and start-up.
async function load(id, keep = true) {
  await metaReady;
  const old = keep ? selected : null; const result = await api('/api/project/' + encodeURIComponent(id)); project = result.project; manifest = result.manifest; sections = result.sections || []; wholeSettings = result.whole_settings || [];
  inputs = result.inputs; genState = result.generation_state || {};
  $('title').textContent = project.title; $('subtitle').textContent = `${project.pages.length} / 24 ページ · ${project.characters.length} 人物 · ${(project.locations || []).length} 背景 · Revision ${project.revision}`;
  // The setup card stays open while the user is working through a page plan; otherwise it shows only for a work with no pages.
  const setupOpen = keep && !$('setup').hidden && (project.page_plan?.pages || []).some(r => r.page > project.pages.length);
  $('empty').hidden = true; $('setup').hidden = !!project.pages.length && !setupOpen; $('studio').hidden = !project.pages.length;
  $('batch-controls').hidden = !project.pages.length;
  $('mode-controls').hidden = false; $('generation-mode').value = project.render_mode || 'panels';
  $('mode-label').textContent = isWhole() ? 'ページ一括生成（NovelAIが1ページを1枚で描く）' : 'コマ別生成（コマごとに画像を作り、台詞を後から組版）';
  $('mode-note').textContent = isWhole() ? 'ページを1枚で生成します。文字は後付け／NovelAI描画を選択できます。コマ別の画像も保存されます。' : 'コマを個別に生成し、台詞を後から組みます。';
  $('generate-all').closest('.range-controls').hidden = isWhole();
  $('new-pages').hidden = project.pages.length >= 24;
  $('batch-title').textContent = `${project.pages.length + 1}ページ目から字コンテを作成`;
  $('page-count').max = Math.min(4, 24 - project.pages.length); $('page-count').value = Math.min(2, 24 - project.pages.length);
  $('import-section').hidden = !!project.pages.length;
  const usedPages = index => (project.batches || []).filter(b => b.source_section === index).map(b => `PAGE ${b.start_page}〜${b.end_page}`).join('、');
  $('source-section').replaceChildren(...sections.map(s => { const used = usedPages(s.index); const opt = el('option', `${s.index+1}. ${s.title}` + (used ? `（使用済み: ${used}）` : '（未使用）')); opt.value = s.index; return opt; }));
  const previous = project.batches?.at(-1);
  $('source-progress').hidden = !previous;
  const allUsed = previous && sections.every(s => usedPages(s.index));
  $('source-progress').replaceChildren(...(previous ? [el('strong', `前回の終わり（PAGE ${previous.end_page}まで・${previous.source_title}）`), el('div', previous.handoff)] : []),
    ...(allUsed ? [el('div', '原作の章はすべて使用済みです。続きは、作品専用タブの原作に「## 章名」で新しい章を足すか、途中で終わった章を選び直して作ります。', 'hint')] : []));
  $('source-section').value = Math.min((project.batches?.at(-1)?.source_section ?? -1) + 1, sections.length - 1);
  $('source-preview').textContent = sections[Number($('source-section').value)]?.text || '';
  const lastBatch = project.batches?.at(-1); $('generate-from').value = lastBatch?.start_page || 1; $('generate-to').value = lastBatch?.end_page || Math.min(4, project.pages.length);
  $('generate-from').max = $('generate-to').max = project.pages.length;
  $('bible-view').replaceChildren();
  for (const [label, entries] of [['人物', project.characters], ['背景', project.locations || []], ['小物', project.props || []]]) for (const entry of entries) { const card = el('div', undefined, 'character-card'); card.append(el('strong', `${label} · ${entry.name}`), el('p', entry.appearance || entry.description)); $('bible-view').append(card); }
  $('batch-history').replaceChildren(...(project.batches || []).map(b => el('p', `${b.start_page}〜${b.end_page}P · ${b.source_title} — ${b.handoff}`)));
  renderSettings(); renderPlan(); wholeChanged = false; renderReview(result.review);
  pageIndex = Math.min(pageIndex, Math.max(0, project.pages.length - 1));
  if (project.pages.length) { selected = project.pages.flatMap(p => p.panels).some(p => p.id === old) ? old : project.pages[pageIndex].panels[0].id; pageIndex = project.pages.findIndex(p => p.panels.some(c => c.id === selected)); renderPages(); renderEditor(); }
  setDirty(false); await library(); await budget(); status('作品を読み込みました');
  resetTrialOutput(); await loadTrialHistory(); updatePager(); showStorage();
}
$('refresh').onclick = () => { if (confirmDiscard('読み直すと、未保存の変更は破棄されます。')) action(async () => { if (project) await load(project.id); else await library(); status('最新の保存内容を読み込みました'); }); };
$('account').onclick = () => action(async () => { status('NovelAIの接続を確認しています'); const s = await api('/api/status', {}); status(`${s.active && s.tier === 3 ? 'Opus接続済み' : 'Opus未確認'} · 残量 ${s.balance} Anlas · V5利用枠 ${s.usage.percent ?? '不明'}%`); });
window.addEventListener('beforeunload', e => { if (anyDirty()) { e.preventDefault(); e.returnValue = ''; } });
// Ctrl+S saves everything that is unsaved.
document.addEventListener('keydown', e => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') { e.preventDefault(); if (!$('save-all').disabled) $('save-all').click(); } });
(async () => {
  try {
    await metaReady; setupProviders(meta.llm_providers); renderGeneralSystem(); renderSettings(); showStorage(); updatePager();
    const items = await library();
    if (items.length) await load(items[0].id); else await budget();
    const jobs = await api('/api/jobs');
    if (jobs.length) { await load(jobs[0].project_id); await action(() => waitJob(jobs[0])); }
  } catch (e) { status(e.message, true); }
})();
