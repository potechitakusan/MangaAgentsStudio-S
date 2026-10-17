import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from studio import backup, novelai, project as db


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='manga-backup-test-'))
        self.patches = [patch.object(db, 'WORK', self.tmp / 'work'),
                        patch.object(novelai, 'ledger_path', lambda: self.tmp / 'ledger.json')]
        for p in self.patches: p.start()
        db.WORK.mkdir()
        self.project = db.create('demo', 'デモ', '原作')
        folder = db.directory('demo')
        (folder / 'images' / 'p1-1').mkdir(parents=True)
        (folder / 'images' / 'p1-1' / 'a.png').write_bytes(b'\x89PNGxx')
        (folder / 'images' / 'p1-1' / 'a.request.json').write_text('{"seed":1}', encoding='utf-8')
        (folder / 'output').mkdir()
        (folder / 'output' / 'page-001.png').write_bytes(b'\x89PNGout')
        (folder / 'exports').mkdir()
        (folder / 'exports' / 'x.zip').write_bytes(b'PK')
        (folder / 'history' ).mkdir(exist_ok=True)
        (folder / 'something.tmp').write_text('x')

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def names(self, info):
        with zipfile.ZipFile(backup.backups_root() / 'demo' / info['name']) as z:
            return set(z.namelist()), json.loads(z.read('backup-manifest.json'))

    def test_light_backup_has_text_but_no_images_outputs_or_temp(self):
        info = backup.create('demo')
        names, manifest = self.names(info)
        self.assertIn('demo/project.json', names)
        self.assertIn('demo/images/p1-1/a.request.json', names)
        self.assertFalse([n for n in names if n.endswith(('.png', '.zip', '.tmp'))])
        self.assertEqual(manifest['revision'], 0)
        self.assertFalse(manifest['full'])
        self.assertIn('-r0-manual-light.zip', info['name'])

    def test_full_backup_adds_images_but_not_regenerable_outputs(self):
        info = backup.create('demo', full=True)
        names, manifest = self.names(info)
        self.assertIn('demo/images/p1-1/a.png', names)
        self.assertFalse([n for n in names if n.startswith(('demo/output/', 'demo/exports/'))])
        self.assertTrue(manifest['full'])

    def test_no_partial_file_left_and_bad_ids_rejected(self):
        backup.create('demo')
        self.assertFalse(list((backup.backups_root() / 'demo').glob('*.part')))
        for bad in ('../x', 'a/b', '', '.hidden', '_trash'):
            with self.assertRaises(db.StudioError):
                backup.create(bad)
        with self.assertRaises(db.StudioError):
            backup.create('missing')

    def test_auto_backup_skips_unchanged_revision_and_prunes(self):
        self.assertEqual(backup.auto_backup_all(), ['demo'])
        self.assertEqual(backup.auto_backup_all(), [])  # revision unchanged
        db.mutate('demo', 0, lambda p: p.update(title='変更'))
        self.assertEqual(backup.auto_backup_all(), ['demo'])
        self.assertEqual([b['revision'] for b in backup.list_backups('demo')], [1, 0])

    def test_auto_prune_keeps_manual_backups(self):
        manual = backup.create('demo')
        folder = backup.backups_root() / 'demo'
        for i in range(backup.AUTO_KEEP + 3):
            (folder / f'demo-20260101-0000{i:02}-r{i}-auto-light.zip').write_bytes(b'PK')
        backup._prune('demo')
        items = backup.list_backups('demo')
        self.assertEqual(len([b for b in items if b['kind'] == 'auto']), backup.AUTO_KEEP)
        self.assertIn(manual['name'], [b['name'] for b in items])

    def test_soft_delete_moves_folder_and_keeps_everything(self):
        with self.assertRaises(db.StudioError):
            backup.soft_delete('demo', 'wrong')
        self.assertTrue((db.directory('demo') / 'project.json').exists())
        result = backup.soft_delete('demo', 'demo')
        self.assertFalse(db.directory('demo').exists())
        moved = backup.trash_root() / result['trash']
        self.assertTrue((moved / 'project.json').exists())
        self.assertTrue((moved / 'images' / 'p1-1' / 'a.png').exists())
        self.assertEqual(json.loads((moved / '_deleted.json').read_text(encoding='utf-8'))['project_id'], 'demo')
        self.assertEqual(db.list_projects(), [])  # Trash and backups never show up as works.

    def test_soft_delete_refuses_busy_unknown_ledger_and_locked(self):
        with self.assertRaises(db.StudioError):
            backup.soft_delete('demo', 'demo', busy=True)
        (self.tmp / 'ledger.json').write_text(json.dumps(dict(spent=0, requests={'r1': dict(project_id='demo', status='unknown')})), encoding='utf-8')
        with self.assertRaises(db.StudioError):
            backup.soft_delete('demo', 'demo')
        (self.tmp / 'ledger.json').write_text(json.dumps(dict(spent=0, requests={'r2': dict(project_id='other', status='unknown')})), encoding='utf-8')
        (db.directory('demo') / '.project.lock').write_text('x')
        with self.assertRaises(db.StudioError):
            backup.soft_delete('demo', 'demo')
        self.assertTrue((db.directory('demo') / 'project.json').exists())

    def test_open_folder_only_accepts_fixed_kinds(self):
        for kind, pid in (('anything', None), ('project', '../x'), ('backups', '../x')):
            with self.assertRaises(db.StudioError):
                backup.open_folder(kind, pid)
        with patch.object(backup.os, 'startfile', create=True) as opened, patch.object(backup.sys, 'platform', 'win32'):
            path = backup.open_folder('trash')
        opened.assert_called_once()
        self.assertEqual(Path(path), backup.trash_root())

    def test_storage_reports_size_and_folders(self):
        backup.create('demo')
        info = backup.storage('demo')
        self.assertGreater(info['bytes'], 0)
        self.assertEqual(len(info['backups']), 1)


if __name__ == '__main__':
    unittest.main()
