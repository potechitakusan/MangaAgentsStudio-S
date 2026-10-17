'use strict';
// Review, generation, rewriting, new pages from the source, and exports.
let reviewReport = null;
function renderReview(report){
  reviewReport = report;
  $('review-results').replaceChildren();
  const count=report?.warnings.length||0;
  $('review-banner').hidden=!count;$('review-banner').textContent=`点検の警告 ${count}件を見る`;
  $('fix-warnings').hidden=!(report&&report.warnings.some(w=>w.page&&project?.pages[w.page-1]));
  if(!report)return;
  $('review-results').append(el('p',`点検時 Revision ${report.revision} · 警告 ${count}件。入力を変更すると再点検します。`));
  for(const warning of report.warnings){
    const line=el('div',undefined,'review-warning'),place=[warning.page?`PAGE ${warning.page}`:'',warning.source||''].filter(Boolean).join(' · ');
    line.append(el('strong',(place?place+' — ':'')+warning.path),el('p',warning.message));
    if(warning.page&&project?.pages[warning.page-1]){const open=el('button','このページを開く');open.type='button';open.onclick=()=>action(async()=>{await save();pageIndex=warning.page-1;selected=project.pages[pageIndex].panels[0].id;showTab('page');renderPages();renderEditor();});line.append(open);}
    $('review-results').append(line);
  }
}
// Warnings live in the work tab's review card; bring the user there and say so.
function showReviewWarnings(count){
  showTab('work');$('production-controls').scrollIntoView({behavior:'smooth',block:'start'});
  status(`点検の警告が ${count}件あります。右の「作品専用」タブ「点検・一括生成」に表示しました。内容を確認し、問題なければ「警告を無視して生成を続ける」を押してください。`);
}
$('review-banner').onclick=()=>{showTab('work');$('production-controls').scrollIntoView({behavior:'smooth',block:'start'});};
// Let the production AI fix what the review found: rewrite each page that has warnings, using the warnings as the instruction, then review again.
$('fix-warnings').onclick=()=>action(async()=>{
  const byPage=new Map();
  for(const w of reviewReport?.warnings||[])if(w.page&&project.pages[w.page-1]){if(!byPage.has(w.page))byPage.set(w.page,[]);byPage.get(w.page).push(w);}
  if(!byPage.size)throw new Error('ページに結び付いた警告がありません');
  const total=[...byPage.values()].reduce((n,list)=>n+list.length,0),skipped=reviewReport.warnings.length-total;
  const pages=[...byPage.keys()].map(n=>`PAGE ${n}`).join('、');
  if(!confirm(`${pages} を${llmLabel()}で改稿し、警告 ${total}件の解消を指示します。改稿前の内容は履歴に残ります。改稿のあと、もう一度点検します。${skipped?`\nページに結び付かない警告 ${skipped}件は対象外です。`:''}`))return;
  await save();
  for(const [number,list] of byPage){
    status(`PAGE ${number} を改稿しています（警告 ${list.length}件）`);
    const instruction='次の点検の警告を解消するように、このページのコマを直してください。警告に関係しない内容は変えないでください。\n'+list.map(w=>`・${w.path}: ${w.message}`).join('\n');
    await runJob('rewrite',{page_number:number,instruction});
  }
  const report=await runJob('review');
  renderReview(report);
  status(report.warnings.length?`改稿しました。再点検の警告は ${report.warnings.length}件です。内容を確認してください。`:'改稿しました。再点検の警告はありません。');
});
async function produce(scope,ignore=false){await save();const resume=ignore&&pendingProduce?.project_id===project.id&&pendingProduce?.revision===project.revision;ignore=resume||$('ignore-warnings-auto').checked;const options=pendingProduce&&resume?pendingProduce.options:{scope,page_number:pageIndex+1,panel_id:selected,regenerate:$('regenerate-existing').checked};const result=await runJob('produce',{...options,ignore_warnings:ignore});if(result.stage==='warnings'){pendingProduce={options,project_id:project.id,revision:project.revision};renderReview(result.review);$('ignore-warnings').hidden=false;showReviewWarnings(result.review.warnings.length);}else{pendingProduce=null;$('ignore-warnings').hidden=true;}}
$('review-all').onclick=()=>action(async()=>{await save();renderReview(await runJob('review'));});
$('produce-all').onclick = () => action(() => produce('all')); $('ignore-warnings').onclick = () => action(() => produce('all', true));
$('generate-all').onclick = () => action(async () => { if (!project?.pages.length) throw new Error('先に字コンテを作ってください'); const start_page=Number($('generate-from').value), end_page=Number($('generate-to').value); await save(); await startJob('generate', {start_page,end_page}); });
$('compose').onclick = () => action(async () => { if (!project?.pages.length) throw new Error('先に字コンテを作ってください'); await save(); await startJob('compose'); });
for(const [id,all] of [['rewrite-page',false],['rewrite-all',true]])$(id).onclick=()=>action(async()=>{await save();await runJob('rewrite',{instruction:$('rewrite-instruction').value||'現在の共通設定と口調に合わせて改稿してください',...(!all?{page_number:pageIndex+1}:{})});});
actionButton(document.querySelector('.revision-tools .actions'),'口調だけ全台詞へ反映',async()=>{await save();await runJob('voices');}).id='voices-all';
$('new-pages').onclick = () => {
  if (!project) { $('create-dialog').showModal(); return; }
  $('setup').hidden = !$('setup').hidden;
  if ($('setup').hidden) return;
  $('setup').scrollIntoView({behavior: 'smooth'});
  $('source-section').value = Math.min((project.batches?.at(-1)?.source_section ?? -1) + 1, sections.length - 1);
  $('source-preview').textContent = sections[Number($('source-section').value)]?.text || '';
};
$('source-section').onchange = () => { $('source-preview').textContent = sections[Number($('source-section').value)]?.text || ''; suggestPlanPages(); };
$('board-agent').onclick = () => action(async () => { await save(); await startJob('agent', {pages:Number($('page-count').value), instruction:$('board-instruction').value, append:!!project.pages.length, source_section:Number($('source-section').value)}); });
$('plan-agent').onclick = () => action(async () => { if (!project) throw new Error('作品を選んでください'); const pages = Number($('plan-pages').value), left = 24 - project.pages.length; if (!Number.isInteger(pages) || pages < 1 || pages > left) throw new Error(`ページ数は1〜${left}です（同じ作品で最大24ページ）`); if (remainingPlan().length && !confirm('今のページ割りの未作成分を、新しいページ割りで置き換えます。')) return; await save(); await runJob('page-plan', {pages, source_section:Number($('source-section').value), instruction:$('board-instruction').value}); });
$('board-auto').onclick = () => action(async () => { if (!remainingPlan().length) throw new Error('先に「1. ページ割り」を作ってください'); if (!confirm(`ページ割りの残り${remainingPlan().length}ページを、2ページずつ${llmLabel()}で字コンテにします。途中で止めるときは「今のページの後で停止」を押してください。`)) return; await save(); try { await runJob('agent-auto', {chunk:2, instruction:$('board-instruction').value}); } catch (e) { await load(project.id); throw e; } });
$('import-board').onclick = () => action(async () => { await api('/api/import', {project_id:project.id, revision:project.revision, storyboard:JSON.parse($('import-json').value)}); await startJob('compose'); });
function downloadFile(path,label){const link=el('a',label);link.href=asset(path);link.download=path.split('/').at(-1);$('downloads').prepend(link);link.click();}
$('export-pages').onclick=()=>action(async()=>{await save();const result=await runJob('export',{format:$('export-format').value,include_text:$('export-text').value==='yes'},false);downloadFile(result.path,`${result.kind.toUpperCase()} ZIP · ${result.pages}ページ`);});
for(const [id,scope] of [['export-system','system'],['export-carry','carry'],['export-settings','all']])$(id).onclick=()=>action(async()=>{await save();const r=await api('/api/config-export',{project_id:project.id,scope});downloadFile(r.path,{system:'共通システム設定JSON',carry:'引き継ぎ用の設定JSON',all:'全入力設定JSON'}[scope]);});
$('import-settings-file').onchange=()=>action(async()=>{const f=$('import-settings-file').files[0];if(!f)return;const config=JSON.parse(await f.text());await save();await api('/api/config-import',{project_id:project.id,revision:project.revision,config});await load(project.id);status('設定JSONを取り込みました');});
for(const id of ['export-controls','regenerate-existing','ignore-warnings-auto','panel-multiplier','rewrite-instruction','board-instruction','agent-instruction','source-section','page-count','generate-from','generate-to'])$(id).addEventListener('input',markDirty);
