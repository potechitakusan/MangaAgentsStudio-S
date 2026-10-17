"""Page plan (studio/pageplan.py) and the storyboard runs that follow it. No AI is called: transports are stand-ins."""
import json
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from studio import project as db, workspace, pageplan, agent

ROOT = Path(__file__).resolve().parents[1]
SOURCE = '人物: 澪と蓮。\n\n## 第一章 雨\n澪「傘、ある？」\n蓮「ないよ」\n\n## 第二章 晴れ\n澪「虹だ」\n'

def reply(name, args):
    return {'role': 'assistant', 'tool_calls': [{'id': name, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args, ensure_ascii=False)}}]}, {}

class PagePlanTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / '.runtime' / ('test-' + uuid.uuid4().hex)
        self.root.mkdir(parents=True)
        self.patch = patch.object(db, 'WORK', self.root / 'work'); self.patch.start()
        p = db.create('w', 'テスト', SOURCE)
        workspace.save('w', p['revision'], dict(system=dict(workspace.EMPTY_SYSTEM), system_layered=True, source=SOURCE))

    def tearDown(self):
        self.patch.stop()
        target = self.root.resolve()
        assert target.is_relative_to((ROOT / '.runtime').resolve()) and target.name.startswith('test-')
        shutil.rmtree(target)

    def plan_rows(self):
        return [dict(page=1, section=0, panels=3, summary='雨の駅で澪が傘を尋ねる', lines=['傘、ある？']),
                dict(page=2, section=0, panels=4, summary='蓮は傘がない', lines=['ないよ']),
                dict(page=3, section=1, panels=3, summary='晴れて虹が出る', lines=['虹だ'])]

    def test_make_numbers_pages_and_validates(self):
        seen = {}
        def transport(messages, tools, timeout):
            content = json.loads(messages[1]['content']); seen.update(content)
            rows = [dict(r, page=99) for r in self.plan_rows()]          # the host renumbers
            return reply('submit_result', dict(pages=rows))
        p = pageplan.make('w', db.load('w')['revision'], 0, 3, transport=transport)
        self.assertEqual([r['page'] for r in p['page_plan']['pages']], [1, 2, 3])
        self.assertEqual(seen['common_settings'], '人物: 澪と蓮。')               # the shared preface is sent once, not per chapter
        self.assertTrue(all(not s['text'].startswith('人物') for s in seen['sections']))
        self.assertEqual(seen['target_pages'], 3)
        # a reply with the wrong page count is sent back once, then the run stops
        bad = lambda *_: reply('submit_result', dict(pages=self.plan_rows()[:2]))
        with self.assertRaisesRegex(db.StudioError, '2件'): pageplan.make('w', db.load('w')['revision'], 0, 3, transport=bad)

    def test_clean_rejects_gaps_and_backward_chapters(self):
        p = db.load('w')
        rows = self.plan_rows()
        with self.assertRaisesRegex(db.StudioError, '連番'): pageplan.clean(dict(pages=[rows[0], rows[2]]), p)
        backwards = [dict(rows[2], page=1), dict(rows[0], page=2)]
        with self.assertRaisesRegex(db.StudioError, '戻って'): pageplan.clean(dict(pages=backwards), p)
        with self.assertRaisesRegex(db.StudioError, '章番号'): pageplan.clean(dict(pages=[dict(rows[0], section=5)]), p)
        # edited on screen and saved with the other inputs
        saved = workspace.save('w', p['revision'], dict(page_plan=dict(pages=rows)))
        self.assertEqual(len(saved['page_plan']['pages']), 3)
        self.assertEqual(workspace.fingerprint(saved), workspace.fingerprint({**saved, 'page_plan': None}))   # review inputs do not include the plan

    def test_storyboard_gets_only_its_rows_and_no_panel_japanese(self):
        p = workspace.save('w', db.load('w')['revision'], dict(page_plan=dict(pages=self.plan_rows())))
        seen = []
        board = dict(characters=[dict(id='mio', name='澪', appearance='girl', appearance_ja='少女', voice='casual', voice_ja='普通体',
                                      outfits=[dict(id='u', name='制服', prompt='uniform', prompt_ja='制服')], default_outfit_id='u')],
                     locations=[], props=[], handoff='駅で別れた',
                     pages=[dict(template='three', panels=[dict(scene='駅', prompt='station', location_id='', characters=[dict(id='mio', prompt='asking', outfit_id='u', expression='custom', x=.5, y=.5)], dialogue=['傘、ある？'], speakers=['mio'])] * 3)])
        def transport(messages, tools, timeout):
            seen.append(messages)
            n = len(seen)
            if n == 1: return reply('read_source', {})
            if n == 2: return reply('read_project', {})
            return reply('save_storyboard', board)
        result = agent.run('w', '', page_count=1, source_section=1, transport=transport)   # the chapter choice gives way to the plan
        user = seen[0][1]['content']
        self.assertIn('PAGE 1（3コマ前後）: 雨の駅で澪が傘を尋ねる', user)
        self.assertNotIn('PAGE 2', user)
        outputs = [json.loads(m['content']) for m in seen[-1] if m['role'] == 'tool']
        source = next(o['source'] for o in outputs if 'source' in o)
        self.assertIn('傘、ある？', source); self.assertNotIn('虹だ', source)
        self.assertEqual(result['batches'][-1]['source_section'], 0)
        schema = agent.board_schema(1, panel_ja=False)
        panel = schema['properties']['pages']['items']['properties']['panels']['items']
        self.assertNotIn('prompt_ja', panel['properties']); self.assertNotIn('prompt_ja', panel['required'])
        self.assertNotIn('prompt_ja', panel['properties']['characters']['items']['properties'])
        self.assertIn('prompt_ja', agent.board_schema(1)['properties']['pages']['items']['properties']['panels']['items']['required'])

    def test_next_chunk_stays_in_one_chapter(self):
        p = db.load('w'); p['page_plan'] = dict(pages=self.plan_rows())
        self.assertEqual([r['page'] for r in pageplan.next_chunk(p, 2)], [1, 2])
        self.assertEqual([r['page'] for r in pageplan.next_chunk(p, 4)], [1, 2])     # page 3 is the next chapter
        p['pages'] = [dict(panels=[])] * 2
        self.assertEqual([r['page'] for r in pageplan.next_chunk(p, 2)], [3])
        self.assertEqual(pageplan.rows_for(p, 3, 2), [])                              # the plan does not cover page 4

    def test_fill_ja_fills_empty_fields_only(self):
        board = dict(title='t', style='s', characters=[dict(id='mio', name='澪', appearance='girl')], locations=[], props=[],
                     pages=[dict(template='two', panels=[dict(id='p1-1', scene='a', prompt='rain', characters=[dict(id='mio', prompt='smiling', x=.5, y=.5)], dialogue=[]),
                                                          dict(id='p1-2', scene='b', prompt='sun', prompt_ja='晴れ（手入力）', negative='text', characters=[], dialogue=[])])])
        db.import_board('w', db.load('w')['revision'], board)
        def transport(messages, tools, timeout):
            items = json.loads(messages[1]['content'])['items']
            return reply('submit_result', dict(items=[dict(id=x['id'], text='訳:' + x['text']) for x in items]))
        self.assertEqual(len(pageplan.missing_ja(db.load('w'))), 3)
        self.assertEqual(pageplan.fill_ja('w', transport=transport), 3)
        panels = db.load('w')['pages'][0]['panels']
        self.assertEqual(panels[0]['prompt_ja'], '訳:rain'); self.assertEqual(panels[0]['characters'][0]['prompt_ja'], '訳:smiling')
        self.assertEqual(panels[1]['prompt_ja'], '晴れ（手入力）'); self.assertEqual(panels[1]['negative_ja'], '訳:text')
        self.assertEqual(pageplan.missing_ja(db.load('w')), [])

if __name__ == '__main__':
    unittest.main()
