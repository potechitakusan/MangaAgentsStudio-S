import http.client
import json
import shutil
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from studio import server, jobs, novelai, workspace, images, project as db


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.gettempdir()) / ('manga-routes-test-' + uuid.uuid4().hex)
        self.root.mkdir()
        self.addCleanup(self.remove_root)
        for p in (patch.object(db, 'WORK', self.root / 'work'),
                  patch.object(novelai, 'ledger_path', lambda: self.root / 'ledger.json'),
                  patch.object(workspace, 'system_defaults', return_value=dict(workspace.DEFAULT_SYSTEM)),
                  patch.dict(server.JOBS, {}, clear=True),
                  patch.object(novelai, 'request', side_effect=AssertionError('Unexpected NovelAI request')),
                  patch.object(server, 'start_job', side_effect=AssertionError('Unexpected production job'))):
            p.start(); self.addCleanup(p.stop)
        self.project = db.create('test', 'テスト', '原作')
        self.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.worker = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.stop_server)

    def remove_root(self):
        target = self.root.resolve()
        assert target.parent == Path(tempfile.gettempdir()).resolve() and target.name.startswith('manga-routes-test-')
        shutil.rmtree(target)

    def stop_server(self):
        self.http.shutdown(); self.http.server_close(); self.worker.join()

    def request(self, method, path, body=b'{}', headers=None, token=True, content_type='application/json; charset=utf-8'):
        values = {'X-Studio-Token': server.TOKEN} if method == 'POST' and token else {}
        values.update(headers or {})
        connection = http.client.HTTPConnection('127.0.0.1', self.http.server_port, timeout=5)
        try:
            connection.request(method, path, body if method == 'POST' else None, values)
            response = connection.getresponse()
            payload = response.read()
            self.assertEqual(response.getheaders()[2:], [
                ('Content-Type', content_type), ('Content-Length', str(len(payload))),
                ('Cache-Control', 'no-store'), ('X-Content-Type-Options', 'nosniff'),
                ('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'")])
            return response.status, payload
        finally:
            connection.close()

    def assert_json(self, method, path, status, value, **kwargs):
        self.assertEqual(self.request(method, path, **kwargs),
                         (status, json.dumps(value, ensure_ascii=False).encode('utf-8')))

    def paths(self):
        return sorted(set(server.GET_ROUTES) | set(server.POST_ROUTES) |
                      {'/api/project/test', '/api/job/test', '/api/unknown'})

    def test_all_routes_require_post_token(self):
        for path in self.paths():
            with self.subTest(path=path):
                self.assert_json('POST', path, 403, {'error': 'この画面を再読み込みしてください'}, token=False)

    def test_library_confirmation_and_restore_routes(self):
        def call(data):
            return self.request('POST', '/api/library-visibility', body=json.dumps(data).encode())
        self.assertEqual(call(dict(project_id='test', excluded=True))[0], 400)
        self.assertEqual(call(dict(project_id='test', excluded=True, confirmed=True))[0], 200)
        self.assert_json('GET', '/api/projects', 200, [])
        code, payload = self.request('GET', '/api/library')
        self.assertEqual(code, 200); self.assertTrue(json.loads(payload)[0]['excluded'])
        self.assertEqual(call(dict(project_id='test', excluded=False))[0], 200)
        self.assertEqual(db.load('test')['revision'], 0)

    def test_position_save_compose_and_conflict_routes(self):
        from PIL import Image
        folder = db.directory('test') / 'images'; folder.mkdir()
        Image.new('RGB', (100,100), 'red').save(folder / 'art.png')
        board = dict(output_method='novelai', title='テスト', style='illustration', characters=[],
            pages=[dict(template='splash', panels=[dict(id='p1',scene='色見本',prompt='red',characters=[],dialogue=[])])])
        p = db.import_board('test', 0, board)
        p = db.mutate('test', p['revision'], lambda p: p['pages'][0]['panels'][0].update(image='images/art.png'))
        request = json.dumps(dict(project_id='test', revision=p['revision'], panel_id='p1', transform=dict(x=.25,y=0,scale=.5))).encode()
        self.assertEqual(self.request('POST', '/api/panel-position', body=request)[0], 200)
        self.assertEqual(self.request('POST', '/api/panel-position', body=request)[0], 400)
        self.assertEqual(self.request('POST', '/api/compose-page', body=b'{"project_id":"test","page_number":1}')[0], 200)
        self.assertTrue((db.directory('test')/'output/page-001.png').exists())
        for number in ('null', 'true', '0', '2'):
            self.assertEqual(self.request('POST', '/api/compose-page', body=('{"project_id":"test","page_number":'+number+'}').encode())[0], 400)

    def test_all_routes_reject_invalid_host_before_dispatch(self):
        for method in ('GET', 'POST'):
            for path in self.paths():
                with self.subTest(method=method, path=path):
                    self.assert_json(method, path, 400, {'error': 'ローカルURLから接続してください'},
                                     headers={'Host': 'evil.example'}, token=False)

    def test_post_routes_reject_wrong_origin_and_token(self):
        for path in server.POST_ROUTES:
            for headers in ({'Origin': 'https://evil.example'}, {'X-Studio-Token': 'invalid'}):
                with self.subTest(path=path, headers=headers):
                    self.assert_json('POST', path, 403, {'error': 'この画面を再読み込みしてください'}, headers=headers)

    def test_unknown_paths_return_404(self):
        for method in ('GET', 'POST'):
            for path in ('/not-a-route', '/api/unknown', '/api/projects/', '/api/create/'):
                with self.subTest(method=method, path=path):
                    self.assert_json(method, path, 404, {'error': 'Not found'})

    def test_get_session(self):
        status, body = self.request('GET', '/api/session')
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(set(data), {'token', 'templates', 'layouts', 'styles', 'system', 'expressions', 'llm_providers'})
        self.assertEqual(data['token'], server.TOKEN)
        self.assertEqual(data['templates'], {k: len(v) for k, v in db.TEMPLATES.items()})
        self.assertEqual(data['system'], workspace.DEFAULT_SYSTEM)

    def test_qwen_page_preview_counts_actual_sent_text(self):
        db.import_board('test',0,db.read_json(db.ROOT/'templates/board.example.json'))
        p=db.load('test');p['output_method']='qwen';
        for page in p['pages']: page.get('whole',{}).pop('draft',None)
        db.dump(db.directory('test')/'project.json',p)
        status,body=self.request('POST','/api/page-request',json.dumps(dict(project_id='test',page_number=1)).encode())
        result=json.loads(body)
        self.assertEqual(status,200)
        self.assertEqual(result['request']['provider'],'qwen')
        self.assertEqual(result['preview']['prompt_characters'],len(result['request']['input']))

    def test_get_projects(self):
        self.assert_json('GET', '/api/projects', 200, [dict(id='test', title='テスト', revision=0, pages=0,output_method='novelai',compatible=True)])

    def test_client_disconnects_are_not_printed(self):
        import contextlib, io
        quiet = server.StudioServer.handle_error
        for exc, printed in ((ConnectionAbortedError(10053, 'aborted'), False), (ConnectionResetError(), False), (BrokenPipeError(), False), (ValueError('real bug'), True)):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                try: raise exc
                except Exception: quiet(server.StudioServer.__new__(server.StudioServer), None, ('127.0.0.1', 1))
            self.assertEqual(bool(err.getvalue()), printed, type(exc).__name__)

    def test_tag_dictionary_is_served(self):
        status, body = self.request('GET', '/api/tag-dictionary')
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIn('camera', data['groups'])
        concept = next(c for c in data['concepts'] if c['id'] == 'cam_close_up')
        self.assertIn('close-up', concept['tags'])
        # names only: no counts or explicit-share figures travel with the dictionary
        self.assertEqual(set(concept), {'id', 'ja', 'group', 'people', 'tags', 'related'})
        self.assertTrue(all('_' not in t for c in data['concepts'] for t in c['tags'] if any(ch.isalpha() for ch in t)))

    def test_carry_export_leaves_pages_and_source_behind(self):
        from studio import workspace
        out = workspace.config_export('test', 'carry')
        self.assertEqual(out['schema'], 'manga-studio-inputs-v4')
        self.assertEqual(set(out['inputs']) - set(workspace.CARRY_FIELDS), set())
        self.assertNotIn('pages', out['inputs']); self.assertNotIn('source', out['inputs'])
        db.create('next', '続き', '## 第一章\n続きの原作')
        p = workspace.config_import('next', 0, out)          # the follow-up work takes the look and catalogs only
        self.assertEqual(p['characters'], db.load('test')['characters'])
        self.assertEqual(p['pages'], [])
        self.assertIn('続きの原作', workspace.editable(p)['source'])

    def test_get_ledger(self):
        db.dump(novelai.ledger_path(), dict(spent=7, halted='halted', requests={
            'a': {'status': 'sending'}, 'b': {'status': 'unknown'}, 'c': {'status': 'complete'}}))
        self.assert_json('GET', '/api/ledger', 200, dict(limit=novelai.MAX_ANLAS, spent=7, halted='halted', unknown=2, resets=[], limit_changes=[]))

    def test_project_and_job_prefixes(self):
        status, body = self.request('GET', '/api/project/test')
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(set(data), {'project', 'manifest', 'sections', 'whole_settings', 'inputs', 'generation_state', 'review', 'references'})
        self.assertEqual(data['project'], self.project)
        job = dict(id='active', status='running')
        jobs.JOBS.update(active=job, done=dict(id='done', status='complete'))
        self.assert_json('GET', '/api/job/active', 200, job)
        self.assert_json('GET', '/api/jobs', 200, [job])
        self.assert_json('GET', '/api/project/missing', 400, {'error': '作品がありません'})
        self.assert_json('GET', '/api/job/missing', 400, {'error': 'ジョブがありません'})

    def test_project_storage_without_id(self):
        self.assert_json('GET', '/api/project-storage', 400, {'error': 'IDは英数字・ハイフン・アンダースコアで1〜64文字です'})

    def test_project_storage_missing_project_is_an_error(self):
        self.assertFalse(db.directory('missing').exists())
        self.assert_json('GET', '/api/project-storage?project=missing', 400, {'error': '作品がありません'})

    def test_rejected_posts_with_a_body_are_always_answered(self):
        # The server must read a small body before replying 403/400; otherwise Windows may reset the connection mid-reply.
        for _ in range(30):
            self.assert_json('POST', '/api/edit', 403, {'error': 'この画面を再読み込みしてください'}, body=b'{"x":"' + b'a' * 4000 + b'"}', token=False)
            self.assert_json('POST', '/api/edit', 400, {'error': 'ローカルURLから接続してください'}, body=b'{"x":"' + b'a' * 4000 + b'"}', headers={'Host': 'example.test'})

    def test_post_size_limit(self):
        for length in ('0', '-1', '1200001'):
            with self.subTest(length=length):
                self.assert_json('POST', '/api/unknown', 400, {'error': '要求サイズが不正です'}, headers={'Content-Length': length}, body=b'')   # a body the server will not read could reset the connection
        self.assert_json('POST', '/api/unknown', 404, {'error': 'Not found'}, body=b'{}' + b' ' * 1199998)

    def test_route_exceptions_return_400(self):
        for error in (db.StudioError, ValueError, KeyError, OSError):
            with self.subTest(method='GET', error=error), patch.object(db, 'list_projects', side_effect=error('失敗')):
                self.assert_json('GET', '/api/projects', 400, {'error': str(error('失敗'))})
        for error in (db.StudioError, ValueError, TypeError, KeyError, OSError):
            with self.subTest(method='POST', error=error), patch.object(workspace, 'save_defaults', side_effect=error('失敗')):
                self.assert_json('POST', '/api/default-system', 400, {'error': str(error('失敗'))}, body=b'{"system": {}}')

    def test_query_and_path_decoding(self):
        self.assertEqual(self.request('GET', '/api/%73ession?from=test'), self.request('GET', '/api/session'))
        with patch.object(workspace, 'save_defaults', return_value={'saved': True}) as saved:
            self.assert_json('POST', '/api/default-system?from=test', 200, {'saved': True}, body=b'{"system": {}}',
                             headers={'Origin': f'http://127.0.0.1:{self.http.server_port}'})
            saved.assert_called_once_with({})
        self.assert_json('POST', '/api/%73tatus', 404, {'error': 'Not found'})

    def test_static_files_keep_suffix_and_directory_limits(self):
        web = self.root / 'web'
        web.mkdir()
        (self.root / 'outside.html').write_bytes(b'outside')
        with patch.object(server, 'WEB', web):
            for name, content_type in (('index.html', 'text/html; charset=utf-8'),
                                       ('app.js', 'text/javascript; charset=utf-8'),
                                       ('style.css', 'text/css; charset=utf-8')):
                payload = name.encode()
                (web / name).write_bytes(payload)
                self.assertEqual(self.request('GET', '/' + name, content_type=content_type), (200, payload))
            self.assertEqual(self.request('GET', '/', content_type='text/html; charset=utf-8'), (200, b'index.html'))
            (web / 'hidden.txt').write_bytes(b'hidden')
            for path in ('/hidden.txt', '/../outside.html', '/missing.html'):
                self.assert_json('GET', path, 404, {'error': 'Not found'})


if __name__ == '__main__':
    unittest.main()
