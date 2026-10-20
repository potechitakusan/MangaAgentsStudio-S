'use strict';
function rememberedPage(id) {
  const n = Number(storedValue('manga-page-' + id));
  return Number.isInteger(n) && n >= 0 && n < 24 ? n : 0;
}
// Centre lane: the shown page, hotspots, panel strip, pager, zoom and page generation.
function renderPages() {
  storeValue('manga-page-' + project.id, String(pageIndex));
  $('page-select').replaceChildren(...project.pages.map((p, i) => { const opt = el('option', `PAGE ${String(i + 1).padStart(2, '0')}`); opt.value = i; return opt; })); $('page-select').value = pageIndex;
  const record = manifest?.pages?.[pageIndex]; $('page-image').src = record ? asset(record.file) : ''; $('page-image').hidden = !record;
  $('page-hotspots').replaceChildren();
  for (const p of record?.panels || []) {
    if (!project.pages[pageIndex].panels.some(c=>c.id===p.id)) continue;
    const b = el('button', undefined, 'hotspot' + (p.id === selected ? ' selected' : '')); b.setAttribute('aria-label', `コマ ${p.id} を選択`);
    const [x,y,w,h] = p.rect; Object.assign(b.style, {left: `${x / manifest.canvas[0] * 100}%`, top: `${y / manifest.canvas[1] * 100}%`, width: `${w / manifest.canvas[0] * 100}%`, height: `${h / manifest.canvas[1] * 100}%`});
    b.onclick = () => selectPanel(p.id); $('page-hotspots').append(b);
  }
  $('panels').replaceChildren();
  for (const p of project.pages[pageIndex].panels) { const b = el('button', undefined, 'panel-thumb' + (p.id === selected ? ' selected' : '')); if (p.image) { const im = el('img'); im.src = asset(p.image); im.alt = p.scene; b.append(im); } b.append(el('span', `${p.id} ${p.image ? '●' : '○'}`)); b.onclick = () => selectPanel(p.id); $('panels').append(b); }
  $('manifest-link').href = asset('output/manifest.json');
  $('overview-link').href = asset(manifest?.overview || 'output/overview.png');
  $('page-hint').textContent = isWhole() ? 'ページ一括画像 · 実際のコマ境界は画像生成により変わります' : 'ページのコマをクリックして編集 · 右から左へ読む';
  const page = project.pages[pageIndex];
  const stale = isWhole() ? genState['page-' + (pageIndex + 1)] : page.panels.some(c => genState[c.id]);
  if (stale) $('page-hint').textContent += ' · 未生成または設定変更あり（再生成対象）';
  renderLayoutControls();
  updatePager();
}

function selectPanel(id) { showTab('page'); action(async () => { await save(); selected = id; renderPages(); renderEditor(); }); }
$('page-select').onchange=()=>{const next=Number($('page-select').value);action(async()=>{await save();pageIndex=next;selected=project.pages[next].panels[0].id;renderPages();renderEditor();});};
function updatePager() {
  const total = project?.pages.length || 0;
  $('page-position').textContent = total ? `${pageIndex + 1} / ${total}` : '—';
  $('page-prev').disabled = busy || pageIndex <= 0;
  $('page-next').disabled = busy || pageIndex >= total - 1;
  if (typeof updateZoomHint === 'function') updateZoomHint();
}
for (const [id, step] of [['page-prev', -1], ['page-next', 1]]) {
  $(id).onclick = () => {
    const next = pageIndex + step;
    if (busy || !project || next < 0 || next >= project.pages.length) return;
    $('page-select').value = next; $('page-select').dispatchEvent(new Event('change'));
  };
}
// ---- page preview zoom: x1.0 / x1.5 / x2.0 of the base size (x2.0 = four times the area), one shared choice ----
const ZOOMS = ['1', '1.5', '2'];
function setZoom(value) {
  if (!ZOOMS.includes(value)) value = '1';
  document.documentElement.style.setProperty('--page-zoom', value);
  document.documentElement.classList.toggle('page-zoomed', value !== '1');
  document.querySelectorAll('.zoom-button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.zoom === value)));
  storeValue('manga-page-zoom', value);
  updateZoomHint();
}
function updateZoomHint() {
  const area = document.querySelector('.page-scroll');
  $('zoom-hint').textContent = area && area.scrollWidth > area.clientWidth + 2 ? '横にスクロールできます（作品一覧を閉じると広く使えます）' : '';
}
window.addEventListener('resize', updateZoomHint);
document.querySelectorAll('.zoom-button').forEach(b => { b.onclick = () => setZoom(b.dataset.zoom); });
setZoom(storedValue('manga-page-zoom') || '1');

// ---- generate the shown page: confirm first, then save everything and generate ----
function producePlan() {
  const number = pageIndex + 1, page = project.pages[pageIndex], regenerate = $('regenerate-existing').checked;
  const stale = isWhole() ? [genState['page-' + number]].filter(Boolean).length : page.panels.filter(c => genState[c.id]).length;
  const targets = isWhole() ? 1 : (regenerate ? page.panels.length : stale);
  const points = [dirty ? '未保存の編集があります。生成の前に、画面の入力をすべて保存します。' : '画面の入力をすべて保存してから生成します。'];
  if (isWhole()) points.push('ページ一括生成: 選択中の画像モデルへ、このページ1枚分の画像（コマ割りを含む）を1回要求します。');
  else points.push(targets ? `コマ別生成: このページの ${targets} コマを1枚ずつ生成し、ページに組版します。` : '生成が必要なコマはありません（生成済みで変更なし）。組版だけを行います。');
  if (!isWhole() && !regenerate) points.push('生成済みで設定が変わっていないコマは対象外です。作り直す場合は、作品専用タブの「採用済みで変更のない画像も生成し直す」を入れてください。');
  points.push('生成の前の点検は、このページと共通設定だけが対象です。他のページの警告では止まりません。');
  if ($('ignore-warnings-auto').checked) points.push('「点検の警告があっても無視して生成する」が入っています。点検で警告が出ても確認せずに生成します（データ構造・費用・結果不明の停止は無視されません）。');
  points.push(imageLabel() === 'NovelAI' ? 'NovelAIへ1枚ずつ送信します。Opus対象設定・費用上限を使い、結果不明時は自動再送しません。' : 'QwenImage 2.1へ1枚ずつ送信します。ローカル生成でAnlas消費はありません。結果不明時は自動再送しません。');
  return {number, targets, points};
}
$('produce-page').onclick = () => {
  if (!project?.pages.length) { status('先に字コンテを作ってください', true); return; }
  const plan = producePlan();
  $('produce-title').textContent = `PAGE ${plan.number} を保存して生成`;
  $('produce-summary').textContent = isWhole() ? `PAGE ${plan.number} をページ一括で生成します。` : `PAGE ${plan.number} の生成・組版を実行します（生成するコマ: ${plan.targets}）。`;
  $('produce-points').replaceChildren(...plan.points.map(text => el('li', text)));
  $('produce-dialog').showModal();
};
$('produce-cancel').onclick = () => $('produce-dialog').close();
$('produce-form').onsubmit = e => { e.preventDefault(); $('produce-dialog').close(); action(() => produce('page')); };
