'use strict';
// Work management: backups, moving to the trash, opening the folders.
function formatBytes(n) { return n > 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB'; }
async function showStorage() {
  const box = $('storage-info'); box.replaceChildren();
  if (!project) { box.textContent = '作品を選ぶと使えます。'; return; }
  try {
    const info = await api('/api/project-storage?project=' + encodeURIComponent(project.id));
    const last = info.backups[0];
    box.append(el('p', `作品フォルダ ${formatBytes(info.bytes)} · バックアップ ${info.backups.length}件（直近10件まで表示）`));
    box.append(el('p', last ? `最新: ${last.name}（${formatBytes(last.bytes)}）` : 'まだバックアップがありません'));
  } catch (e) { box.textContent = e.message; }
}
for (const [id, full] of [['backup-light', false], ['backup-full', true]]) {
  $(id).onclick = () => action(async () => {
    if (!project) throw new Error('作品を選んでください');
    await save();
    status('バックアップを作成しています…');
    const r = await api('/api/project-backup', {project_id: project.id, full});
    await showStorage();
    status(`バックアップを作成しました: ${r.name}（${formatBytes(r.bytes)}）`);
  });
}
for (const [id, kind] of [['open-backups', 'backups'], ['open-project-folder', 'project'], ['open-trash', 'trash']]) {
  $(id).onclick = () => action(async () => {
    if (kind !== 'trash' && !project) throw new Error('作品を選んでください');
    const r = await api('/api/open-folder', {kind, project_id: project?.id});
    status(`フォルダを開きました: ${r.path}`);
  });
}
$('delete-project').onclick = () => {
  if (!project) { status('作品を選んでください', true); return; }
  if (!confirmDiscard('この作品を削除すると、未保存の変更も失われます。')) return;
  $('delete-name').textContent = `${project.title}（${project.id}）`; $('delete-confirm').value = ''; $('delete-go').disabled = true;
  $('delete-dialog').showModal();
};
$('delete-confirm').oninput = () => { $('delete-go').disabled = $('delete-confirm').value !== project?.id; };
$('delete-cancel').onclick = () => $('delete-dialog').close();
$('delete-form').onsubmit = e => {
  e.preventDefault();
  action(async () => {
    const id = project.id;
    const r = await api('/api/project-delete', {project_id: id, confirm: $('delete-confirm').value});
    $('delete-dialog').close(); setDirty(false);
    alert(`「${r.title || id}」をゴミ箱フォルダへ移動しました。\n完全に消す場合や元に戻す場合は、画面の「ゴミ箱フォルダを開く」から操作してください。`);
    location.reload();
  });
};

