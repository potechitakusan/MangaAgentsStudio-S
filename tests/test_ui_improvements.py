import copy
import json
from pathlib import Path
import shutil
import uuid
import unittest
from unittest.mock import patch
from PIL import Image, ImageDraw
from studio import project as db, library, rendering, workspace, exporting, wholepage

ROOT = db.ROOT


class UIImprovementTests(unittest.TestCase):
    def setUp(self):
        self.root = (ROOT / '.runtime' / ('ui-improvements-' + uuid.uuid4().hex)).resolve()
        self.root.mkdir()
        self.work_patch = patch.object(db, 'WORK', self.root / 'work'); self.work_patch.start()
        self.addCleanup(self.work_patch.stop)
        self.addCleanup(self.cleanup)
        db.create('sample', 'テスト作品', '原作')
        panel = dict(id='p1', scene='色見本', prompt='colored stripes', characters=[], dialogue=[], crop=[.5, .5])
        board = dict(output_method='novelai', title='テスト作品', style='illustration', characters=[], pages=[
            dict(template='splash', panels=[panel]), dict(template='splash', panels=[dict(panel, id='p2')])])
        p = db.import_board('sample', 0, board)
        folder = db.directory('sample') / 'images'; folder.mkdir()
        im = Image.new('RGB', (400, 200), 'red'); ImageDraw.Draw(im).rectangle((200, 0, 399, 199), fill='blue'); im.save(folder / 'stripes.png')
        self.project = db.mutate('sample', p['revision'], lambda p: [c.update(image='images/stripes.png') for c in db.panels(p)])

    def cleanup(self):
        assert self.root.parent == (ROOT / '.runtime').resolve() and self.root.name.startswith('ui-improvements-')
        shutil.rmtree(self.root)

    def test_exclusion_is_confirmed_reversible_and_preserves_data(self):
        before = (db.directory('sample') / 'project.json').read_bytes()
        with self.assertRaises(db.StudioError): library.set_visibility('sample', True)
        with self.assertRaises(db.StudioError): library.set_visibility('sample', True, 1)
        library.set_visibility('sample', True, True)
        self.assertEqual(library.visible(), [])
        self.assertTrue(library.catalog()[0]['excluded'])
        self.assertEqual(len(db.list_projects()), 1)  # backup sees excluded works
        library.set_visibility('sample', False)
        self.assertEqual(len(library.visible()), 1)
        self.assertEqual(before, (db.directory('sample') / 'project.json').read_bytes())

    def external(self):
        folder = self.root / 'external'
        shutil.copytree(db.directory('sample'), folder)
        p = db.read_json(folder / 'project.json'); p['id'] = 'imported'; db.dump(folder / 'project.json', p)
        return folder

    def test_import_copies_history_and_images_but_skips_hidden_and_unknown_files(self):
        folder = self.external()
        (folder / '.env').write_text('DO-NOT-COPY', encoding='utf-8')
        (folder / 'auth.json').write_text('DO-NOT-COPY', encoding='utf-8')
        (folder / 'run.cmd').write_text('DO-NOT-COPY', encoding='utf-8')
        before = (folder / 'project.json').read_bytes()
        result = library.import_folder(str(folder))
        self.assertTrue(result['copied'])
        target = db.directory('imported')
        self.assertEqual(before, (target / 'project.json').read_bytes())
        self.assertEqual(before, (folder / 'project.json').read_bytes())
        self.assertEqual((folder / 'images/stripes.png').read_bytes(), (target / 'images/stripes.png').read_bytes())
        self.assertTrue(list((target / 'history').glob('*.json')))
        for name in ['.env', 'auth.json', 'run.cmd']: self.assertFalse((target / name).exists())
        with self.assertRaisesRegex(db.StudioError, '同じID'): library.import_folder(str(folder))
        library.set_visibility('imported', True, True)
        self.assertFalse(library.import_folder(str(target))['copied'])
        self.assertEqual(len(library.visible()), 2)

    def test_import_rejects_missing_assets_traversal_and_locks(self):
        folder = self.external()
        (folder / '.project.lock').write_text('in progress', encoding='utf-8')
        with self.assertRaisesRegex(db.StudioError, 'ロック'): library.import_folder(str(folder))
        (folder / '.project.lock').unlink()
        p = db.read_json(folder / 'project.json')
        for path in ('images/missing.png', 'images/../../outside.png', 'C:/outside.png'):
            p['pages'][0]['panels'][0]['image'] = path; db.dump(folder / 'project.json', p)
            with self.assertRaises(db.StudioError): library.import_folder(str(folder))
            self.assertFalse(db.directory('imported').exists())
        self.assertEqual(list(db.WORK.glob('_import-*')), [])

    def test_partial_copies_and_malformed_folders_do_not_break_library(self):
        db.dump(db.WORK / '_import-pending/project.json', self.project)
        bad = db.WORK / 'broken'; bad.mkdir(); (bad / 'project.json').write_text('{oops', encoding='utf-8')
        self.assertEqual([p['id'] for p in library.visible()], ['sample'])

    def test_import_empty_work_requires_a_valid_catalog(self):
        folder = self.external(); p = db.read_json(folder / 'project.json')
        p['pages'] = []; p['characters'] = 'broken'; db.dump(folder / 'project.json', p)
        with self.assertRaises(db.StudioError): library.import_folder(str(folder))
        self.assertFalse(db.directory('imported').exists())
        p['characters'] = []; db.dump(folder / 'project.json', p)
        self.assertTrue(library.import_folder(str(folder))['copied'])

    def test_slanted_panel_clips_transformed_art(self):
        p = copy.deepcopy(self.project)
        panel = copy.deepcopy(p['pages'][0]['panels'][0])
        panel['image_transform'] = dict(x=0, y=0, scale=1)
        p['pages'][0] = dict(template='P3-21-lean-right', panels=[dict(panel,id=f'c{i}') for i in range(3)])
        layers, _ = rendering.page_layers(p, 1)
        art = next(im for name, im, offset in layers if name == 'c0 コマ画像')
        self.assertEqual(art.getpixel((5, art.height-5))[3], 0)  # outside the leaning left edge
        self.assertEqual(art.getpixel((art.width//2, art.height//2))[3], 255)

    def test_placement_gaps_translation_clipping_and_original_preservation(self):
        source = (db.directory('sample') / 'images/stripes.png').read_bytes()
        p = self.project
        p['pages'][0]['panels'][0]['image_transform'] = dict(x=0, y=0, scale=.2)
        layers, record = rendering.page_layers(p, 1)
        canvas = rendering.flatten(layers)
        self.assertEqual(canvas.getpixel((600, 200)), (255, 255, 255))
        self.assertEqual(canvas.getpixel((450, 900)), (255, 0, 0))
        self.assertEqual(canvas.getpixel((750, 900)), (0, 0, 255))
        p['pages'][0]['panels'][0]['image_transform']['x'] = .25
        moved = rendering.flatten(rendering.page_layers(p, 1)[0])
        self.assertEqual(moved.getpixel((450, 900)), (255, 255, 255))
        self.assertEqual(moved.getpixel((600, 900)), (255, 0, 0))
        self.assertEqual(moved.getpixel((1190, 900)), (255, 255, 255))
        self.assertEqual(source, (db.directory('sample') / 'images/stripes.png').read_bytes())
        self.assertEqual(record['panels'][0]['image_transform']['scale'], .2)

    def test_transform_validation_revision_and_settings_roundtrip(self):
        revision = self.project['revision']
        for value in (None, {}, dict(x=0,y=0,scale=0), dict(x=3,y=0,scale=1),
                      dict(x=False,y=0,scale=1), dict(x=float('nan'),y=0,scale=1), dict(x=0,y=0,scale=float('inf'))):
            with self.assertRaises(db.StudioError): db.edit_panel('sample', revision, 'p1', {'image_transform': value})
        transform = dict(x=.25, y=-.1, scale=.5)
        saved = db.edit_panel('sample', revision, 'p1', {'image_transform': transform})
        with self.assertRaisesRegex(db.StudioError, '競合'): db.edit_panel('sample', revision, 'p1', {'image_transform': dict(x=0,y=0,scale=1)})
        settings = workspace.config_export('sample', 'all')
        self.assertEqual(settings['inputs']['pages'][0]['panels'][0]['image_transform'], transform)
        saved = workspace.config_import('sample', saved['revision'], settings)
        self.assertEqual(saved['pages'][0]['panels'][0]['image_transform'], transform)
        self.assertNotIn('image_transform', wholepage.from_panels(saved, 1)['panels'][0])

    def test_compose_selected_page_preserves_other_png_and_psd_uses_same_layers(self):
        rendering.compose('sample')
        other = db.directory('sample') / 'output/page-002.png'; before = other.stat().st_mtime_ns
        saved = db.edit_panel('sample', self.project['revision'], 'p1', {'image_transform': dict(x=.15,y=0,scale=.2)})
        result = rendering.compose('sample', page_number=1)
        self.assertEqual(before, other.stat().st_mtime_ns)
        self.assertEqual(result['pages'][0]['revision'], saved['revision'])
        self.assertNotEqual(result['pages'][1]['revision'], saved['revision'])
        layers, _ = rendering.page_layers(saved, 1)
        out = self.root / 'placement.psd'
        info = exporting.write_psd(out, layers, saved, 1)
        self.assertLessEqual(info['composite_max_difference'], 1)
        self.assertFalse(info['application_readback'])


if __name__ == '__main__': unittest.main()
