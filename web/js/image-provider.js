'use strict';
let referenceCatalog=[];
const imageLabel = () => project?.output_method === 'qwen' ? 'QwenImage 2.1' : 'NovelAI';
function renderImageSettings() {
  $('work-image-settings').hidden=!project;
  if(!project)return;
  const s=inputs.image_settings,qwen=project.output_method==='qwen';
  $('image-provider-label').textContent=imageLabel();
  $('qwen-options').hidden=!qwen;
  $('reference-mode').value=s.reference_mode;
  $('anime-finish').checked=s.anime_finish;$('anime-denoise').value=s.denoise;
  $('image-provider-note').textContent=qwen?'ローカルComfyUIで生成します。VRAMを空けるためStrataを停止し、制作AIはGrokを選んでください。仕上げを有効にするとQwen原画と追加時間も保存します。':'NovelAIのOpus対象設定と費用保護を使います。';
  $('trial-generate').textContent=`保存して${imageLabel()}で1枚試し描き`;
  $('reference-catalog').replaceChildren();
  for(const r of referenceCatalog){
    const box=el('div',undefined,'reference-item');
    box.append(el('strong',`${r.name} ${r.outfit_name||'背景'} · ${r.status==='ready'?'登録済み':r.status==='stale'?'要更新':'未登録'}`));
    if(r.image){const im=el('img');im.src=asset(r.image);im.alt=r.name;im.loading='lazy';box.append(im);}
    if(r.message)box.append(el('p',r.message,'hint'));
    actionButton(box,'試し描きの対象にする',async()=>{trialKind.value=r.kind;trialTargets(r.id);trialOutfits(r.outfit_id);trialFraming.value='full';trialExpression.value='neutral';trialInvalidate();trialCard.scrollIntoView({behavior:'smooth'});});
    $('reference-catalog').append(box);
  }
}
for(const id of ['reference-mode','anime-finish','anime-denoise']) $(id).onchange=()=>{markDirty('作品の画像生成設定');$('image-provider-status').textContent='未保存です。保存後の生成へ適用します。';};
$('save-image-provider').onclick=()=>action(async()=>{await save();await load(project.id);$('image-provider-status').textContent='作品の画像設定を保存しました';});
$('check-image-provider').onclick=()=>action(async()=>{const r=await api('/api/image-status',{project_id:project.id});$('image-provider-status').textContent=r.provider==='qwen'?`ComfyUI接続成功 · ${r.model}`:'NovelAI接続成功';});
