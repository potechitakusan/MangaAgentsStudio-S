'use strict';
// Background jobs: start one and poll until it finishes. startJob reloads the screen and protects unsaved edits; runJob returns the job result.
async function waitJob(job) {
  if (job.llm_provider) { $('llm-provider').value = job.llm_provider; renderProvider(); }
  while (job.status === 'running') { status(job.message); await new Promise(r => setTimeout(r, 1300)); job = await api('/api/job/' + job.id); }
  if (job.status !== 'complete') throw new Error(job.message);
  if (dirty) { status('処理が完了しました。未保存の編集を保護しています。コピーして退避してから「読み直す」を押してください。'); }
  else { await load(project.id); status(job.message); }
}
async function startJob(kind, extra = {}) { if (!project) throw new Error('作品を選んでください'); const job = await api('/api/job', {kind,project_id:project.id,revision:project.revision,request_id:crypto.randomUUID(),...extra}); await waitJob(job); }
async function runJob(kind,extra={},reload=true){const job=await api('/api/job',{kind,project_id:project.id,revision:project.revision,request_id:crypto.randomUUID(),...extra});runningJob=job.id;let state=job;$('cancel-production').hidden=kind!=='produce';$('cancel-auto').hidden=kind!=='agent-auto';while(state.status==='running'){status(state.message);await new Promise(r=>setTimeout(r,1000));state=await api('/api/job/'+job.id);}runningJob=null;$('cancel-production').hidden=true;$('cancel-auto').hidden=true;if(state.status!=='complete')throw new Error(state.message);if(reload)await load(project.id);status(state.message);return state.result;}
$('cancel-auto').onclick=async()=>{if(runningJob){await api('/api/cancel',{request_id:runningJob});status('今作っているページが終わったら停止します');}};
$('cancel-production').onclick=async()=>{if(runningJob){await api('/api/cancel',{request_id:runningJob});status('現在の送信が終わり次第停止します');}};
