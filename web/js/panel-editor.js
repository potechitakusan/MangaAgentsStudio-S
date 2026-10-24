'use strict';
// Right panel, panel part: the selected panel's fields, characters, speakers and its actions.
function renderPanelEditor() {
  $('panel-editor').hidden = false;
  const p = current(); $('panel-title').textContent = `コマ ${p.id}`; for (const k of ['scene','prompt','negative']) $(k).value = p[k] || '';
  $('dialogue').value = (p.dialogue || []).join('\n'); $('seed').value = p.seed ?? ''; $('crop-x').value = (p.crop || [.5,.5])[0]; $('crop-y').value = (p.crop || [.5,.5])[1];
  $('panel-image').hidden = !p.image; if (p.image) $('panel-image').src = asset(p.image);
  $('adjust-position').hidden = !p.image || isWhole();
  $('panel-image').classList.toggle('position-editable', !!p.image && !isWhole());
  $('panel-image').title = p.image && !isWhole() ? 'ダブルクリックで位置調整' : '';
  $('setting-fields').hidden = !(project.locations || []).length;
  $('location-id').replaceChildren(el('option', '（指定なし）'), ...(project.locations || []).map(l => { const opt = el('option', l.name); opt.value=l.id; return opt; })); $('location-id').firstChild.value=''; $('location-id').value=p.location_id || '';
  $('prop-ids').replaceChildren(...(project.props || []).map(l => { const box = el('label', undefined, 'check-item'); const input = el('input'); Object.assign(input, {type:'checkbox', value:l.id, checked:(p.prop_ids || []).includes(l.id)}); box.append(input, el('span', l.name)); return box; }));
  if (!(project.props || []).length) $('prop-ids').append(el('span', '小物が登録されていません（作品専用タブの台帳で追加）', 'hint'));
  $('characters').replaceChildren();
  $('novelai-prompt-options').hidden=project.output_method!=='novelai';
  const options=$('prompt-options-fields');options.replaceChildren();
  const mode=selectField(options,'送信プロンプト',[['catalog','台帳の外見・衣装・背景・画風を合成'],['literal','原文をそのまま使用']],p.prompt_mode||'catalog');mode.id='prompt-mode';
  const flags={use_coords:!!p.characters.length,use_order:true,negative_use_coords:!!p.characters.length,negative_use_order:false,...p.conditioning};
  for(const [key,label] of Object.entries({use_coords:'人物の配置ピンを使う',use_order:'人物の入力順を使う',negative_use_coords:'人物別除外の配置ピンを使う',negative_use_order:'人物別除外の入力順を使う'})){const l=el('label',undefined,'check-item'),c=el('input');c.type='checkbox';c.id='condition-'+key;c.checked=flags[key];l.append(c,el('span',label));options.append(l);}
  for (const c of p.characters || []) {
    const card = el('div', undefined, 'character-card'); card.dataset.id = c.id; const cast = project.characters.find(x => x.id === c.id);
    card.append(el('strong', cast?.name || c.id), el('p', cast?.appearance || ''));
    const caption = el('textarea'); caption.value = c.prompt; caption.rows = 2; caption.dataset.field = 'prompt'; caption.setAttribute('aria-label', `${cast?.name}の動作・表情`); const captionLabel = el('label', '動作・表情（英語・画像モデルへ送信）'); captionLabel.append(caption); card.append(captionLabel);
    if(project.output_method==='novelai'){const neg=textField(card,'この人物だけの除外（英語）',c.negative||'',null,2);neg.dataset.field='negative';const ja=textField(card,'この人物だけの除外（日本語・確認用）',c.negative_ja||'',null,2);ja.dataset.field='negative_ja';const actions=el('div',undefined,'translation-actions');actionButton(actions,'日本語 → 英語',()=>translateField(ja,neg,'en'));actionButton(actions,'英語 → 日本語',()=>translateField(neg,ja,'ja'));card.append(actions);}
    const row = el('div', undefined, 'field-row'); for (const key of ['x','y']) { const label = el('label', key === 'x' ? '位置 横 0〜1' : '位置 縦 0〜1'); const input = el('input'); Object.assign(input, {type:'number',min:0,max:1,step:'any',value:c[key] ?? .5}); input.dataset.field = key; label.append(input); row.append(label); } card.append(row); $('characters').append(card);
  }
  $('candidate-list').replaceChildren();
  for (const c of p.candidates || []) { const finished=c.operation==='finish_existing',label=finished?'LoRA仕上げ':`候補 ${c.seed}`; const b = el('button', c.id === p.adopted_request ? (finished?'採用中 · LoRA仕上げ':'採用中') : label, c.id === p.adopted_request ? 'active' : ''); b.title = `Seed ${c.seed} / 生成時 revision ${c.revision}`; b.onclick = () => action(async () => { if (dirty) throw new Error('候補を変更する前に編集内容を保存してください'); await api('/api/adopt', {project_id:project.id, revision:project.revision, panel_id:p.id, candidate_id:c.id}); await load(project.id); status('候補画像を採用し、組版に反映しました'); }); $('candidate-list').append(b); }
  $('sent-request').hidden = !p.image;
  if (p.image) $('sent-request').href = asset(p.image.replace(/\.png$/, (p.candidates||[]).find(c=>c.id===p.adopted_request)?.operation==='finish_existing'?'.finish-request.json':'.request.json'));
  $('request-preview').textContent = '';
  // Japanese check fields come first, then the English text that is sent.
  for(const key of ['prompt','negative']){const parent=$(`panel-${key}-ja`);parent.replaceChildren();const t=textField(parent,key==='prompt'?'画像プロンプト（日本語・確認用）':'除外する内容（日本語・確認用）',p[key+'_ja']||'',null,key==='prompt'?3:2);t.id=key+'-ja';const actions=el('div',undefined,'translation-actions');actionButton(actions,'日本語 → 英語',()=>translateField(t,$(key),'en'));actionButton(actions,'英語 → 日本語',()=>translateField($(key),t,'ja'));parent.append(actions);}
  [...$('characters').children].forEach((card,i)=>{const character=p.characters[i];const area=card.querySelector('[data-field="prompt"]');const t=textField(card,'動作・表情（日本語・確認用）',character.prompt_ja,null,2);t.dataset.field='prompt_ja';const actions=el('div',undefined,'translation-actions');actionButton(actions,'日本語 → 英語',()=>translateField(t,area,'en'));actionButton(actions,'英語 → 日本語',()=>translateField(area,t,'ja'));actionButton(actions,'このコマから外す',async()=>{card.remove();panelChanged=true;markDirty('コマ '+current().id+' から人物を外す');renderSpeakers();});card.append(actions);area.closest('label').before(t.parentElement,actions);});
  $('add-character-select').replaceChildren(...project.characters.filter(c=>!p.characters.some(x=>x.id===c.id)).map(c=>{const o=el('option',c.name);o.value=c.id;return o;}));
  $('speaker-fields').replaceChildren();renderSpeakers();$('generate-panel').textContent=isWhole()?'保存してこのページを再生成':'保存して再生成';
  panelChanged=false;
  // Per-character outfit and expression.
  [...$('characters').children].forEach((card,i)=>{const a=p.characters[i],c=project.characters.find(c=>c.id===a.id);for(const [field,label,options,value] of [['outfit_id','このコマの衣装',[['','既定の衣装'],...(c.outfits||[]).map(o=>[o.id,o.name])],a.outfit_id||''],['expression','このコマの表情',meta.expressions.map(e=>[e.id,e.name]),a.expression||'custom']]){const s=selectField(card,label,options,value);s.dataset.field=field;s.onchange=e=>{panelChanged=true;markDirty(e);};}});
  const modeFields=()=>{const literal=project.output_method==='novelai'&&mode.value==='literal';for(const card of $('characters').children){for(const key of ['outfit_id','expression'])card.querySelector(`[data-field="${key}"]`).disabled=literal;for(const key of ['prompt','prompt_ja']){const area=card.querySelector(`[data-field="${key}"]`);area.closest('label').firstChild.textContent=(literal?'人物の送信全文':'動作・表情')+(key==='prompt'?'（英語・画像モデルへ送信）':'（日本語・確認用）');}}};mode.onchange=modeFields;modeFields();
  wholeChanged=false;
  setDirty(false);
}

function renderEditor() { renderPanelEditor(); renderPageEditor(); }
function renderSpeakers(){const saved=current()?.speakers||[];const prior=[...$('speaker-fields').querySelectorAll('select')].map(x=>x.value);$('speaker-fields').replaceChildren();const chars=[...$('characters').children].map(c=>c.dataset.id);$('dialogue').value.split('\n').filter(x=>x.trim()).forEach((line,i)=>{const l=el('label',`台詞${i+1}の話者`),s=el('select');const none=el('option','ナレーション／未指定');none.value='';s.append(none);for(const id of chars){const c=project.characters.find(c=>c.id===id);const o=el('option',c.name);o.value=id;s.append(o);}s.value=prior[i]??saved[i]??'';s.onchange=markDirty;l.append(s);$('speaker-fields').append(l);});}
function patch() {
  const result = {scene:$('scene').value, prompt:$('prompt').value, negative:$('negative').value,
    ...((project.locations || []).length ? {location_id:$('location-id').value, prop_ids:[...$('prop-ids').querySelectorAll('input:checked')].map(o=>o.value)} : {}),
    dialogue:$('dialogue').value.split('\n').filter(x => x.trim()), seed:$('seed').value === '' ? null : Number($('seed').value),
    crop:[Number($('crop-x').value),Number($('crop-y').value)],
    characters:[...$('characters').children].map(c => ({id:c.dataset.id, prompt:c.querySelector('[data-field="prompt"]').value, x:Number(c.querySelector('[data-field="x"]').value), y:Number(c.querySelector('[data-field="y"]').value)}))};
  result.prompt_ja=$('prompt-ja')?.value||'';result.negative_ja=$('negative-ja')?.value||'';result.speakers=[...$('speaker-fields').querySelectorAll('select')].map(x=>x.value);result.characters.forEach((c,i)=>c.prompt_ja=$('characters').children[i].querySelector('[data-field="prompt_ja"]')?.value||'');result.needs_edit=false;
  result.characters.forEach((c,i)=>{const card=$('characters').children[i];for(const k of ['outfit_id','expression'])c[k]=card.querySelector(`[data-field="${k}"]`).value;});
  if(project.output_method==='novelai'){result.prompt_mode=$('prompt-mode').value;result.conditioning={};for(const key of ['use_coords','use_order','negative_use_coords','negative_use_order'])result.conditioning[key]=$('condition-'+key).checked;result.characters.forEach((c,i)=>{for(const key of ['negative','negative_ja'])c[key]=$('characters').children[i].querySelector(`[data-field="${key}"]`).value;});}
  return result;
}
$('panel-form').addEventListener('input', markDirty);
$('panel-form').addEventListener('input',()=>{panelChanged=true;});
$('dialogue').addEventListener('input', renderSpeakers);
$('panel-form').onsubmit = e => { e.preventDefault(); action(async () => { await save(); await startJob('compose'); }); };
$('generate-panel').onclick=()=>action(async()=>{await save();if(isWhole())await startJob('generate-page',{page_number:pageIndex+1});else await startJob('generate',{panel_id:selected});});
$('edit-agent').onclick = () => action(async () => { const instruction = $('agent-instruction').value.trim(); if (!instruction) throw new Error('修正内容を入力してください'); await save(); await startJob('agent', {panel_id:selected,instruction}); });
$('show-request').onclick = () => action(async () => { await save(); $('request-preview').textContent = JSON.stringify(await api('/api/request', {project_id:project.id,panel_id:selected}), null, 2); status('次回の要求プレビューを表示しました。Seed空欄の場合は生成時に再抽選します'); });
$('add-character').onclick=()=>action(async()=>{await save();const id=$('add-character-select').value;if(!id)throw new Error('追加できる人物がありません');const p=current();await api('/api/edit',{project_id:project.id,revision:project.revision,panel_id:selected,patch:{characters:[...p.characters,{id,prompt:'',prompt_ja:'',x:.5,y:.5}]}});await load(project.id);});
$('remove-this-panel').onclick=()=>action(async()=>{if(!confirm(`コマ ${selected} をこのページから削除します。削除前の内容と画像は保存履歴に残ります。`))return;await save();const v=structuredClone(inputs),p=v.pages[pageIndex];if(p.panels.length<=1)throw new Error('最低1コマ必要です');p.panels=p.panels.filter(c=>c.id!==selected);p.template=templateFor(p.panels.length).id;p.whole.linked=true;project=await api('/api/workspace',{project_id:project.id,revision:project.revision,inputs:v});await startJob('compose');});
