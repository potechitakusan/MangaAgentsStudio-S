"""Asynchronous, explicit user-initiated production jobs."""
from __future__ import annotations
import os
import subprocess
import sys
import threading
import time
from . import project as db
from . import novelai, strata_tasks, production, exporting, llm, pageplan
from .compose import compose

JOBS = {}
JOB_LOCK = threading.Lock()

def save_job(job):
    db.dump(db.ROOT / '.runtime' / 'jobs' / (job['id'] + '.json'), job)

def job_run(job, data):
    with llm.using(data.get('llm_provider','strata')):
        _job_run(job, data)

def run_agent_process(job, pid, data, suffix=''):
    """One storyboard or panel-edit run in its own process, inside work/<id> (studio.cli agent). Raises with the log tail on failure."""
    env = os.environ.copy()
    env.update(PYTHONPATH=str(db.ROOT), PYTHONIOENCODING='utf-8')
    env.pop('NOVELAI_API_KEY', None)
    command = [sys.executable, '-X', 'utf8', '-m', 'studio.cli', 'agent', pid,
               '--instruction', data.get('instruction') or '原作を漫画の字コンテとNovelAIプロンプトにして保存してください。',
               '--pages', str(data.get('pages', 1)), '--provider', llm.current()]
    if data.get('panel_id'): command.extend(['--panel', data['panel_id']])
    if data.get('append'): command.append('--append')
    if data.get('source_section') is not None: command.extend(['--section', str(data['source_section']+1)])
    log = db.directory(pid) / 'agent-runs' / (job['id'] + suffix + '.log')
    log.parent.mkdir(exist_ok=True)
    with log.open('w', encoding='utf-8') as stream:
        result = subprocess.run(command, cwd=db.directory(pid), env=env, stdout=stream, stderr=stream,
                                timeout=650, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise db.StudioError(log.read_text(encoding='utf-8')[-700:])

def new_pages_ja(pid, before, progress):
    """The storyboard leaves panel Japanese to the host: translate it for the pages that were just added."""
    after = len(db.load(pid)['pages'])
    pages = list(range(before + 1, after + 1))
    if not pages or not pageplan.missing_ja(db.load(pid), pages): return ''
    try:
        filled = pageplan.fill_ja(pid, pages, progress)
        left = len(pageplan.missing_ja(db.load(pid), pages))
        return f'。コマの日本語訳を{filled}件補いました' + (f'（未入力{left}件は「欠けた翻訳を補う」で補えます）' if left else '')
    except db.StudioError as exc:     # the pages are saved; only the Japanese check text is missing
        return f'。コマの日本語訳は補えませんでした（{str(exc)[:80]}）。「欠けた翻訳を補う」で補えます'

def _job_run(job, data):
    try:
        pid = data['project_id']
        kind = job['kind']
        def progress(message):
            job['message']=message; save_job(job)
        if kind == 'agent':
            before = len(db.load(pid)['pages'])
            progress(llm.label() + 'で字コンテを作成しています')
            run_agent_process(job, pid, data)
            note = '' if data.get('panel_id') else new_pages_ja(pid, before, progress)   # finish the Japanese before saying "done"
            job['message'] = llm.label()+'の編集と組版が完了しました' + note
        elif kind == 'page-plan':
            progress(llm.label() + 'でページ割りを作成しています（原作全体を1回で割り振ります）')
            p = pageplan.make(pid, data['revision'], data.get('source_section', 0), data.get('pages'), data.get('instruction', ''))
            job['message'] = f'ページ割りを作成しました（PAGE {pageplan.remaining(p)[0]["page"]}〜{pageplan.remaining(p)[-1]["page"]}）。内容を確認・編集してから字コンテを作ってください'
        elif kind == 'agent-auto':
            # Finite: the rest of the page plan, a few pages per run, each run a fresh session. Stops at the first failure.
            size = data.get('chunk', 2)
            if type(size) is not int or not 1 <= size <= 4: raise db.StudioError('1回のページ数は1〜4です')
            total = len(pageplan.remaining(db.load(pid)))
            if not total: raise db.StudioError('ページ割りに未作成のページがありません。先にページ割りを作ってください')
            made, notes = 0, ''
            while True:
                p = db.load(pid)
                chunk = pageplan.next_chunk(p, size)
                if not chunk: break
                if job.get('cancel_requested'): notes += '。停止の指示により途中で止めました'; break
                first, last = chunk[0]['page'], chunk[-1]['page']
                progress(f'{llm.label()}で字コンテ作成 PAGE {first}〜{last}（残り{len(pageplan.remaining(p))}ページ）')
                try: run_agent_process(job, pid, dict(pages=len(chunk), append=bool(p['pages']), source_section=chunk[0]['section'], instruction=data.get('instruction', '')), f'-p{first}')
                except db.StudioError as exc:
                    raise db.StudioError(f'PAGE {first}〜{last}で停止しました（作成済み{made}ページは保存済み。もう一度押すと続きから再開します）: ' + str(exc)[-500:]) from None
                made += len(chunk)
                ja = new_pages_ja(pid, first - 1, progress)
                if '補えません' in ja or '未入力' in ja: notes = ja
            job['message'] = f'{llm.label()}でページ割りの{made}ページを字コンテにしました' + notes
        elif kind == 'generate':
            p = db.load(pid)
            if p['revision'] != data['revision']: raise db.StudioError('編集競合: 作品を読み直してください')
            targets = db.generation_targets(p, data.get('panel_id'), data.get('start_page'), data.get('end_page'))
            for i, panel in enumerate(targets):
                if i: time.sleep(3)
                job['message'] = f'NovelAI生成 {i + 1}/{len(targets)}: {panel}'
                save_job(job)
                record = novelai.generate(pid, panel, job['id'] + f'-{i}', data['revision'] if i == 0 else None)
                if record['status'] != 'complete': raise db.StudioError('生成結果が未確定です。履歴を確認してください')
                compose(pid)
            job['message'] = f'{len(targets)}コマの生成と組版が完了しました'
        elif kind == 'preview':
            job['result']=novelai.generate(pid,None,job['id'],data['revision'],preview=data['preview'])
            if job['result']['status']!='complete': raise db.StudioError('試し描きの結果が未確定です。台帳を確認してください')
            job['message']='試し描きを保存しました（漫画の採用画像は変更しません）'
        elif kind == 'wardrobe':
            strata_tasks.wardrobe(pid,data['revision'],data['character_id'],data.get('instruction',''))
            job['message']='固定外見と衣装を保存しました。内容と既存コマの衣装指定を確認してください'
        elif kind == 'generate-page':
            record = novelai.generate(pid, None, job['id'], data['revision'], page_number=data['page_number'])
            if record['status'] != 'complete': raise db.StudioError('ページ生成結果が未確定です。台帳を確認してください')
            compose(pid)
            job['message'] = '1ページの一括生成が完了しました'
        elif kind == 'compose':
            compose(pid)
            job['message'] = 'ページを組み直しました'
        elif kind == 'translate':
            job['result']=strata_tasks.translate(pid,data['items'],data['target'])
            job['message']='翻訳が完了しました'
        elif kind == 'review':
            job['result']=strata_tasks.review(pid,progress)
            job['message']=f'{llm.label()}点検完了 · 警告{len(job["result"]["warnings"])}件'
        elif kind == 'voices':
            strata_tasks.voices(pid,data['revision'],data.get('page_number'),progress)
            compose(pid);job['message']='共通の口調を台詞へ反映しました'
        elif kind == 'rewrite':
            p=db.load(pid)
            if p['revision']!=data['revision']: raise db.StudioError('編集競合: 最新の内容で実行してください')
            numbers=[data['page_number']] if data.get('page_number') else list(range(1,len(p['pages'])+1))
            for i,number in enumerate(numbers):
                progress(f'{llm.label()}改稿 {i+1}/{len(numbers)} · {number}ページ')
                p=strata_tasks.rewrite_page(pid,p['revision'],number,data.get('instruction','共通設定に合わせて外見の記述と口調を整えてください'))
                compose(pid)
            job['message']=f'{len(numbers)}ページの改稿を保存しました。画像への反映は再生成してください'
        elif kind == 'produce':
            job['result']=production.produce(pid,data['revision'],job['id'],scope=data.get('scope','all'),page_number=data.get('page_number'),panel_id=data.get('panel_id'),regenerate=bool(data.get('regenerate')),ignore_warnings=bool(data.get('ignore_warnings')),progress=progress,cancelled=lambda:job.get('cancel_requested'))
            job['message']='点検の警告を確認してください' if job['result']['stage']=='warnings' else f'{job["result"]["generated"]}枚の生成・組版が完了しました'+('（点検の警告は無視しました）' if job['result'].get('warnings_ignored') else '')
        elif kind == 'export':
            job['result']=exporting.bundle(pid,data.get('format','png'),data.get('include_text',True),progress)
            job['message']='書き出しが完了しました'
        job.update(status='complete', finished_at=db.now())
    except Exception as exc:
        job.update(status='failed', message=str(exc)[:900], finished_at=db.now())
    finally:
        save_job(job)

def start_job(data):
    job_id = db.identifier(data.get('request_id'))
    kind = data.get('kind')
    if kind not in ('agent','page-plan','agent-auto','generate','generate-page','compose','translate','review','rewrite','voices','produce','export','preview','wardrobe'): raise db.StudioError('処理種別が不正です')
    provider=llm.validate_provider(data.get('llm_provider','strata'))
    db.load(data['project_id'])
    with JOB_LOCK:
        if job_id in JOBS: return JOBS[job_id]
        if any(j['status'] == 'running' for j in JOBS.values()): raise db.StudioError('別の処理が実行中です')
        job = dict(id=job_id, kind=kind, project_id=data['project_id'], llm_provider=provider, status='running', message='処理を開始しています', started_at=db.now())
        JOBS[job_id] = job
        save_job(job)
        threading.Thread(target=job_run, args=(job, data), daemon=True).start()
        return job
