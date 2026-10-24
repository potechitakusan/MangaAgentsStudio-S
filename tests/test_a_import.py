import copy
import json
from pathlib import Path
import shutil
import unittest
import uuid
import zipfile
from unittest.mock import patch
from studio import project as db, workspace, novelai, wholepage, a_import, a_source, import_merge, library, backup

ROOT = db.ROOT


class AImportTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / '.runtime' / ('test-a-import-' + uuid.uuid4().hex)
        self.root.mkdir(parents=True)
        self.work_patch = patch.object(db, 'WORK', self.root / 'work'); self.work_patch.start()
        self.network = patch('socket.socket', side_effect=AssertionError('No external or local network')); self.network.start()
        self.a = self.root / 'A'; self.a.mkdir()
        self.write('project.json', dict(schemaVersion=1, name='Synthetic A work', createdAt='2026-10-24'))
        self.write_page(1)
        db.create('test', 'Import destination', 'Placeholder', 'novelai')

    def tearDown(self):
        self.network.stop(); self.work_patch.stop()
        target = self.root.resolve()
        assert target.is_relative_to((ROOT / '.runtime').resolve()) and target.name.startswith('test-a-import-')
        shutil.rmtree(target)

    def write(self, path, value): db.dump(self.a / path, value)

    def write_page(self, n):
        actors = [dict(char_caption='girl, black hair, blue jacket, smiling', centers=[dict(x=.3,y=.6)])]
        raw = novelai.request_payload('1girl, holding a red book, courtyard', 'text, extra fingers', actors, 42, 832, 1216,
                  exclusions=[dict(char_caption='glasses', centers=actors[0]['centers'])],
                  conditioning=dict(use_coords=False,use_order=True,negative_use_coords=False,negative_use_order=False))
        self.write(f'input/novelai/requests/p{n:02}-01.json',raw)
        self.write(f'output/layout/page-{n:03}/layout.json',dict(schemaVersion=2,template=dict(id='P1-splash',panels=[dict(id='panel-01',order=1,polygon=[[0,0],[100,0],[100,100],[0,100]])])))
        self.write(f'input/pages/page-{n:02}.json',dict(schema_version=1,layout=f'output/layout/page-{n:03}/layout.json',panels=[dict(panel=1,request=f'p{n:02}-01')]))

    def transfer(self):
        result = a_import.inspect_source(self.a)
        result['inputs']['characters'] = [dict(id='hero',name='Hero',appearance='',appearance_ja='',voice='',voice_ja='')]
        for page in result['inputs']['pages']:
            for panel in page['panels']:
                panel['scene']='Book scene'; panel['needs_edit']=False
                for actor in panel['characters']: actor['id']='hero'
        _, warnings, _ = a_import.validate_transfer(self.a,result)
        result['acknowledgements']={w['code']:'Confirmed for synthetic fixture' for w in warnings}
        return result

    def apply(self, transfer=None, resolutions=None):
        transfer = transfer or self.transfer()
        return a_import.apply(self.a, transfer, 'test', db.load('test')['revision'], resolutions)

    def test_inspect_readonly_and_requires_identity(self):
        before=a_source.snapshot(self.a)
        value=a_import.inspect_source(self.a)
        with self.assertRaises(db.StudioError): a_import.preview(self.a,value,'test')
        self.assertEqual(a_source.snapshot(self.a),before)
        self.assertFalse(list(db.directory('test').glob('imports/*')))

    def test_extra_storyboard_is_snapshotted_and_change_detected(self):
        path=self.a/'input'/'custom-story.md';path.parent.mkdir(exist_ok=True);path.write_text('A custom storyboard',encoding='utf-8')
        t=self.transfer()
        t['source']=a_source.snapshot(self.a,documents=['input/custom-story.md'])
        self.assertIn('input/custom-story.md',t['source']['files'])
        self.assertTrue(a_import.preview(self.a,t,'test')['ready'])
        path.write_text('Updated storyboard',encoding='utf-8')
        with self.assertRaises(db.StudioError): a_import.preview(self.a,t,'test')
        for name in ['input/../secret.md','input/.env','config/private.txt']:
            with self.assertRaises(db.StudioError): a_source.snapshot(self.a,documents=[name])

    def test_alternative_can_be_selected_without_editing_a(self):
        (self.a/'input/pages/page-01.json').unlink()
        raw=db.read_json(self.a/'input/novelai/requests/p01-01.json')
        raw['input']='1girl, library';raw['parameters']['v4_prompt']['caption']['base_caption']=raw['input']
        self.write('input/novelai/requests/p01-01a.json',raw)
        before=a_source.snapshot(self.a);t=a_import.inspect_source(self.a)
        self.assertEqual(len(t['inputs']['pages'][0]['panels']),1)
        self.assertTrue(any(i['code'].endswith(':alternatives') for i in t['issues']))
        t['inputs']['pages'][0]['template']='P1-splash'
        t['inputs']['characters']=[dict(id='hero',name='Hero',appearance='')]
        panel=t['inputs']['pages'][0]['panels'][0];panel.update(a_source.request_parts(raw),scene='Library',needs_edit=False)
        panel['characters'][0]['id']='hero';t['bindings'][0]['request']='input/novelai/requests/p01-01a.json'
        _,warnings,_=a_import.validate_transfer(self.a,t)
        t['acknowledgements']={w['code']:'Synthetic chosen alternative' for w in warnings}
        self.apply(t)
        self.assertEqual(novelai.build_request(db.load('test'),db.panels(db.load('test'))[0]),raw)
        self.assertEqual(a_source.snapshot(self.a),before)

    def test_literal_request_and_v4_settings_roundtrip(self):
        self.apply()
        p=db.load('test'); panel=db.panels(p)[0]
        req=novelai.build_request(p,panel)
        self.assertEqual(req,db.read_json(self.a/'input/novelai/requests/p01-01.json'))
        exported=workspace.config_export('test','all')
        self.assertEqual(exported['schema'],'manga-studio-inputs-v4')
        self.assertNotIn('import_state',exported['inputs'])
        p=workspace.config_import('test',p['revision'],exported)
        self.assertEqual(novelai.build_request(p,db.panels(p)[0]),req)
        old=dict(exported,schema='manga-studio-inputs-v3')
        with self.assertRaises(db.StudioError): workspace.config_import('test',p['revision'],old)
        self.assertEqual(p['schema_version'],4)

    def test_catalog_mode_merges_appearance_outfit_and_negative(self):
        t=self.transfer(); c=t['inputs']['characters'][0]
        c.update(appearance='girl, black hair',outfits=[dict(id='blue',name='Blue',prompt='blue jacket',prompt_ja='')],default_outfit_id='blue')
        panel=t['inputs']['pages'][0]['panels'][0];panel['prompt_mode']='catalog';panel['characters'][0].update(prompt='smiling',outfit_id='blue')
        _,warnings,_=a_import.validate_transfer(self.a,t)
        change=next(w for w in warnings if w['code'].endswith(':request:input'))
        self.assertEqual(change['before'],panel['prompt']);self.assertIn('very aesthetic',change['after'])
        t['acknowledgements'].update({w['code']:'Catalog composition' for w in warnings})
        self.apply(t);p=db.load('test');r=novelai.build_request(p,db.panels(p)[0])
        char=r['parameters']['v4_prompt']['caption']['char_captions'][0]['char_caption']
        self.assertEqual(char.count('black hair'),1);self.assertEqual(char.count('blue jacket'),1)
        self.assertEqual(r['parameters']['v4_negative_prompt']['caption']['char_captions'][0]['char_caption'],'glasses')
        self.assertFalse(r['parameters']['v4_prompt']['use_coords'])

    def test_preview_no_writes_and_repeat_is_noop(self):
        t=self.transfer();original=(db.directory('test')/'project.json').read_bytes()
        report=a_import.preview(self.a,t,'test');self.assertTrue(report['ready'])
        self.assertEqual((db.directory('test')/'project.json').read_bytes(),original)
        self.apply(t);revision=db.load('test')['revision']
        report=self.apply(t);self.assertFalse(report['changed']);self.assertEqual(report['revision'],revision)
        self.assertEqual(len(list((db.directory('test')/'imports').glob('*.json'))),1)

    def test_reimport_removed_options_reset_instead_of_reviving_old_values(self):
        self.apply();t=self.transfer();panel=t['inputs']['pages'][0]['panels'][0]
        panel.pop('prompt_mode');panel.pop('conditioning')
        for actor in panel['characters']:actor.pop('negative');actor.pop('negative_ja')
        _,warnings,_=a_import.validate_transfer(self.a,t)
        t['acknowledgements'].update({w['code']:'Synthetic reset to S defaults' for w in warnings})
        self.apply(t)
        p=db.load('test');actual=db.panels(p)[0];request=novelai.build_request(p,actual)
        self.assertEqual(actual['prompt_mode'],'catalog')
        self.assertTrue(request['parameters']['v4_prompt']['use_coords'])
        self.assertEqual(request['parameters']['v4_negative_prompt']['caption']['char_captions'][0]['char_caption'],'')
        self.assertFalse(self.apply(t)['changed'])

    def test_reimport_preserves_s_edits_and_candidates(self):
        self.apply();p=db.load('test');pid=db.panels(p)[0]['id']
        p=db.edit_panel('test',p['revision'],pid,dict(scene='Edited in S',dialogue=['S dialogue'],speakers=['hero']))
        db.mutate('test',p['revision'],lambda q: db.panels(q)[0].update(image='images/retained.png',candidates=[dict(id='retained')]))
        raw=db.read_json(self.a/'input/novelai/requests/p01-01.json')
        raw['input']+=' at sunset';raw['parameters']['v4_prompt']['caption']['base_caption']=raw['input']
        self.write('input/novelai/requests/p01-01.json',raw)
        self.apply();panel=db.panels(db.load('test'))[0]
        self.assertEqual(panel['scene'],'Edited in S');self.assertEqual(panel['dialogue'],['S dialogue'])
        self.assertTrue(panel['prompt'].endswith('at sunset'));self.assertEqual(panel['image'],'images/retained.png')
        self.assertEqual(panel['candidates'],[dict(id='retained')])

    def test_same_field_conflict_and_accepted_baseline(self):
        self.apply();p=db.load('test');pid=db.panels(p)[0]['id']
        db.edit_panel('test',p['revision'],pid,dict(prompt='S revised base'))
        raw=db.read_json(self.a/'input/novelai/requests/p01-01.json')
        raw['input']='A revised base';raw['parameters']['v4_prompt']['caption']['base_caption']=raw['input']
        self.write('input/novelai/requests/p01-01.json',raw)
        t=self.transfer();preview=a_import.preview(self.a,t,'test')
        self.assertFalse(preview['ready']);conflict=next(c for c in preview['conflicts'] if c['path'].endswith('/prompt'))
        with self.assertRaises(db.StudioError): self.apply(t)
        self.apply(t,{conflict['path']:'local'})
        self.assertEqual(db.panels(db.load('test'))[0]['prompt'],'S revised base')
        self.assertTrue(a_import.preview(self.a,t,'test')['ready'])
        self.assertFalse(self.apply(t)['changed'])

    def test_upstream_delete_requires_decision_and_history_retains(self):
        self.write_page(2);self.apply();p=db.load('test')
        (self.a/'input/pages/page-01.json').unlink()
        (self.a/'input/novelai/requests/p01-01.json').unlink()
        t=self.transfer();report=a_import.preview(self.a,t,'test')
        conflict=next(c for c in report['conflicts'] if c['path']=='/pages/a-page-001')
        self.assertIn('削除',conflict['reason'])
        self.apply(t,{conflict['path']:'incoming'})
        self.assertEqual(len(db.load('test')['pages']),1)
        old=db.read_json(db.directory('test')/'history'/f"project-{p['revision']:05}.json")
        self.assertEqual(len(old['pages']),2)

    def test_source_changed_revision_lock_and_state_tampering(self):
        t=self.transfer();self.apply(t)
        with self.assertRaises(db.StudioError): a_import.apply(self.a,t,'test',0)
        lock=db.directory('test')/'.project.lock';lock.write_text('test')
        modified=copy.deepcopy(t);modified['inputs']['title']='Another title'
        with self.assertRaises(db.StudioError): self.apply(modified)
        lock.unlink()
        state=a_import.state_for(db.load('test'));key=db.load('test')['import_state']
        state['base']['title']='Tampered';db.dump(db.directory('test')/'imports'/(key+'.json'),state)
        with self.assertRaises(db.StudioError): a_import.preview(self.a,t,'test')
        self.write('project.json',dict(name='Changed',createdAt='2026-10-24'))
        with self.assertRaises(db.StudioError): a_import.validate_transfer(self.a,t)

    def test_unknown_fields_nonfinite_variables_and_flags(self):
        t=self.transfer()
        for mutate in (
            lambda x:x['inputs']['pages'][0]['panels'][0].update(image='images/injected.png'),
            lambda x:x['inputs']['pages'][0]['panels'][0]['characters'][0].update(unknown='x'),
            lambda x:x['inputs']['pages'][0]['panels'][0]['conditioning'].update(use_coords=1),
            lambda x:x['inputs']['pages'][0]['panels'][0].update(prompt='${NOVELAI_CHAR_EXAMPLE}'),
            lambda x:x['inputs']['pages'][0]['panels'][0]['characters'][0].update(x=float('nan')),
            lambda x:x['inputs'].update(system={'storyboard':'injected'}),
        ):
            bad=copy.deepcopy(t);mutate(bad)
            with self.assertRaises(db.StudioError): a_import.validate_transfer(self.a,bad)

    def test_source_path_and_credentials_are_rejected(self):
        for path in ('../../outside.json','.env','input/novelai/requests/../../.env','C:/secret.json'):
            self.write('input/pages/page-01.json',dict(schema_version=1,layout=path,panels=[]))
            with self.assertRaises(db.StudioError): a_import.inspect_source(self.a)
        self.write_page(1)
        self.write('input/novelai/requests/p01-01.json',dict(action='generate',input='x',parameters=dict(api_key='FAKE_TEST_VALUE')))
        with self.assertRaisesRegex(db.StudioError,'認証'): a_import.inspect_source(self.a)
        with self.assertRaises(db.StudioError): a_source.parse('{"x":1,"x":2}')
        with self.assertRaises(db.StudioError): a_source.parse('{"x":NaN}')

    def test_acknowledgements_cannot_be_bypassed_by_deleting_issues(self):
        t=self.transfer();t['issues']=[];t['acknowledgements']={}
        report=a_import.preview(self.a,t,'test');self.assertFalse(report['ready'])
        with self.assertRaises(db.StudioError): self.apply(t)

    def test_import_state_survives_folder_copy_and_backup(self):
        self.apply();p=db.load('test');record=a_import.state_for(p)
        saved=backup.create('test',full=False)
        archive=Path(saved['path']) if 'path' in saved else None
        if archive is None:
            archive=next((db.WORK/'_backups'/'test').glob('*.zip'))
        if not archive.is_absolute(): archive=ROOT/archive
        with zipfile.ZipFile(archive) as z: self.assertTrue(any('/imports/' in n or n.startswith('imports/') for n in z.namelist()))
        external=self.root/'external';shutil.copytree(db.directory('test'),external)
        with patch.object(db,'WORK',self.root/'copied'):
            library.import_folder(str(external))
            self.assertEqual(a_import.state_for(db.load('test')),record)

    def test_output_cannot_overwrite_a_or_project(self):
        with self.assertRaises(db.StudioError): a_import.write_output(self.a,self.a/'transfer.json',{})
        with self.assertRaises(db.StudioError): a_import.write_output(self.a,db.directory('test')/'project.json',{})

    def test_generation_mode_cannot_discard_imported_options(self):
        self.apply();p=db.load('test')
        with self.assertRaises(db.StudioError): wholepage.set_mode('test',p['revision'],'whole')
        self.assertEqual(db.load('test').get('render_mode'),'panels')


class MergeTests(unittest.TestCase):
    def test_independent_fields_and_duplicate_application(self):
        base=dict(characters=[dict(id='a',appearance='hair',voice='quiet')])
        local=copy.deepcopy(base);local['characters'][0]['voice']='loud'
        incoming=copy.deepcopy(base);incoming['characters'][0]['appearance']='new hair'
        result,conflicts=import_merge.merge(base,local,incoming)
        self.assertEqual(conflicts,[]);self.assertEqual(result['characters'][0],dict(id='a',appearance='new hair',voice='loud'))
        self.assertEqual(import_merge.merge(incoming,result,incoming)[0],result)

    def test_reorder_and_remove_keep_local_order(self):
        base=dict(pages=[dict(id='a'),dict(id='b')]);local=copy.deepcopy(base);incoming=dict(pages=[dict(id='b')])
        result,conflicts=import_merge.merge(base,local,incoming,{'/pages/a':'local'})
        self.assertEqual([x['id'] for x in result['pages']],['a','b'])
        self.assertEqual(len(conflicts),1)

    def test_stale_resolution_rejected(self):
        with self.assertRaises(db.StudioError): import_merge.merge({}, {}, {}, {'/obsolete':'incoming'})


if __name__=='__main__': unittest.main()
