'use strict';
// Right panel, page part: panel frames of the shown page and the page-batch (whole page) design.
function renderPageEditor() {
  renderWhole(); $('whole-editor').hidden = !isWhole(); $('whole-text-mode').value = inputs?.text_mode || 'embedded';
}
function renderLayoutControls() {
  const page = project.pages[pageIndex];
  $('layout-select').replaceChildren(...meta.layouts.map(t => { const o = el('option', `${t.count}コマ · ${t.name}`); o.value = t.id; return o; }));
  $('layout-select').value = page.template; drawLayout();
}
function renderWhole() {
  const config = wholeSettings[pageIndex]; const saved = project.pages[pageIndex].whole || {};
  $('whole-title').textContent = `PAGE ${pageIndex+1} · ページ一括`;
  $('whole-format').value = config.format; $('whole-seed').value = config.seed ?? ''; $('whole-draft').value = JSON.stringify(config.draft,null,2);
  $('whole-preview').textContent = ''; $('whole-length').textContent = '';
  $('whole-candidates').replaceChildren();
  for (const candidate of saved.candidates || []) {
    const active = saved.adopted_request === candidate.id;
    const b = el('button', `${active ? '採用中' : '候補'} · ${candidate.format} · ${candidate.seed}`, active ? 'active' : '');
    b.onclick = () => action(async () => { await save(); await api('/api/page-adopt',{project_id:project.id,revision:project.revision,page_number:pageIndex+1,candidate_id:candidate.id}); await load(project.id); status('ページ候補を採用しました'); });
    $('whole-candidates').append(b);
  }
  $('whole-sent-request').hidden = !saved.image;
  if (saved.image) $('whole-sent-request').href = asset(saved.image.replace(/\.png$/,'.request.json'));
  setDirty(false);
}
async function saveWhole() { await save(); }
$('whole-form').addEventListener('input', markDirty);
$('whole-draft').addEventListener('input', () => { wholeChanged = true; });
$('whole-form').onsubmit = e => { e.preventDefault(); action(async () => { await saveWhole(); status('ページ設計を保存しました。画像への反映は再生成してください'); }); };
$('generate-whole').onclick = () => action(async () => { await saveWhole(); await startJob('generate-page',{page_number:pageIndex+1}); });
$('preview-whole').onclick = () => action(async () => { await saveWhole(); const r=await api('/api/page-request',{project_id:project.id,page_number:pageIndex+1}); $('whole-preview').textContent=JSON.stringify(r.request,null,2); $('whole-length').textContent=`本文 ${r.preview.prompt_characters}文字 · 台詞 ${r.preview.text_characters}/374文字。長い説明はモデル側で欠落する場合があります。`; status('送信内容を表示しました（画像生成なし）'); });
$('reset-whole').onclick=()=>action(async()=>{await save();$('whole-draft').value=JSON.stringify(await api('/api/page-plan',{project_id:project.id,page_number:pageIndex+1}),null,2);wholeChanged=true;markDirty('ページ一括の設計を元コマから作り直し');status('元コマから作り直しました。保存すると反映されます');});
$('generation-mode').onchange = () => { const mode=$('generation-mode').value; if(busy) { $('generation-mode').value=project.render_mode || 'panels'; return; } action(async () => { await save(); await api('/api/mode',{project_id:project.id,revision:project.revision,mode}); await load(project.id); }); };
$('whole-text-mode').onchange=markDirty;
function drawLayout(){const layout=meta.layouts.find(t=>t.id===$('layout-select').value);$('layout-preview').replaceChildren();if(!layout)return;const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox','0 0 100 150');svg.setAttribute('aria-label',layout.name);layout.polygons.forEach((points,i)=>{const poly=document.createElementNS(ns,'polygon');poly.setAttribute('points',points.map(([x,y])=>`${x*100},${y*150}`).join(' '));poly.setAttribute('fill','#edf3e9');poly.setAttribute('stroke','#276957');poly.setAttribute('stroke-width','0.7');svg.append(poly);const text=document.createElementNS(ns,'text');text.setAttribute('x',points.reduce((s,p)=>s+p[0],0)/points.length*100);text.setAttribute('y',points.reduce((s,p)=>s+p[1],0)/points.length*150);text.setAttribute('font-size','8');text.textContent=i+1;svg.append(text);});$('layout-preview').append(svg,el('p',layout.summary));}
$('layout-select').onchange=drawLayout;
async function applyLayout(id){await save();await api('/api/layout',{project_id:project.id,revision:project.revision,page_number:pageIndex+1,template:id});await load(project.id);status('コマ枠を変更しました。追加したコマを編集するか制作AIで改稿してください');}
$('apply-layout').onclick=()=>action(()=>applyLayout($('layout-select').value));
function templateFor(count){return meta.layouts.find(t=>t.count===count&&t.id.startsWith('P')&&!t.id.includes('lean')&&!t.id.includes('diag'))||meta.layouts.find(t=>t.count===count);}
for(const [id,delta] of [['add-panel',1],['remove-panel',-1]])$(id).onclick=()=>action(async()=>{const count=project.pages[pageIndex].panels.length+delta;const t=templateFor(count);if(!t)throw new Error('対応するコマ数は1〜7です');await applyLayout(t.id);});
$('scale-panels').onclick=()=>action(async()=>{const multiplier=Number($('panel-multiplier').value);if(!Number.isFinite(multiplier)||multiplier<.5||multiplier>2)throw new Error('倍率は0.5〜2です');if(!confirm(`作品の全ページのコマ数を ×${multiplier} に増減します。増えたコマは空欄で、減るコマは各ページの末尾から外します（変更前の内容と画像は保存履歴に残ります）。`))return;await save();const v=structuredClone(inputs),old=v.pages.reduce((n,p)=>n+p.panels.length,0),goal=Math.max(v.pages.length,Math.round(old*multiplier));if(goal>v.pages.length*7)throw new Error('1ページ7コマを超えます');let total=old;while(total!==goal){let advanced=false;for(const p of v.pages){const count=p.panels.length+(goal>total?1:-1);const t=templateFor(count);if(!t)continue;if(count>p.panels.length)p.panels.push({id:'c-'+crypto.randomUUID().slice(0,12),scene:'',prompt:'',prompt_ja:'',negative:'',characters:[],dialogue:[],speakers:[],location_id:v.locations[0]?.id||'',prop_ids:[],needs_edit:true});else p.panels.pop();p.template=t.id;p.whole.linked=true;total+=goal>total?1:-1;advanced=true;if(total===goal)break;}if(!advanced)throw new Error('このコマ数へ変更できません');}project=await api('/api/workspace',{project_id:project.id,revision:project.revision,inputs:v});await startJob('compose');status(`${old} → ${total}コマへ変更しました。制作AI改稿または直接編集で内容を整えてください`);});
