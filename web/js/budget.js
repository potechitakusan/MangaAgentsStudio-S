'use strict';
// The verification budget in the header: change its limit or put the counter back to 0 (both are recorded with the time).
// Results are shown inside the dialog too: the status bar sits behind the modal while it is open.
function budgetSay(text, error = false) { status(text, error); $('budget-message').textContent = text; $('budget-message').classList.toggle('error-text', error); }
function renderBudget(b) {
  const last = b.resets.at(-1);
  $('budget-now').textContent = `いまの合計 ${b.spent} Anlas ／ 上限 ${b.limit} Anlas` + (last ? `（最後に0に戻した日時: ${localTime(last.at)}）` : '（まだ0に戻したことはありません）');
  $('budget-limit').value = b.limit;
  const stops = [b.halted ? `停止中: ${b.halted}` : '', b.unknown ? `結果が未確認の生成が ${b.unknown}件あります。` : ''].filter(Boolean).join(' ');
  $('budget-halt').hidden = !stops; $('budget-halt').textContent = stops ? stops + '（0に戻しても解除されません。docs/guides/OPERATIONS.md の復旧手順を確認してください）' : '';
  const rows = [...b.resets.map(r => ({at: r.at, text: `0に戻した（戻す前の合計 ${r.spent_before} Anlas、上限 ${r.limit}）`})),
                ...b.limit_changes.map(c => ({at: c.at, text: `上限を ${c.previous} → ${c.limit} Anlas に変更`}))].sort((x, y) => y.at.localeCompare(x.at));
  $('budget-history').replaceChildren(...(rows.length ? rows.map(r => el('div', `${localTime(r.at)}　${r.text}`)) : [el('div', 'まだ変更はありません', 'hint')]));
}
$('budget').onclick = async () => { try { renderBudget(await budget()); $('budget-message').textContent = ''; $('budget-dialog').showModal(); } catch (e) { status(e.message, true); } };
$('budget-save').onclick = async () => {
  const limit = Number($('budget-limit').value);
  if (!Number.isInteger(limit) || limit < 1 || limit > 1000000) { budgetSay('上限は1〜1,000,000の整数で入れてください', true); return; }
  try { renderBudget(await api('/api/budget', {limit})); await budget(); budgetSay(`検証費用の上限を ${limit} Anlas にしました`); } catch (e) { budgetSay(e.message, true); }
};
$('budget-reset').onclick = async () => {
  const now = await budget();
  if (!confirm(`検証費用の合計（いま ${now.spent} Anlas）を0に戻します。日時と戻す前の合計は履歴に残ります。`)) return;
  try { renderBudget(await api('/api/budget', {reset: true})); await budget(); budgetSay('検証費用の合計を0に戻しました（日時を記録しました）'); } catch (e) { budgetSay(e.message, true); }
};
$('budget-close').onclick = () => $('budget-dialog').close();
