"""Finite, reviewed production batches. Never retries an image request."""
import time
from . import project as db, workspace, strata_tasks, novelai, images
from .compose import compose

def produce(project_id, revision, request_id, scope='all', page_number=None, panel_id=None,
            regenerate=False, ignore_warnings=False, progress=None, cancelled=None):
    p=db.load(project_id)
    if p['revision']!=revision: raise db.StudioError('編集競合: 最新の内容で実行してください')
    if scope not in ('all','page','panel'): raise db.StudioError('生成範囲が不正です')
    pages=None   # the pages the review and the input check cover; None = the whole work
    if scope=='page':
        from .wholepage import page_at
        page_at(p,page_number); pages=[page_number]
    if scope=='panel':
        db.panel_by_id(p,panel_id)
        if p.get('render_mode')=='whole': raise db.StudioError('ページ一括モードではページ単位で再生成してください。コマ別の生成はモードを切り替えます')
        pages=[next(i for i,page in enumerate(p['pages'],1) if any(c['id']==panel_id for c in page['panels']))]
    digest=workspace.fingerprint(p,pages)
    def valid(path,fingerprint):
        if not path.exists(): return None
        saved=db.read_json(path)
        return saved if saved.get('fingerprint')==fingerprint and saved.get('complete') else None
    report=valid(strata_tasks.review_path(project_id,pages),digest)
    if report is None and pages:   # a still-valid review of the whole work also covers any one page of it
        whole=valid(strata_tasks.review_path(project_id),workspace.fingerprint(p))
        if whole: report={**whole,'warnings':strata_tasks.scoped_warnings(whole,pages)}
    if report is None: report=strata_tasks.review(project_id,progress,pages=pages)
    if report['warnings'] and not ignore_warnings:
        return dict(stage='warnings',review=report,generated=0)
    selected=pages or list(range(1,len(p['pages'])+1))
    targets=[]
    for number in selected:
        if p.get('render_mode')=='whole':
            if regenerate or images.is_stale(p,page_number=number): targets.append((None,number))
        else:
            for panel in p['pages'][number-1]['panels']:
                if scope=='panel' and panel['id']!=panel_id: continue
                if regenerate or images.is_stale(p,panel=panel): targets.append((panel['id'],None))
    results=[]
    for i,(pid,number) in enumerate(targets):
        if cancelled and cancelled(): raise db.StudioError('生成を停止しました。完了した画像は保存済みです')
        current=db.load(project_id)
        if workspace.fingerprint(current,pages)!=digest: raise db.StudioError('生成中に入力が変更されたため、残りの生成を停止しました')
        if progress: progress(f'{images.label()} {i+1}/{len(targets)} · '+(f'{number}ページ' if number else pid))
        record=images.generate(project_id,pid,request_id+f'-{i}',current['revision'],page_number=number)
        if record['status']!='complete': raise db.StudioError('生成が未確定のため停止しました')
        results.append(record['id'])
        compose(project_id)
        if i+1<len(targets) and images.current()['provider']=='novelai': time.sleep(3)
    compose(project_id)  # Dialogue-only edits need composition even when all artwork is reusable.
    return dict(stage='complete',generated=len(results),request_ids=results,review_id=report['id'],warnings_ignored=bool(report['warnings'] and ignore_warnings))
