'use strict';
// Right panel, work tab: system prompts (general and this work's own), style, source, characters/locations/props, and saving.
const SYSTEM_LABELS={storyboard:'構成・字コンテ',translation:'翻訳',review:'生成前点検',constraints:'共通の制作条件'};
function generalSystemValues(){const value={};for(const k of Object.keys(SYSTEM_LABELS))value[k]=document.querySelector(`[data-global-system="${k}"]`)?.value||'';return value;}
function renderGeneralSystem(){const box=$('global-system-fields');box.replaceChildren();for(const [k,n] of Object.entries(SYSTEM_LABELS)){const l=el('label',n+'（日本語）'),t=el('textarea');t.rows=3;t.value=meta.system[k]||'';t.dataset.globalSystem=k;t.oninput=markGeneral;l.append(t);box.append(l);}}
function showLayerNote(){$('system-layer-note').textContent=$('system-layered').checked?'実行時は、汎用タブの内容、この欄の内容の順に連結して使います。この欄が空の項目は汎用の内容だけが使われます。':'この作品は従来どおり、下の指示だけを使います（汎用とは連結されません）。連結するにはチェックを入れ、汎用と重複する内容を下から削ってください。';}
function workSystemValues(){const value={};for(const k of ['storyboard','translation','review','constraints'])value[k]=document.querySelector(`[data-config-path="system.${k}"]`)?.value||'';return value;}
// Remembered screen inputs. Every work starts from these defaults, so text typed for one work never shows up (or gets saved) in another.
const UI_DEFAULTS={'rewrite-instruction':'','panel-multiplier':'1.2','agent-instruction':'','export-format':'png','export-text':'yes'};
const UI_SAVED=[...Object.keys(UI_DEFAULTS),'board-instruction','source-section','page-count','generate-from','generate-to'];
function renderSettings(){
  const v=inputs||{system:{storyboard:'',translation:'',review:'',constraints:''},system_layered:true,style:'anime coloring, clean lineart, cinematic lighting',style_ja:'アニメ塗り、明瞭な線、映画的な照明',characters:[],locations:[],props:[],source:'',title:''};
  $('system-fields').replaceChildren();for(const [k,n] of Object.entries(SYSTEM_LABELS))textField($('system-fields'),n+'（日本語・この作品だけ）',v.system[k],`system.${k}`,3);
  $('system-layered').checked=Boolean(v.system_layered);showLayerNote();
  $('edit-title').value=v.title||'';$('edit-source').value=v.source||'';$('style-fields').replaceChildren();bilingualFields($('style-fields'),'絵柄',v.style,v.style_ja,'style','style_ja');bilingualFields($('style-fields'),'共通の除外',v.style_negative,v.style_negative_ja,'style_negative','style_negative_ja');
  $('style-preset').replaceChildren(el('option','自由入力'));$('style-preset').firstChild.value='';for(const s of meta.styles){const o=el('option',s.name_ja);o.value=s.id;$('style-preset').append(o);}$('style-preset').value=v.style_preset||'';
  $('catalog-fields').replaceChildren();
  for(const [kind,label] of [['characters','人物'],['locations','背景'],['props','小物']]){
    const group=el('section',undefined,'catalog-group');group.append(el('h3',label));
    (v[kind]||[]).forEach((entry,i)=>{const card=el('details',undefined,'catalog-edit');card.append(el('summary',`${entry.name} · ${entry.id}`));
      textField(card,'名前',entry.name,`${kind}.${i}.name`,1).dataset.catalogName='';textField(card,'ID（英数字。コマや台詞からの参照に使います）',entry.id,`${kind}.${i}.id`,1);
      if(kind==='characters'){bilingualFields(card,'固定外見（身体・髪。衣装は下で指定）',entry.appearance,entry.appearance_ja,`${kind}.${i}.appearance`,`${kind}.${i}.appearance_ja`);bilingualFields(card,'口調',entry.voice,entry.voice_ja,`${kind}.${i}.voice`,`${kind}.${i}.voice_ja`,false);wardrobeEditor(card,entry,i);}else bilingualFields(card,'固定特徴',entry.description,entry.description_ja,`${kind}.${i}.description`,`${kind}.${i}.description_ja`);
      actionButton(card,`この${label}を削除`,async()=>{inputs=collectInputs();inputs[kind].splice(i,1);renderSettings();markDirty('削除: '+entry.name+'（'+label+'）');});group.append(card);
    });
    actionButton(group,label+'を追加',async()=>{if(!project)throw new Error('先に作品を作成してください');inputs=collectInputs();const id=kind.slice(0,3)+'-'+crypto.randomUUID().slice(0,8);inputs[kind].push(kind==='characters'?{id,name:'新しい人物',appearance:'',appearance_ja:'',voice:'',voice_ja:''}:{id,name:'新しい'+label,description:'',description_ja:''});renderSettings();markDirty(label+'を追加');const added=[...$('catalog-fields').querySelectorAll(`[data-catalog-kind="${kind}"] > details.catalog-edit`)].at(-1);if(added){added.open=true;added.querySelector('[data-catalog-name]')?.select();}});group.dataset.catalogKind=kind;$('catalog-fields').append(group);
  }
  $('export-controls').hidden=!project;$('production-controls').hidden=!project?.pages.length;
  const ui=v.ui_inputs||{};for(const [id,value] of Object.entries(UI_DEFAULTS))$(id).value=ui[id]??value;
  // The chapter and page count are chosen by load() (next chapter, pages left). The generation range is kept only while it fits this work.
  const pages=project?.pages.length||0,from=Number(ui['generate-from']),to=Number(ui['generate-to']);
  if(Number.isInteger(from)&&Number.isInteger(to)&&1<=from&&from<=to&&to<=pages){$('generate-from').value=from;$('generate-to').value=to;}
  // Empty by default: the host already states the job, and the page plan sets the panels per page.
  $('board-instruction').value=ui['board-instruction']??'';
  $('regenerate-existing').checked=Boolean(ui['regenerate-existing']);
  $('ignore-warnings-auto').checked=Boolean(ui['ignore-warnings-auto']);
  $('source-preview').textContent=sections[Number($('source-section').value)]?.text||'';
  renderTrial();
}
function collectInputs(){
  if(!inputs)throw new Error('作品を選んでください');const value=structuredClone(inputs);
  document.querySelectorAll('[data-config-path]').forEach(n=>setAtPath(value,n.dataset.configPath,n.value,true));
  value.style_preset=$('style-preset').value;value.system_layered=$('system-layered').checked;value.render_mode=project.render_mode||'panels';value.text_mode=$('whole-text-mode').value||value.text_mode;
  if(project.pages.length){
    const p=value.pages[pageIndex];const index=project.pages[pageIndex].panels.findIndex(c=>c.id===selected);
    if(index>=0&&panelChanged)p.panels[index]={...p.panels[index],...patch()};
    p.whole={...p.whole,format:$('whole-format').value||p.whole.format,seed:$('whole-seed').value===''?null:Number($('whole-seed').value),linked:panelChanged?true:p.whole.linked};
    if(wholeChanged){const draft=JSON.parse($('whole-draft').value);p.template=draft.template;p.panels=draft.panels.map((c,i)=>({...c,id:p.panels[i]?.id||'c-'+crypto.randomUUID().slice(0,12)}));p.whole.linked=true;}
  }
  collectPlan(value);
  value.ui_inputs={...value.ui_inputs};for(const id of UI_SAVED)value.ui_inputs[id]=$(id).value;
  value.ui_inputs['regenerate-existing']=$('regenerate-existing').checked;
  value.ui_inputs['ignore-warnings-auto']=$('ignore-warnings-auto').checked;
  for(const id of trialIds)value.ui_inputs[id]=$(id).value;document.querySelectorAll('[data-wardrobe-instruction]').forEach(n=>value.ui_inputs['wardrobe-'+n.dataset.wardrobeInstruction]=n.value);
  return value;
}
async function save(){if(!project||!dirty)return;const input=collectInputs();project=await api('/api/workspace',{project_id:project.id,revision:project.revision,inputs:input});const result=await api('/api/project/'+project.id);inputs=result.inputs;wholeSettings=result.whole_settings;wholeChanged=false;panelChanged=false;setDirty(false);}
$('workbench-common').addEventListener('input', markDirty);
$('save-common').onclick=()=>action(async()=>{if(!project)throw new Error('作品を選んでから保存してください（全作品共通の内容は汎用タブの「汎用を保存」）');await save();await load(project.id);status('作品の設定を保存しました');});
$('save-all').onclick=()=>action(async()=>{const count=dirtyItems.size+generalItems.size;if(generalItems.size){meta.system=await api('/api/default-system',{system:generalSystemValues()});clearGeneral();}if(project&&dirty){await save();await load(project.id);}status(`未保存の変更をすべて保存しました（${count}項目）`);});
$('save-global').onclick=()=>action(async()=>{meta.system=await api('/api/default-system',{system:generalSystemValues()});clearGeneral();status('汎用の共通システムプロンプトを保存しました。次の実行から全作品へ反映されます');});
$('system-layered').onchange=showLayerNote;
$('system-preview-button').onclick=()=>{const general=generalSystemValues(),own=workSystemValues(),layered=$('system-layered').checked;$('system-preview').textContent=Object.entries(SYSTEM_LABELS).map(([k,n])=>`■ ${n}\n`+(layered?[general[k],own[k]]:[own[k]]).map(t=>t.trim()).filter(Boolean).join('\n\n')).join('\n\n');};
$('style-preset').onchange=()=>action(async()=>{const style=meta.styles.find(s=>s.id===$('style-preset').value);if(!style){markDirty('絵柄プリセットの選択');return;}document.querySelector('[data-config-path="style"]').value=style.style_prompt;document.querySelector('[data-config-path="style_ja"]').value=style.style_prompt_ja||style.name_ja;document.querySelector('[data-config-path="style_negative"]').value=style.negative_style;document.querySelector('[data-config-path="style_negative_ja"]').value=style.negative_style_ja||'';markDirty('絵柄プリセットの適用');status('絵柄と日本語訳を入力欄へ反映しました。確認して保存してください');});
$('translate-missing').onclick=()=>action(async()=>{await save();const v=structuredClone(inputs),pairs=[];function add(o,en,ja){if(o[en]&&!o[ja])pairs.push({object:o,en,ja});}add(v,'style','style_ja');add(v,'style_negative','style_negative_ja');for(const c of v.characters){add(c,'appearance','appearance_ja');add(c,'voice','voice_ja');for(const o of c.outfits||[])add(o,'prompt','prompt_ja');}for(const c of [...v.locations,...v.props])add(c,'description','description_ja');for(const page of v.pages)for(const c of page.panels){add(c,'prompt','prompt_ja');add(c,'negative','negative_ja');for(const actor of c.characters)add(actor,'prompt','prompt_ja');}for(let i=0;i<pairs.length;i+=8){const batch=pairs.slice(i,i+8),out=await runJob('translate',{items:batch.map((x,n)=>({id:'field-'+n,text:x.object[x.en]})),target:'ja'},false);for(const item of out){const x=batch[Number(item.id.split('-')[1])];x.object[x.ja]=item.text;}}project=await api('/api/workspace',{project_id:project.id,revision:project.revision,inputs:v});await load(project.id);status(`${pairs.length}項目を日本語へ翻訳して保存しました`);});
