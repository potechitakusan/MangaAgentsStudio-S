'use strict';
// The project list and the new-project dialog.
async function library() {
  const items = await api('/api/projects'); $('projects').replaceChildren();
  for (const p of items) { const b = el('button', p.title, 'project-card' + (project?.id === p.id ? ' active' : '')); b.append(el('small', `${p.pages} ページ · ${p.id}${p.compatible?'':' · 旧形式（未対応）'}`)); b.disabled=!p.compatible;if(!p.compatible)b.dataset.unavailable=''; b.onclick = () => { if (confirmDiscard('別の作品を開くと、未保存の変更は破棄されます。')) action(() => load(p.id)); }; $('projects').append(b); }
  return items;
}
for (const id of ['new-project','empty-new']) $(id).onclick = () => $('create-dialog').showModal();
$('close-dialog').onclick = () => $('create-dialog').close();
$('create-form').onsubmit=e=>{e.preventDefault();action(async()=>{const p=await api('/api/create',{id:$('new-id').value,title:$('new-title').value,source:$('new-source').value,output_method:$('new-image-provider').value,render_mode:document.querySelector('input[name="new-render-mode"]:checked').value});$('create-dialog').close();pageIndex=0;await load(p.id,false);status('原作を保存しました。生成方式は「作品専用」タブに表示されています');});};
