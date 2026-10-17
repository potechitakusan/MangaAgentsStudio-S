import copy
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from studio import project as db, workspace


class SystemLayerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='manga-system-test-'))
        self.current = copy.deepcopy(workspace.DEFAULT_SYSTEM)   # stands in for .runtime/system-settings.json
        self.patches = [patch.object(db, 'WORK', self.tmp / 'work'),
                        patch.object(workspace, 'system_defaults', lambda: copy.deepcopy(self.current))]
        for p in self.patches: p.start()
        db.WORK.mkdir()

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def general(self, **values):
        self.current = dict(copy.deepcopy(workspace.DEFAULT_SYSTEM), **values)
        return self.current

    def test_layered_work_joins_general_then_own(self):
        self.general(storyboard='GENERAL-SB')
        work = dict(workspace.EMPTY_SYSTEM, storyboard='OWN-SB', review='OWN-REVIEW')
        effective = workspace.effective_system(dict(system=work, system_layered=True))
        self.assertEqual(effective['storyboard'], 'GENERAL-SB\n\nOWN-SB')
        self.assertTrue(effective['review'].endswith('\n\nOWN-REVIEW'))
        self.assertEqual(effective['translation'], workspace.DEFAULT_SYSTEM['translation'])  # own part empty: no stray blank lines
        self.assertEqual(effective['constraints'], workspace.DEFAULT_SYSTEM['constraints'])

    def test_general_change_reaches_layered_works_at_run_time(self):
        project = dict(system=copy.deepcopy(workspace.EMPTY_SYSTEM), system_layered=True)
        self.general(review='FIRST')
        self.assertEqual(workspace.effective_system(project)['review'], 'FIRST')
        self.general(review='SECOND')
        self.assertEqual(workspace.effective_system(project)['review'], 'SECOND')

    def test_older_works_keep_their_own_prompts_only(self):
        self.general(storyboard='GENERAL-SB')
        own = dict(workspace.DEFAULT_SYSTEM, storyboard='LEGACY-ONLY')
        self.assertEqual(workspace.effective_system(dict(system=own)), own)
        self.assertEqual(workspace.effective_system({}), workspace.DEFAULT_SYSTEM)

    def test_create_layers_by_default_and_legacy_system_stays_standalone(self):
        project = db.create('new-work', '新作', '原作')
        saved = workspace.save('new-work', project['revision'], dict(system=dict(workspace.EMPTY_SYSTEM), system_layered=True, source='原作'))
        self.assertTrue(saved['system_layered'])
        self.assertEqual(saved['system'], workspace.EMPTY_SYSTEM)
        legacy = db.create('old-work', '旧作', '原作')
        saved = workspace.save('old-work', legacy['revision'], dict(system=dict(workspace.DEFAULT_SYSTEM), source='原作'))
        self.assertFalse(saved.get('system_layered', False))
        self.assertEqual(workspace.editable(saved)['system_layered'], False)

    def test_layered_flag_must_be_boolean_and_changes_the_fingerprint(self):
        project = db.create('flag-work', '旗', '原作')
        with self.assertRaises(db.StudioError):
            workspace.save('flag-work', project['revision'], dict(system_layered='yes', source='原作'))
        saved = workspace.save('flag-work', project['revision'], dict(system=dict(workspace.EMPTY_SYSTEM), system_layered=True, source='原作'))
        before = workspace.fingerprint(saved)
        self.general(constraints='CHANGED-GENERAL')
        self.assertNotEqual(workspace.fingerprint(saved), before)   # a review made earlier no longer matches


if __name__ == '__main__':
    unittest.main()
