'use strict';
let libraryExcludeId = null;
function syncLibraryControls() {
  $('library-exclude-go').disabled = busy || !libraryExcludeId || !$('library-confirm').checked;
}
function cancelLibraryExclude() {
  libraryExcludeId = null; $('library-confirm').checked = false; $('library-exclude').hidden = true; syncLibraryControls();
}
async function refreshLibraryManager() {
  const items = await api('/api/library');
  $('library-items').replaceChildren();
  for (const [hidden, title] of [[false, '一覧に表示中'], [true, '除外中（再読込みできます）']]) {
    const section = el('section'); section.append(el('h3', title));
    const entries = items.filter(p => p.excluded === hidden);
    if (!entries.length) section.append(el('p', '作品はありません', 'hint'));
    for (const p of entries) {
      const row = el('div', undefined, 'library-manager-row'); row.dataset.projectId = p.id;
      const info = el('div'); info.append(el('strong', p.title), el('small', `${p.id} · ${p.pages}ページ${p.compatible ? '' : ' · 旧形式（未対応）'}`)); row.append(info);
      const button = el('button', hidden ? '一覧に戻す' : '除外…'); button.type = 'button';
      button.onclick = hidden ? () => libraryAction(async () => {
        await api('/api/library-visibility', {project_id: p.id, excluded: false});
        cancelLibraryExclude(); await library(); await refreshLibraryManager();
        libraryMessage(`「${p.title}」を一覧に戻しました。左の一覧から開けます。`);
      }) : () => {
        libraryExcludeId = p.id; $('library-confirm').checked = false;
        $('library-exclude-title').textContent = `「${p.title}」を一覧から除外`;
        $('library-exclude').hidden = false; syncLibraryControls();
        $('library-exclude').scrollIntoView({block: 'nearest'});
      };
      row.append(button); section.append(row);
    }
    $('library-items').append(section);
  }
}
function libraryMessage(message, error = false) {
  $('library-message').textContent = message; $('library-message').className = error ? 'error-text' : 'hint';
}
function libraryAction(fn) {
  return action(async () => { libraryMessage('処理中…'); try { await fn(); } catch (e) { libraryMessage(e.message, true); throw e; } });
}
$('manage-library').onclick = () => libraryAction(async () => {
  cancelLibraryExclude(); await refreshLibraryManager(); libraryMessage(''); $('library-dialog').showModal();
});
$('library-close').onclick = () => $('library-dialog').close();
$('library-dialog').oncancel = e => { if (busy) e.preventDefault(); };
$('library-confirm').onchange = syncLibraryControls;
$('library-exclude-cancel').onclick = cancelLibraryExclude;
$('library-refresh').onclick = () => libraryAction(async () => {
  cancelLibraryExclude(); await library(); await refreshLibraryManager(); libraryMessage('一覧を読み直しました。');
});
$('library-choose').onclick = () => libraryAction(async () => {
  libraryMessage('このPCで開いたフォルダ選択画面を操作してください。');
  const result = await api('/api/library-choose', {});
  if (result.path) $('library-path').value = result.path;
  libraryMessage(result.path ? '「フォルダを読み込む」で作品を追加します。' : 'フォルダ選択を取り消しました。');
});
$('library-import-form').onsubmit = e => {
  e.preventDefault(); libraryAction(async () => {
    const result = await api('/api/library-import', {path: $('library-path').value});
    cancelLibraryExclude(); await library(); await refreshLibraryManager();
    libraryMessage(`「${result.title}」を${result.copied ? 'コピーして読み込みました' : '一覧に戻しました'}。左の一覧から開けます。${result.warning || ''}`, !!result.warning);
  });
};
function clearOpenProject() {
  project = null; inputs = null; manifest = null; selected = null; pageIndex = 0;
  wholeSettings = []; sections = []; genState = {}; referenceCatalog = [];
  $('title').textContent = '漫画をつくる'; $('subtitle').textContent = '一覧から作品を選ぶか、管理から読み込んでください。';
  $('empty').hidden = false;
  for (const id of ['studio', 'setup', 'batch-controls', 'mode-controls']) $(id).hidden = true;
  setDirty(false); renderSettings(); resetTrialOutput(); updatePager(); showStorage();
}
$('library-exclude-go').onclick = () => {
  if (!libraryExcludeId || !$('library-confirm').checked) return;
  const id = libraryExcludeId;
  if (project?.id === id && !confirmDiscard('表示中の作品を一覧から除外します。未保存の変更は破棄されます。')) return;
  libraryAction(async () => {
    await api('/api/library-visibility', {project_id: id, excluded: true, confirmed: $('library-confirm').checked});
    cancelLibraryExclude();
    const items = await library();
    if (project?.id === id) {
      const next = items.find(p => p.compatible);
      if (next) await load(next.id, false); else clearOpenProject();
    }
    await refreshLibraryManager(); libraryMessage('一覧から除外しました。作品フォルダは残っています。「一覧に戻す」で再読込みできます。');
    status('作品を一覧から除外しました（フォルダは保持）');
  });
};
