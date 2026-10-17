import unittest
from studio import agent, project as db


class CropTidyTests(unittest.TestCase):
    def tidy(self, crop):
        panel = {'crop': crop}
        agent.tidy_crop(panel)
        return panel.get('crop', 'dropped')

    def test_valid_and_out_of_range_pairs(self):
        self.assertEqual(self.tidy([0.3, 0.7]), [0.3, 0.7])
        self.assertEqual(self.tidy([1.4, -0.2]), [1.0, 0.0])          # clamped, not a reason to stop the run
        self.assertEqual(self.tidy([1, 0]), [1.0, 0.0])

    def test_a_box_uses_its_centre_and_other_shapes_are_dropped(self):
        self.assertEqual(self.tidy([0.2, 0.2, 0.4, 0.6]), [0.4, 0.5])
        for bad in ([0.5], [0.1, 0.2, 0.3], 'center', None, [0.5, 'x'], {}, [True, False]):
            self.assertEqual(self.tidy(bad), 'dropped', bad)

    def test_panels_without_crop_and_whole_boards(self):
        board = {'pages': [{'panels': [{'id': 'a'}, {'id': 'b', 'crop': [0.5, 0.5, 0.2, 0.2]}, {'id': 'c', 'crop': 'bad'}]}]}
        agent.tidy_board(board)
        panels = board['pages'][0]['panels']
        self.assertNotIn('crop', panels[0])
        self.assertEqual(panels[1]['crop'], [0.6, 0.6])
        self.assertNotIn('crop', panels[2])
        agent.tidy_board({}); agent.tidy_board(None); agent.tidy_crop(None)    # tolerant of odd input

    def test_schema_now_demands_two_numbers_in_range(self):
        schema = agent.CROP_SCHEMA
        self.assertEqual((schema['minItems'], schema['maxItems']), (2, 2))
        self.assertEqual((schema['items']['minimum'], schema['items']['maximum']), (0, 1))
        panel = agent.board_schema()['properties']['pages']['items']['properties']['panels']['items']['properties']
        self.assertIs(panel['crop'], schema)

    def test_manual_validation_error_says_what_it_received(self):
        panel = dict(id='p', scene='s', prompt='x', prompt_ja='x', negative='', negative_ja='', location_id='', characters=[], dialogue=[], crop=[0.1, 0.2, 0.3])
        with self.assertRaisesRegex(db.StudioError, r'受け取った値: \[0\.1, 0\.2, 0\.3\]'):
            db.validate_panel(panel, set(), True)

    def test_schema_fixes_page_count_when_given(self):
        pages = agent.board_schema(4)['properties']['pages']
        self.assertEqual((pages['minItems'], pages['maxItems']), (4, 4))
        self.assertNotIn('minItems', agent.board_schema()['properties']['pages'])

    def test_user_request_states_fixed_part_even_when_instruction_blank(self):
        text = agent.user_request('', 4, '第二章')
        self.assertIn('正確に4ページ', text); self.assertIn('第二章', text); self.assertNotIn('構成の希望', text)
        self.assertIn('構成の希望: 夜景多め', agent.user_request(' 夜景多め ', 4, '第二章'))
        self.assertEqual(agent.user_request('台詞を直す', 4, 'x', panel_id='p1-1'), '台詞を直す')

    def test_existing_catalog_entries_need_no_translation_but_new_outfits_do(self):
        project = dict(characters=[dict(id='a')], locations=[dict(id='room')], props=[])
        board = dict(characters=[dict(id='a', outfits=[dict(id='yukata', name='浴衣', prompt='yukata', prompt_ja='浴衣')])],
                     locations=[dict(id='room')], pages=[])
        agent.bilingual_board(board, project)
        board['characters'][0]['outfits'][0]['prompt_ja'] = ''
        with self.assertRaisesRegex(db.StudioError, 'outfits/0/prompt_ja'): agent.bilingual_board(board, project)
        with self.assertRaisesRegex(db.StudioError, 'characters/0/appearance_ja'): agent.bilingual_board(board)

    def test_speaker_error_names_panel_and_mismatch(self):
        panel = dict(id='p12-3', scene='s', prompt='x', prompt_ja='x', location_id='', characters=[dict(id='a', prompt='x')], dialogue=['1', '2'], speakers=['a', 'b'])
        with self.assertRaisesRegex(db.StudioError, r"p12-3: .*台詞2個.*\['a', 'b'\].*\['a'\]"):
            db.validate_panel(panel, {'a', 'b'}, True)

    def test_offscreen_or_miscounted_speakers_are_repaired_and_noted(self):
        panel = dict(id='p1-5', dialogue=['a', 'b'], speakers=['mio', 'ren', 'x'], characters=[dict(id='ren')])
        board = dict(pages=[dict(panels=[panel, dict(id='p1-6', dialogue=['c'], speakers=[], characters=[])])])
        notes = agent.tidy_board(board)
        self.assertEqual(panel['speakers'], ['', 'ren'])
        self.assertEqual(board['pages'][0]['panels'][1]['speakers'], [''])
        self.assertEqual(len(notes), 2); self.assertIn('p1-5', notes[0])
        self.assertEqual(agent.tidy_board(board), [])

    def test_panel_translation_can_be_deferred_but_catalog_cannot(self):
        panel = dict(prompt='x', prompt_ja='', negative='n', negative_ja='', characters=[dict(id='a', prompt='p', prompt_ja='')])
        board = dict(pages=[dict(panels=[panel])])
        self.assertEqual(agent.bilingual_board(board, allow_untranslated_panels=True), 3)
        with self.assertRaisesRegex(db.StudioError, 'prompt_ja'): agent.bilingual_board(board)
        board['characters'] = [dict(id='b', outfits=[dict(prompt='x', prompt_ja='x')])]
        with self.assertRaisesRegex(db.StudioError, 'characters/0/appearance_ja'): agent.bilingual_board(board, allow_untranslated_panels=True)

    def test_unwrap_result_peels_only_an_undefined_lone_key(self):
        from studio import strata_tasks
        schema = agent.object_schema({'warnings': {'type': 'array'}}, ('warnings',))
        self.assertEqual(strata_tasks.unwrap_result({'result': {'warnings': []}}, schema), {'warnings': []})
        self.assertEqual(strata_tasks.unwrap_result({'result': {'output': {'warnings': []}}}, schema), {'warnings': []})
        self.assertEqual(strata_tasks.unwrap_result({'warnings': []}, schema), {'warnings': []})
        self.assertEqual(strata_tasks.unwrap_result({'result': [1]}, schema), {'result': [1]})


if __name__ == '__main__':
    unittest.main()
