import copy
import io
import json
import shutil
import uuid
import unittest
import threading
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from studio import project as db, novelai, agent, wholepage, images, qwen
from studio.compose import compose, crop_box
from studio.server import Handler, ThreadingHTTPServer, TOKEN

REAL_ROOT = db.ROOT

class StudioTests(unittest.TestCase):
    def setUp(self):
        self.root = REAL_ROOT / '.runtime' / ('test-' + uuid.uuid4().hex)
        self.root.mkdir(parents=True)
        self.patches = [patch.object(db, 'WORK', self.root / 'work'), patch.object(novelai, 'ledger_path', lambda: self.root / 'ledger.json'), patch.object(qwen, 'ledger_path', lambda: self.root / 'qwen-ledger.json'), patch.object(images,'defaults',return_value=dict(images.DEFAULTS))]
        for p in self.patches: p.start()
        self.board = db.read_json(REAL_ROOT / 'templates/board.example.json')
        db.create('test', 'テスト', '澪が傘を開いた')
        db.import_board('test', 0, self.board)
        self.state = dict(active=True, tier=3, balance=10000, usage=dict(percent=50, isNegative=False))
        self.png = io.BytesIO()
        Image.new('RGB', (1216, 832), '#abcdef').save(self.png, 'PNG')

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        target = self.root.resolve()
        assert target.is_relative_to((REAL_ROOT / '.runtime').resolve()) and target.name.startswith('test-')
        shutil.rmtree(target)

    def test_conflict_and_history(self):
        db.edit_panel('test', 1, 'p1-1', {'scene': '変更'})
        with self.assertRaisesRegex(db.StudioError, '編集競合'):
            db.edit_panel('test', 1, 'p1-1', {'scene': '古い変更'})
        self.assertEqual(db.panel_by_id(db.load('test'), 'p1-1')['scene'], '変更')
        self.assertEqual(db.read_json(db.directory('test') / 'history/project-00001.json')['revision'], 1)

    def test_input_geometry_and_paths(self):
        bad = copy.deepcopy(self.board); bad['pages'][0]['panels'][0]['characters'][0]['x'] = 2
        with self.assertRaises(db.StudioError): db.validate_board(bad)
        bad = copy.deepcopy(self.board); bad['pages'][0]['panels'][1]['id'] = 'p1-1'
        with self.assertRaises(db.StudioError): db.validate_board(bad)
        for name in ('../escape', 'C:/escape', 'test/../../'):
            with self.assertRaises(db.StudioError): db.directory(name)
        with self.assertRaises(db.StudioError): db.asset_path('test', '../../.env')

    def test_compose_handoff_and_crop(self):
        manifest = compose('test')
        self.assertEqual(manifest['canvas'], [1200, 1800])
        self.assertEqual(len(manifest['pages'][0]['panels']), 3)
        self.assertEqual(manifest['pages'][0]['panels'][0]['text_layers'][0]['text'], 'まだ降ってる')
        with Image.open(db.directory('test') / 'output/page-001.png') as im: self.assertEqual(im.size, (1200, 1800))
        self.assertEqual(crop_box((100,200), (100,100), [.5,0]), [0,0,100,100])

    def test_request_character_appearance_and_free_dimensions(self):
        p = db.load('test'); r = novelai.build_request(p, db.panels(p)[0]); params = r['parameters']
        self.assertEqual(params['steps'], 23); self.assertEqual(params['n_samples'], 1)
        self.assertLessEqual(params['width'] * params['height'], 1048576)
        self.assertIn('short black hair', params['v4_prompt']['caption']['char_captions'][0]['char_caption'])
        self.assertIn('worried expression', params['v4_prompt']['caption']['char_captions'][0]['char_caption'])

    def test_non_opus_and_unknown_allowance_block(self):
        for change in ({'active': False}, {'tier': 2}, {'usage': {}}, {'usage': {'percent': 0, 'isNegative': False}}):
            s = copy.deepcopy(self.state); s.update(change)
            with self.assertRaises(db.StudioError): novelai.free_guard(s)

    def test_generate_is_idempotent(self):
        with patch.object(novelai, 'status', return_value=self.state), patch.object(novelai, 'request', return_value=self.png.getvalue()) as send:
            first = novelai.generate('test', 'p1-1', 'one')
            second = novelai.generate('test', 'p1-1', 'one')
        self.assertEqual(first, second); self.assertEqual(send.call_count, 1)
        self.assertEqual(novelai.ledger()['spent'], 0)
        self.assertTrue(db.panel_by_id(db.load('test'), 'p1-1')['image'].endswith('one.png'))

    def test_unknown_send_blocks_following_generation(self):
        with patch.object(novelai, 'status', return_value=self.state), patch.object(novelai, 'request', side_effect=db.StudioError('timeout')) as send:
            with self.assertRaises(db.StudioError): novelai.generate('test', 'p1-1', 'uncertain')
            with self.assertRaisesRegex(db.StudioError, '未確認'): novelai.generate('test', 'p1-1', 'again')
        self.assertEqual(send.call_count, 1)
        self.assertEqual(novelai.ledger()['requests']['uncertain']['status'], 'unknown')

    def test_paid_decrease_halts_and_budget_blocks(self):
        after = copy.deepcopy(self.state); after['balance'] -= 10
        with patch.object(novelai, 'status', side_effect=[self.state,after]), patch.object(novelai, 'request', return_value=self.png.getvalue()):
            novelai.generate('test', 'p1-1', 'unexpected')
        self.assertEqual(novelai.ledger()['spent'], 10)
        with patch.object(novelai, 'request') as send:
            with self.assertRaisesRegex(db.StudioError, '費用台帳が停止中です。使い方ガイドの「困ったとき」'):
                novelai.generate('test', 'p1-2', 'halted')
            send.assert_not_called()
        db.dump(novelai.ledger_path(), {'spent':5000,'requests':{}})
        with patch.object(novelai, 'request') as send:
            with self.assertRaises(db.StudioError): novelai.generate('test', 'p1-2', 'over')
            send.assert_not_called()

    def test_budget_limit_change_and_reset_are_recorded(self):
        db.dump(novelai.ledger_path(), {'spent': 5000, 'requests': {'old': {'status': 'complete', 'actual_anlas': 0}}})
        with patch.object(novelai, 'request') as send:
            with self.assertRaisesRegex(db.StudioError, '上限（5000 Anlas）'): novelai.generate('test', 'p1-2', 'over')
            send.assert_not_called()
        summary = novelai.set_budget(limit=6000)
        self.assertEqual((summary['limit'], summary['spent']), (6000, 5000))
        self.assertEqual([(c['previous'], c['limit']) for c in summary['limit_changes']], [(5000, 6000)])
        summary = novelai.set_budget(reset=True)
        self.assertEqual(summary['spent'], 0)
        self.assertEqual((summary['resets'][-1]['spent_before'], summary['resets'][-1]['limit']), (5000, 6000))
        self.assertIn('T', summary['resets'][-1]['at'])
        self.assertIn('old', novelai.ledger()['requests'])                       # per-request records are kept
        for bad in (0, -1, 1.5, '5000', 2_000_000):
            with self.assertRaises(db.StudioError): novelai.set_budget(limit=bad)
        with self.assertRaises(db.StudioError): novelai.set_budget()
        # A reset never clears the safety stops: an unexpected decrease or an unconfirmed send still blocks sending.
        state = novelai.ledger(); state['halted'] = 'decrease'; db.dump(novelai.ledger_path(), state)
        novelai.set_budget(reset=True)
        with patch.object(novelai, 'request') as send:
            with self.assertRaisesRegex(db.StudioError, '停止中'): novelai.generate('test', 'p1-2', 'halted-after-reset')
            send.assert_not_called()

    def test_edit_during_generation_preserves_edit_and_candidate(self):
        def send(*_):
            db.edit_panel('test', 1, 'p1-1', {'prompt': 'new scene'})
            return self.png.getvalue()
        with patch.object(novelai, 'status', return_value=self.state), patch.object(novelai, 'request', side_effect=send):
            novelai.generate('test', 'p1-1', 'concurrent')
        p = db.panel_by_id(db.load('test'), 'p1-1')
        self.assertEqual(p['prompt'], 'new scene'); self.assertNotIn('image', p); self.assertEqual(len(p['candidates']),1)

    def test_agent_loop_is_bounded(self):
        def transport(*_):
            return {'role':'assistant','tool_calls':[{'id':'a','type':'function','function':{'name':'read_project','arguments':'{}'}}]}, {}
        with self.assertRaisesRegex(db.StudioError, '反復'):
            agent.run('test', '修正してください', panel_id='p1-1', transport=transport)
        self.assertFalse((db.directory('test') / '.agent.lock').exists())

    def test_agent_target_only_and_stop_after_save(self):
        count = 0
        before = copy.deepcopy(db.panel_by_id(db.load('test'), 'p1-2'))
        def transport(*_):
            nonlocal count
            count += 1
            name,args = ('read_project',{}) if count == 1 else ('update_panel',{'patch':{'prompt':'wide shot, wet station'}})
            return {'role':'assistant','tool_calls':[{'id':str(count),'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}, {}
        agent.run('test','雨の駅にしてください',panel_id='p1-1',transport=transport)
        self.assertEqual(count,2); self.assertEqual(db.panel_by_id(db.load('test'),'p1-2'),before)

    def test_http_blocks_cross_origin_and_secret_files(self):
        server = ThreadingHTTPServer(('127.0.0.1',0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        base = f'http://127.0.0.1:{server.server_port}'
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            for suffix, headers, body, code in [
                ('/api/create', {'Content-Type':'application/json'}, b'{}', 403),
                ('/api/create', {'Content-Type':'application/json','X-Studio-Token':TOKEN,'Origin':'https://example.com'}, b'{}',403),
                ('/.env', {}, None,404),
                ('/asset?project=test&path=../../.env', {}, None,400),
                ('/api/projects', {'Host':'evil.example'},None,400),
            ]:
                req = urllib.request.Request(base + suffix, body, headers)
                with self.assertRaises(urllib.error.HTTPError) as err: opener.open(req,timeout=5)
                self.assertEqual(err.exception.code,code)
        finally:
            server.shutdown(); server.server_close(); worker.join()

    def test_whole_prompt_formats_positions_and_text_suffix(self):
        p = db.load('test')
        for kind in wholepage.FORMATS:
            config = wholepage.settings(p,1); config['format']=kind; config['seed']=123
            p=wholepage.save('test',p['revision'],1,config)
            payload=novelai.build_page_request(p,1)
            params=payload['parameters']
            self.assertEqual((params['width'],params['height'],params['steps']), (832,1216,23))
            self.assertTrue(payload['input'].endswith('Text: '+ '\n\n'.join(line for c in self.board['pages'][0]['panels'] for line in c['dialogue'])))
            self.assertNotIn('no text',payload['input'])
            self.assertNotIn('speech bubble',params['negative_prompt'])
            actors=params['v4_prompt']['caption']['char_captions']
            self.assertIn('short black hair',actors[0]['char_caption'])
            self.assertLess(actors[0]['centers'][0]['y'],.3)
            self.assertGreater(actors[-1]['centers'][0]['y'],.64)
        self.assertEqual(db.load('test')['pages'][0]['panels'],self.board['pages'][0]['panels'])

    def test_whole_validation_blocks_before_image_send(self):
        p=db.load('test'); config=wholepage.settings(p,1)
        config['draft']['panels'][0]['dialogue']=['長'*80]*3
        config['draft']['panels'][1]['dialogue']=['長'*80]*3
        for panel in config['draft']['panels']: panel.pop('speakers',None)
        with self.assertRaisesRegex(db.StudioError,'374'): wholepage.save('test',p['revision'],1,config)
        config=wholepage.settings(p,1); config['draft']['panels'][0]['characters'][0]['id']='unknown'
        with self.assertRaisesRegex(db.StudioError,'未登録'): wholepage.save('test',p['revision'],1,config)
        with self.assertRaises(db.StudioError): novelai.build_page_request(p,0)
        with self.assertRaises(db.StudioError): wholepage.set_mode('test',p['revision'],'bad')

    def test_whole_speaker_binding_reaches_matching_actor(self):
        p=db.load('test'); config=wholepage.settings(p,1)
        config['draft']['panels'][0]['speakers']=['mio']
        compiled=wholepage.compile_prompt(p,1,config)
        self.assertTrue(compiled['actors'][0]['char_caption'].endswith('Text: まだ降ってる'))
        config['draft']['panels'][0]['speakers']=['nobody']
        with self.assertRaisesRegex(db.StudioError,'speakers'): wholepage.save('test',p['revision'],1,config)

    def test_whole_generation_switch_and_adopt_preserve_panels(self):
        first=compose('test'); before=(db.directory('test')/'output/page-001.png').read_bytes()
        png=io.BytesIO(); Image.new('RGB',(832,1216),'#123456').save(png,'PNG')
        original=copy.deepcopy(db.load('test')['pages'][0]['panels'])
        with patch.object(novelai,'status',return_value=self.state),patch.object(novelai,'request',return_value=png.getvalue()) as send:
            a=novelai.generate('test',None,'page-one',page_number=1)
            again=novelai.generate('test',None,'page-one',page_number=1)
            self.assertEqual(a,again); self.assertEqual(send.call_count,1)
            with self.assertRaises(db.StudioError): novelai.generate('test','p1-1','page-one')
        p=db.load('test'); wholepage.set_mode('test',p['revision'],'whole')
        m=compose('test'); entry=m['pages'][0]
        self.assertTrue(entry['embedded_text']); self.assertFalse(entry['actual_panel_bounds_known'])
        self.assertEqual(entry['panels'],[])
        self.assertEqual((db.directory('test')/'output/page-001.png').read_bytes(),before)
        p=db.load('test'); self.assertEqual(p['pages'][0]['panels'],original)
        wholepage.adopt('test',p['revision'],1,'page-one')
        p=db.load('test'); wholepage.set_mode('test',p['revision'],'panels')
        m=compose('test'); self.assertEqual(m['pages'][0]['panels'],first['pages'][0]['panels'])

    def test_whole_concurrent_edit_retains_candidate_only(self):
        png=io.BytesIO(); Image.new('RGB',(832,1216),'#234567').save(png,'PNG')
        def send(*_):
            p=db.load('test'); config=wholepage.settings(p,1); config['format']='yaml'
            wholepage.save('test',p['revision'],1,config)
            return png.getvalue()
        with patch.object(novelai,'status',return_value=self.state),patch.object(novelai,'request',side_effect=send):
            novelai.generate('test',None,'page-edit',page_number=1)
        target=db.load('test')['pages'][0]['whole']
        self.assertEqual(target['format'],'yaml'); self.assertNotIn('image',target)
        self.assertEqual(len(target['candidates']),1)

    def continuation_board(self):
        board = copy.deepcopy(self.board)
        board['locations'] = [dict(id='cafe', name='喫茶店', description='sage green door, walnut counter')]
        board['props'] = [dict(id='bell', name='鈴', description='small round brass bell')]
        board['pages'] *= 4
        for panel in db.panels(board): panel.update(location_id='cafe', prop_ids=['bell'])
        board['handoff'] = 'カウンターに鈴を置いた。修理は次章。'
        return board

    def test_twenty_four_pages_keep_images_and_fixed_catalogs(self):
        db.create('long', '長編', '共通設定\n## 前半\n原作A\n## 後半\n原作B')
        board = self.continuation_board()
        first = db.append_board('long', 0, board, 0, 4)
        first = db.mutate('long', first['revision'], lambda p: p['pages'][0]['panels'][0].update(image='images/kept.png', candidates=[{'id':'keep'}]))
        old_pages = copy.deepcopy(first['pages'])
        for batch in range(5):
            latest = db.append_board('long', db.load('long')['revision'], board, 1, 4)
        self.assertEqual(len(latest['pages']), 24)
        self.assertEqual(latest['pages'][:4], old_pages)
        self.assertEqual(len({c['id'] for c in db.panels(latest)}), 72)
        context = db.continuity_context(latest)
        self.assertEqual(context['next_page'], 25)
        self.assertEqual(len(context['recent_handoffs']), 3)
        self.assertNotIn('image', context['previous_page'][0])
        for panel in (db.panels(latest)[0], db.panels(latest)[-1]):
            payload = novelai.build_request(latest, panel)
            self.assertIn('sage green door', payload['input'])
            self.assertIn('small round brass bell', payload['input'])
            self.assertIn('short black hair', json.dumps(payload))
        before = db.load('long')
        with self.assertRaisesRegex(db.StudioError, '24ページ'): db.append_board('long', before['revision'], board)
        self.assertEqual(db.load('long'), before)
        self.assertEqual(db.generation_targets(latest, start_page=21, end_page=24)[-1], 'p24-3')
        with self.assertRaisesRegex(db.StudioError, '4ページ'): db.generation_targets(latest, start_page=1, end_page=5)

    def test_append_adds_only_new_outfits_to_existing_character(self):
        db.create('long', '長編', '原作')
        board = self.continuation_board()
        board['characters'][0]['outfits'] = [dict(id='school', name='制服', prompt='uniform', prompt_ja='制服')]
        board['characters'][0]['default_outfit_id'] = 'school'
        first = db.append_board('long', 0, board, expected_pages=4)
        cid = board['characters'][0]['id']
        more = dict(pages=board['pages'][:1], handoff='x', characters=[dict(id=cid, appearance='changed', outfits=[
            dict(id='school', name='X', prompt='changed', prompt_ja='x'), dict(id='yukata', name='浴衣', prompt='yukata', prompt_ja='浴衣')])])
        for panel in db.panels(more): panel.update(location_id='cafe', prop_ids=['bell'])
        latest = db.append_board('long', first['revision'], more, 0, 1)
        person = next(c for c in latest['characters'] if c['id'] == cid)
        self.assertEqual([o['id'] for o in person['outfits']], ['school', 'yukata'])
        self.assertEqual(person['outfits'][0]['prompt'], 'uniform')
        self.assertEqual(person['appearance'], first['characters'][0]['appearance'])
        self.assertEqual(latest['batches'][-1]['ignored_catalog'], [f'characters/{cid}'])

    def test_successful_ai_saves_clear_only_the_request_they_used(self):
        # The remembered request boxes are one-shot: a saved batch / panel edit empties its own box and nothing else.
        def remember(p): p['ui_inputs'] = {'agent-instruction': '雨の駅にして', 'board-instruction': '4ページで', 'rewrite-instruction': '口調を直す'}
        db.mutate('test', db.load('test')['revision'], remember)
        calls = iter([('read_project', {}), ('update_panel', {'patch': {'prompt': 'wide shot, wet station'}})])
        def transport(*_):
            name, args = next(calls)
            return {'role':'assistant','tool_calls':[{'id':name,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}, {}
        agent.run('test', '雨の駅にしてください', panel_id='p1-1', transport=transport)
        self.assertEqual(db.load('test')['ui_inputs'], {'board-instruction': '4ページで', 'rewrite-instruction': '口調を直す'})
        db.create('long', '長編', '原作')
        db.mutate('long', 0, remember)
        latest = db.append_board('long', 1, self.continuation_board(), expected_pages=4)
        self.assertEqual(latest['ui_inputs'], {'agent-instruction': '雨の駅にして', 'rewrite-instruction': '口調を直す'})
        with self.assertRaises(db.StudioError): db.append_board('long', latest['revision'], self.continuation_board(), expected_pages=2)

    def test_append_rejects_changed_bible_and_wrong_page_count(self):
        db.create('long', '長編', '原作')
        board = self.continuation_board()
        first = db.append_board('long', 0, board, expected_pages=4)
        # Re-outputting an existing catalog entry with different text is ignored (never overwritten) and noted.
        for key, field in [('characters','appearance'), ('locations','description'), ('props','description')]:
            changed = copy.deepcopy(board); changed[key][0][field] = 'changed'
            changed['pages'] = changed['pages'][:1]
            latest = db.append_board('long', db.load('long')['revision'], changed, 0, 1)
            self.assertEqual(latest[key][0][field], first[key][0][field])
            self.assertIn(f"{key}/{changed[key][0]['id']}", latest['batches'][-1]['ignored_catalog'])
        first = db.load('long')
        changed = copy.deepcopy(board); changed['style'] = 'different'
        with self.assertRaisesRegex(db.StudioError, 'style'): db.append_board('long', first['revision'], changed)
        with self.assertRaisesRegex(db.StudioError, '4ページ受信.*ちょうど2ページ'): db.append_board('long', first['revision'], board, expected_pages=2)
        self.assertEqual(db.load('long'), first)

    def test_agent_selected_chapter_then_panel_auto_chapter(self):
        db.create('chapters', '章', '共通設定\n## 第一章\n秘密A\n## 第二章\n秘密B')
        board = self.continuation_board()
        db.append_board('chapters', 0, board, 0, 4)
        calls = [('read_source',{}), ('read_project',{}), ('save_storyboard',board)]
        received = []
        def transport(messages, *_):
            received.append(copy.deepcopy(messages))
            name, args = calls.pop(0)
            return {'role':'assistant','tool_calls':[{'id':str(len(calls)), 'type':'function', 'function':{'name':name,'arguments':json.dumps(args)}}]}, {}
        agent.run('chapters', '続き', page_count=4, append=True, source_section=1, transport=transport)
        text = received[-1][-3]['content']
        self.assertIn('秘密B', text); self.assertNotIn('秘密A', text); self.assertIn('共通設定', text)
        calls.extend([('read_source',{}), ('read_project',{}), ('update_panel', {'patch': {'scene':'後半を修正'}})])
        agent.run('chapters', '修正', panel_id='p8-1', transport=transport)
        text = received[-1][-3]['content']
        self.assertIn('秘密B', text); self.assertNotIn('秘密A', text)
        self.assertEqual(len(db.load('chapters')['pages']),8)

if __name__ == '__main__': unittest.main()
