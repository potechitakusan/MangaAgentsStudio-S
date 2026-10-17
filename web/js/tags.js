'use strict';
// "辞書からタグを選ぶ": candidate NovelAI tags by concept (knowledge/novelai/tag-dictionary.json). The person picks; nothing is
// added automatically. Plain fields get ", tag" at the end; the page design JSON gets the tag at the cursor.
let tagDictionary = null, tagTarget = null, draftCursor = 0, lastEnglishField = null;
const tagTargets = () => {
  const list = [['prompt', '画像プロンプト（英語）', $('prompt')], ['negative', '除外する内容（英語）', $('negative')]];
  [...$('characters').children].forEach(card => list.push(['actor-' + card.dataset.id, `${card.querySelector('strong')?.textContent || card.dataset.id} の動作・表情（英語）`, card.querySelector('[data-field="prompt"]')]));
  if (isWhole()) list.push(['whole', 'ページ設計JSON（カーソルの位置）', $('whole-draft')]);
  return list.filter(([, , node]) => node);
};
document.addEventListener('focusin', e => { if (e.target.closest?.('#panel-form') && e.target.matches('textarea[lang="en"], [data-field="prompt"]')) lastEnglishField = e.target; });
for (const ev of ['keyup', 'click', 'blur']) $('whole-draft').addEventListener(ev, () => { draftCursor = $('whole-draft').selectionStart; });

async function openTagPicker(prefer) {
  if (!project?.pages.length) { status('先に字コンテを作ってください', true); return; }
  if (!tagDictionary) tagDictionary = await api('/api/tag-dictionary');
  const targets = tagTargets();
  $('tag-target').replaceChildren(...targets.map(([id, label]) => { const o = el('option', label); o.value = id; return o; }));
  const preferred = prefer || targets.find(([, , node]) => node === lastEnglishField)?.[0] || 'prompt';
  $('tag-target').value = targets.some(([id]) => id === preferred) ? preferred : targets[0][0];
  $('tag-group').replaceChildren(el('option', 'すべての分類'), ...Object.entries(tagDictionary.groups).map(([id, name]) => { const o = el('option', name); o.value = id; return o; }));
  $('tag-group').firstChild.value = '';
  $('tag-search').value = ''; $('tag-message').textContent = '';
  renderTagList(); $('tag-dialog').showModal(); $('tag-search').focus();
}
function renderTagList() {
  const word = $('tag-search').value.trim().toLowerCase(), group = $('tag-group').value;
  const hits = tagDictionary.concepts.filter(c => (!group || c.group === group) && (!word || c.ja.toLowerCase().includes(word) || (tagDictionary.groups[c.group] || '').includes(word) || [...c.tags, ...c.related].some(t => t.toLowerCase().includes(word))));
  $('tag-list').replaceChildren(...hits.slice(0, 80).map(c => {
    const row = el('div', undefined, 'tag-row');
    row.append(el('div', `${c.ja}`, 'tag-concept'), el('div', `${tagDictionary.groups[c.group] || c.group}${c.people && c.people !== '0' ? ' · ' + c.people + '人' : ''}`, 'hint'));
    const chips = el('div', undefined, 'tag-chips');
    c.tags.forEach(t => chips.append(tagChip(t, false)));
    if (c.related.length) { chips.append(el('span', '関連:', 'hint')); c.related.forEach(t => chips.append(tagChip(t, true))); }
    row.append(chips); return row;
  }), ...(hits.length > 80 ? [el('p', `ほか ${hits.length - 80}件。言葉で絞り込んでください。`, 'hint')] : []), ...(hits.length ? [] : [el('p', '見つかりません。別の言葉で探してください。', 'hint')]));
}
function tagChip(tag, related) { const b = el('button', tag, 'tag-chip' + (related ? ' related' : '')); b.type = 'button'; b.onclick = () => insertTag(tag); return b; }
function insertTag(tag) {
  const [, label, node] = tagTargets().find(([id]) => id === $('tag-target').value) || [];
  if (!node) return;
  if (node === $('whole-draft')) {
    const at = Math.min(draftCursor, node.value.length);
    // Keep the prompt readable: separate from the words around the cursor with ", ".
    const before = node.value[at - 1] || '', after = node.value[at] || '';
    const piece = (before && !/[\s,"]/.test(before) ? ', ' : '') + tag + (/[A-Za-z0-9]/.test(after) ? ', ' : '');
    node.value = node.value.slice(0, at) + piece + node.value.slice(at); draftCursor = at + piece.length;
    $('tag-message').textContent = `「${tag}」をページ設計JSONのカーソル位置に入れました。"prompt" の文字列の中に入っているか確かめてください。`;
  } else {
    const have = node.value.split(',').map(x => x.trim().toLowerCase());
    if (have.includes(tag.toLowerCase())) { $('tag-message').textContent = `「${tag}」はすでに入っています。`; return; }
    node.value = node.value.trim() ? node.value.replace(/[\s,]*$/, '') + ', ' + tag : tag;
    $('tag-message').textContent = `「${tag}」を ${label} に足しました。保存すると反映されます。`;
  }
  node.dispatchEvent(new Event('input', {bubbles: true}));
}
$('tag-search').oninput = renderTagList;
$('tag-group').onchange = renderTagList;
$('tag-close').onclick = () => $('tag-dialog').close();
$('tag-picker').onclick = () => openTagPicker();
$('tag-picker-whole').onclick = () => openTagPicker('whole');
