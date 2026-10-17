"""Loopback web UI; asynchronous, explicit user-initiated production jobs."""
from __future__ import annotations
import json
import mimetypes
import os
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit, parse_qs
from . import project as db
from . import novelai
from . import wholepage
from . import workspace, layouts, exporting, profiles, llm, backup
from .compose import compose
from .jobs import JOBS, JOB_LOCK, save_job, job_run, _job_run, start_job

TOKEN = secrets.token_urlsafe(32)
WEB = db.ROOT / 'web'
STATIC_TYPES = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8'}

def get_session(self, query):
    return self.send(dict(token=TOKEN, templates={k: len(v) for k, v in db.TEMPLATES.items()},layouts=layouts.catalog(db.TEMPLATES),styles=workspace.STYLE_PRESETS,system=workspace.system_defaults(),expressions=profiles.EXPRESSIONS,llm_providers=llm.options()))

def get_tag_dictionary(self, query):
    # Candidate tags for the "辞書からタグを選ぶ" picker (knowledge/novelai/tag-dictionary.json, built by tools/prompt_lab/build_tag_dictionary.py).
    path = db.ROOT / 'knowledge' / 'novelai' / 'tag-dictionary.json'
    return self.send(db.read_json(path) if path.exists() else dict(groups={}, concepts=[]))

def get_projects(self, query):
    return self.send(db.list_projects())

def get_project(self, path):
    project_id = path.split('/')[-1]
    p = db.load(project_id)
    manifest = db.directory(project_id) / 'output' / 'manifest.json'
    generation_state={}
    for number,page in enumerate(p['pages'],1):
        if p.get('render_mode')=='whole':
            try: generation_state[f'page-{number}']=novelai.is_stale(p,page_number=number)
            except db.StudioError: generation_state[f'page-{number}']=True
        else:
            for panel in page['panels']:
                try: generation_state[panel['id']]=novelai.is_stale(p,panel=panel)
                except db.StudioError: generation_state[panel['id']]=True
    return self.send(dict(project=p, manifest=db.read_json(manifest) if manifest.exists() else None,
                          sections=db.source_sections(project_id),
                          whole_settings=[wholepage.settings(p,n) for n in range(1,len(p['pages'])+1)],
                          inputs=workspace.editable(p),generation_state=generation_state,review=db.read_json(db.directory(project_id)/'review.json') if (db.directory(project_id)/'review.json').exists() else None))

def get_job(self, path):
    job = JOBS.get(path.split('/')[-1])
    if not job: raise db.StudioError('ジョブがありません')
    return self.send(job)

def get_jobs(self, query):
    return self.send([j for j in JOBS.values() if j['status'] == 'running'])

def get_project_storage(self, query):
    return self.send(backup.storage(query.get('project', [''])[0]))

def get_ledger(self, query):
    return self.send(novelai.ledger_summary())

def post_budget(self, data):
    return self.send(novelai.set_budget(data.get('limit'), bool(data.get('reset'))))

def get_asset(self, query):
    project_id = query.get('project', [''])[0]
    relative = query.get('path', [''])[0]
    # Only render products and generation history; no arbitrary project files.
    if not relative.startswith(('images/', 'output/', 'exports/')): raise db.StudioError('画像・組版・書出し結果だけ取得できます')
    target = db.asset_path(project_id, relative)
    if not target.is_file(): return self.send({'error': 'ファイルなし'}, 404)
    return self.send(target.read_bytes(), content_type=mimetypes.guess_type(target.name)[0] or 'application/octet-stream')

def post_create(self, data):
    p=db.create(data['id'], data['title'], data['source'])
    mode=data.get('render_mode','panels')
    if mode not in ('panels','whole'): raise db.StudioError('生成方式が不正です')
    style=dict(source=data['source'],text_mode='overlay',render_mode=mode,style_ja='アニメ塗り、明瞭な線、映画的な照明')
    # New works layer their own (empty) prompts on the general ones; a caller that sends 'system' keeps the old standalone behaviour.
    if 'system' in data: p=workspace.save(p['id'],p['revision'],dict(system=data['system'],**style))
    else: p=workspace.save(p['id'],p['revision'],dict(system=dict(workspace.EMPTY_SYSTEM),system_layered=True,**style))
    return self.send(p)

def post_workspace(self, data):
    return self.send(workspace.save(data['project_id'],data['revision'],data['inputs']))

def post_default_system(self, data):
    return self.send(workspace.save_defaults(data['system']))

def post_preview_request(self, data):
    return self.send(profiles.preview(db.load(data['project_id']),data['preview']))

def post_preview_history(self, data):
    return self.send(profiles.history(data['project_id']))

def post_layout(self, data):
    p=workspace.resize_page(data['project_id'],data['revision'],data['page_number'],data['template'])
    compose(p['id']); return self.send(p)

def post_config_export(self, data):
    return self.send(exporting.config_download(data['project_id'],data['scope']))

def post_config_import(self, data):
    p=workspace.config_import(data['project_id'],data['revision'],data['config'])
    if p['pages']: compose(p['id'])
    return self.send(p)

def post_cancel(self, data):
    job=JOBS.get(data['request_id'])
    if not job or job['kind'] not in ('produce','agent-auto'): raise db.StudioError('停止可能な処理がありません')
    job['cancel_requested']=True; save_job(job); return self.send(dict(message='現在の送信完了後に停止します'))

def post_edit(self, data):
    result = db.edit_panel(data['project_id'], data['revision'], data['panel_id'], data['patch'])
    return self.send(result)

def post_mode(self, data):
    p = wholepage.set_mode(data['project_id'],data['revision'],data['mode'])
    if p['pages']: compose(data['project_id'])
    return self.send(p)

def post_page_edit(self, data):
    return self.send(wholepage.save(data['project_id'],data['revision'],data['page_number'],data['settings']))

def post_page_plan(self, data):
    p = db.load(data['project_id'])
    return self.send(wholepage.from_panels(p,data['page_number']))

def post_page_request(self, data):
    p = db.load(data['project_id'])
    return self.send(dict(request=novelai.build_page_request(p,data['page_number']),
                          preview=wholepage.compile_prompt(p,data['page_number'])))

def post_page_adopt(self, data):
    p = wholepage.adopt(data['project_id'],data['revision'],data['page_number'],data['candidate_id'])
    compose(data['project_id'])
    return self.send(p)

def post_import(self, data):
    return self.send(db.import_board(data['project_id'], data['revision'], data['storyboard']))

def post_adopt(self, data):
    p = novelai.adopt(data['project_id'], data['revision'], data['panel_id'], data['candidate_id'])
    compose(data['project_id'])
    return self.send(p)

def post_project_backup(self, data):
    return self.send(backup.create(data['project_id'], full=data.get('full') is True))

def post_project_delete(self, data):
    with JOB_LOCK: busy = any(j['status'] == 'running' and j['project_id'] == data['project_id'] for j in JOBS.values())
    return self.send(backup.soft_delete(data['project_id'], data['confirm'], busy=busy))

def post_open_folder(self, data):
    return self.send(dict(path=backup.open_folder(data['kind'], data.get('project_id'))))

def post_job(self, data):
    return self.send(start_job(data))

def post_status(self, data):
    return self.send(novelai.status())

def post_request(self, data):
    p = db.load(data['project_id'])
    return self.send(novelai.build_request(p, db.panel_by_id(p, data['panel_id'])))

GET_ROUTES = {
    '/api/session': get_session,
    '/api/projects': get_projects,
    '/api/tag-dictionary': get_tag_dictionary,
    '/api/jobs': get_jobs,
    '/api/project-storage': get_project_storage,
    '/api/ledger': get_ledger,
    '/asset': get_asset,
}

POST_ROUTES = {
    '/api/create': post_create,
    '/api/workspace': post_workspace,
    '/api/default-system': post_default_system,
    '/api/preview-request': post_preview_request,
    '/api/preview-history': post_preview_history,
    '/api/layout': post_layout,
    '/api/config-export': post_config_export,
    '/api/config-import': post_config_import,
    '/api/cancel': post_cancel,
    '/api/budget': post_budget,
    '/api/edit': post_edit,
    '/api/mode': post_mode,
    '/api/page-edit': post_page_edit,
    '/api/page-plan': post_page_plan,
    '/api/page-request': post_page_request,
    '/api/page-adopt': post_page_adopt,
    '/api/import': post_import,
    '/api/adopt': post_adopt,
    '/api/project-backup': post_project_backup,
    '/api/project-delete': post_project_delete,
    '/api/open-folder': post_open_folder,
    '/api/job': post_job,
    '/api/status': post_status,
    '/api/request': post_request,
}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # Requests can contain a user's project name, no default access log.

    def send(self, data, status=200, content_type='application/json; charset=utf-8'):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8') if not isinstance(data, bytes) else data
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def verify_host(self):
        if self.headers.get('Host') not in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'):
            raise db.StudioError('ローカルURLから接続してください')

    def do_GET(self):
        try:
            self.verify_host()
            url = urlsplit(self.path)
            path = unquote(url.path)
            query = parse_qs(url.query)
            handler = GET_ROUTES.get(path)
            if handler: return handler(self, query)
            if path.startswith('/api/project/'): return get_project(self, path)
            if path.startswith('/api/job/'): return get_job(self, path)
            # The screen is plain files under web/ (html, css/, js/); nothing else is ever served from there.
            target = (WEB / ('index.html' if path == '/' else path.lstrip('/'))).resolve()
            if target.suffix in STATIC_TYPES and target.is_file() and target.is_relative_to(WEB.resolve()):
                return self.send(target.read_bytes(), content_type=STATIC_TYPES[target.suffix])
            self.send({'error': 'Not found'}, 404)
        except (db.StudioError, ValueError, KeyError, OSError) as exc:
            self.send({'error': str(exc)}, 400)

    def drain(self):
        """Read and discard a body of acceptable size, so an early error reply is not turned into a connection reset."""
        try: size = int(self.headers.get('Content-Length', '0'))
        except ValueError: return
        if 0 < size <= 1200000: self.rfile.read(size)

    def do_POST(self):
        try:
            try: self.verify_host()
            except db.StudioError: self.drain(); raise
            expected_origin = 'http://' + self.headers['Host']
            if self.headers.get('Origin') not in (None, expected_origin) or self.headers.get('X-Studio-Token') != TOKEN:
                self.drain(); return self.send({'error': 'この画面を再読み込みしてください'}, 403)
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 1200000: raise db.StudioError('要求サイズが不正です')
            data = json.loads(self.rfile.read(length))
            path = urlsplit(self.path).path
            handler = POST_ROUTES.get(path)
            if handler: return handler(self, data)
            self.send({'error': 'Not found'}, 404)
        except (db.StudioError, ValueError, TypeError, KeyError, OSError) as exc:
            self.send({'error': str(exc)}, 400)

class StudioServer(ThreadingHTTPServer):
    """A browser that leaves before its reply is written (a page change cancels image loads, a tab is closed) is normal.
    Windows reports it as WinError 10053/10054; such a request is simply dropped instead of printing a traceback.
    Any other error is still printed."""
    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)): return
        super().handle_error(request, client_address)

def serve(port=8765):
    for file in (db.ROOT / '.runtime' / 'jobs').glob('*.json'):
        job = db.read_json(file)
        if job['status'] == 'running':
            job.update(status='interrupted', message='サーバー終了で処理が中断されました。生成台帳を確認してください')
            save_job(job)
        JOBS[job['id']] = job
    server = StudioServer(('127.0.0.1', port), Handler)
    pidfile = db.ROOT / '.runtime' / 'server.pid'
    pidfile.parent.mkdir(exist_ok=True)
    pidfile.write_text(str(os.getpid()), encoding='ascii')
    backup.start_auto()  # Light backups of changed works at start-up and every 30 minutes.
    print(f'MangaAgentsStudio-S: http://127.0.0.1:{port}', flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        server.server_close()
        if pidfile.exists() and pidfile.read_text(encoding='ascii').strip() == str(os.getpid()):
            pidfile.unlink()
