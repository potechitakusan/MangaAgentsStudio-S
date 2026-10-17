'use strict';
// The page plan (studio/pageplan.py): which part of the source each coming page shows. Made by the production AI in one call,
// checked and edited here, then followed by the storyboard runs.
const remainingPlan = () => (project?.page_plan?.pages || []).filter(r => r.page > (project?.pages.length || 0));

// A starting guess for "this chapter to the end in N pages": four pages a chapter, within the 24-page limit.
function suggestPlanPages() {
  if (!project) return;
  const left = 24 - project.pages.length, chapters = Math.max(1, sections.length - Number($('source-section').value || 0));
  $('plan-pages').max = Math.max(1, left);
  $('plan-pages').value = Math.max(1, Math.min(left, chapters * 4));
}

function renderPlan() {
  const box = $('plan-view'); box.replaceChildren();
  const rows = remainingPlan();
  suggestPlanPages();
  if (rows.length) $('plan-pages').value = rows.length;
  const first = (project?.pages.length || 0) + 1, chunk = rows.length ? rows.filter(r => r.page < first + 2 && r.section === rows[0].section).length : 0;
  $('board-note').textContent = rows.length
    ? `ページ割りがあるので、PAGE ${rows[0].page}から割り当てどおりに作ります（章の選択は使いません）。まず1〜2ページで試し、良ければ「最後まで自動で作る」を使えます。`
    : 'ページ割りがない場合は、選んだ章を指定のページ数に分けて作ります（1回1〜4ページ）。';
  if (rows.length) $('page-count').value = Math.max(1, Math.min(Number($('page-count').max) || 4, chunk || 1));
  if (!rows.length) return;
  const head = el('div', `ページ割り: PAGE ${rows[0].page}〜${rows.at(-1).page}（${rows.length}ページ・未作成）`, 'plan-head');
  box.append(head);
  rows.forEach(row => {
    const card = el('details', undefined, 'plan-page'); card.dataset.planPage = row.page;
    if (row.page < first + 2) card.open = true;
    const title = sections[row.section]?.title || `章 ${row.section + 1}`;
    card.append(el('summary', `PAGE ${row.page} · ${title} · ${row.panels}コマ前後 — ${row.summary}`));
    const summary = textField(card, 'このページの出来事', row.summary, null, 2); summary.dataset.planField = 'summary';
    const lines = textField(card, '入れる台詞（1行に1つ。原作の文言）', row.lines.join('\n'), null, Math.max(2, row.lines.length)); lines.dataset.planField = 'lines';
    const panels = el('label', 'コマ数の目安'), input = el('input'); Object.assign(input, {type: 'number', min: 1, max: 7, value: row.panels}); input.dataset.planField = 'panels'; input.oninput = markDirty; panels.append(input); card.append(panels);
    box.append(card);
  });
}

// Edited rows go back into the inputs that save() sends; rows of pages already drawn are kept as they were.
function collectPlan(value) {
  if (!value.page_plan?.pages) return;
  const done = project.pages.length;
  value.page_plan.pages = value.page_plan.pages.map(row => {
    const card = document.querySelector(`[data-plan-page="${row.page}"]`);
    if (row.page <= done || !card) return row;
    const field = name => card.querySelector(`[data-plan-field="${name}"]`).value;
    return {...row, summary: field('summary').trim() || row.summary, lines: field('lines').split('\n').map(x => x.trim()).filter(Boolean), panels: Math.max(1, Math.min(7, Number(field('panels')) || row.panels))};
  });
}
