import copy
import io
import unittest
from unittest.mock import patch
from PIL import Image
from studio import project as db, images, references, qwen, novelai, workspace, wholepage
import test_profiles


class ReferenceTests(unittest.TestCase):
    setUp=test_profiles.ProfileTests.setUp
    tearDown=test_profiles.ProfileTests.tearDown
    prepared=test_profiles.ProfileTests.prepared

    def qproject(self):
        p=self.prepared()
        # Test fixture only. Production mutations cannot change the method.
        p['output_method']='qwen';
        for page in p['pages']: page.get('whole',{}).pop('draft',None)
        p['image_settings']['reference_mode']='references'
        db.dump(db.directory('test')/'project.json',p)
        return p

    def trial(self, options, name):
        p=db.load('test')
        def stage(g,record,stage,persist):
            size=g['11']['inputs'];out=io.BytesIO()
            Image.new('RGB',(size['width']*2,size['height']*2),'#7195ab').save(out,'PNG')
            return out.getvalue()
        with patch.object(qwen,'preflight'),patch.object(qwen,'run_stage',side_effect=stage) as run,patch.object(novelai,'request') as remote:
            result=images.generate('test',None,name,p['revision'],preview=options)
            self.assertEqual(run.call_count,1);remote.assert_not_called()
        self.assertEqual(db.load('test')['revision'],p['revision'])
        return result

    def registered(self):
        p=self.qproject()
        self.trial(dict(kind='character',id='mio',outfit_id='outing'), 'char-ref')
        p=references.adopt('test',p['revision'],'char-ref')
        self.trial(dict(kind='location',id='shop'), 'loc-ref')
        return references.adopt('test',p['revision'],'loc-ref')

    def test_output_method_locked_in_mutations_and_all_import_scopes(self):
        self.qproject()
        db.create('other','Other','source')
        for scope in ('all','carry','system'):
            data=workspace.config_export('test',scope)
            self.assertEqual(data['output_method'],'qwen')
            with self.assertRaisesRegex(db.StudioError,'output_method'):
                workspace.config_import('other',0,data)
            data.pop('output_method')
            with self.assertRaisesRegex(db.StudioError,'output_method'):
                workspace.config_import('test',db.load('test')['revision'],data)
        with self.assertRaises(db.StudioError):workspace.save('other',0,dict(output_method='qwen'))
        with self.assertRaises(db.StudioError):db.mutate('other',0,lambda p:p.update(output_method='qwen'))
        board=copy.deepcopy(self.board);board['output_method']='qwen'
        with self.assertRaisesRegex(db.StudioError,'output_method'):db.import_board('other',0,board)
        board.pop('output_method')
        with self.assertRaisesRegex(db.StudioError,'output_method'):db.import_board('other',0,board)
        self.assertEqual(db.load('other')['revision'],0)
        data=workspace.config_export('test','all');data['inputs']['output_method']='novelai'
        with self.assertRaises(db.StudioError):workspace.config_import('test',db.load('test')['revision'],data)

    def test_legacy_rejected_and_not_automatically_converted(self):
        p=db.load('test');p.pop('output_method');db.dump(db.directory('test')/'project.json',p)
        with self.assertRaisesRegex(db.StudioError,'output_method'):db.load('test')
        self.assertFalse(db.list_projects()[0]['compatible'])
        self.assertNotIn('output_method',db.read_json(db.directory('test')/'project.json'))

    def test_reference_reuse_graph_mapping_and_prompt_only(self):
        p=self.registered();panel=db.panels(p)[0]
        a=images.build_request(p,panel);b=images.build_request(p,db.panels(p)[1])
        self.assertEqual(a['references'],b['references'])
        self.assertEqual([r['slot'] for r in a['references']],['image1','image2'])
        self.assertEqual([r['kind'] for r in a['references']],['character','location'])
        self.assertIn('image1 is the reference for character mio wearing outfit outing',a['input'])
        g=qwen.graph(a,'test')
        self.assertEqual(g['5']['inputs']['images.image_1'],['101',0])
        self.assertEqual(g['5']['inputs']['images.image_2'],['102',0])
        whole=images.build_page_request(p,1)
        self.assertEqual(len(whole['references']),2)  # all panels reuse one pair
        self.assertFalse(any(c.get('image') for c in db.panels(p)))
        p['image_settings']['reference_mode']='prompt'
        self.assertEqual(images.build_request(p,panel)['references'],[])
        self.assertFalse(any(n['class_type']=='LoadImage' for n in qwen.graph(images.build_request(p,panel),'test').values()))

    def test_missing_stale_outfit_and_corrupt_images_stop_before_send(self):
        p=self.qproject()
        with patch.object(qwen,'http') as send:
            with self.assertRaisesRegex(db.StudioError,'未登録'):images.generate('test','p1-1','missing')
            send.assert_not_called()
        # registered() calls prepared() again, safely preserving the current work.
        p=self.registered();panel=db.panels(p)[0]
        panel['characters'][0]['outfit_id']='work'
        with self.assertRaisesRegex(db.StudioError,'未登録'):images.build_request(p,panel)
        panel['characters'][0]['outfit_id']='outing'
        p['characters'][0]['voice']='polite';p['characters'][0]['appearance_ja']='訳の修正'
        images.build_request(p,panel)
        p['characters'][0]['appearance']+=' red eyes'
        with self.assertRaisesRegex(db.StudioError,'変更前'):images.build_request(p,panel)
        p=db.load('test');ref=next(iter(p['references'].values()))
        db.asset_path('test',ref['image']).write_bytes(b'changed')
        with self.assertRaisesRegex(db.StudioError,'ファイルが変更'):images.build_request(p,db.panels(p)[0])

    def test_old_candidate_and_portrait_cannot_be_registered(self):
        p=self.qproject();self.trial(dict(kind='character',id='mio',framing='portrait'),'portrait')
        with self.assertRaisesRegex(db.StudioError,'全身'):references.adopt('test',p['revision'],'portrait')
        self.trial(dict(kind='character',id='mio'),'before-change')
        p=workspace.save('test',p['revision'],dict(style='watercolor'))
        with self.assertRaisesRegex(db.StudioError,'変更'):references.adopt('test',p['revision'],'before-change')
        with self.assertRaises(db.StudioError):references.adopt('test',p['revision'],'../../escape')

    def test_reference_change_invalidates_generated_image_and_review(self):
        p=self.registered();panel=db.panels(p)[0];request=images.build_request(p,panel)
        image='images/p1-1/old.png';db.dump(db.asset_path('test',image).with_suffix('.request.json'),request)
        panel['image']=image;db.asset_path('test',image).write_bytes(b'placeholder')
        self.assertFalse(images.is_stale(p,panel=panel));before=workspace.fingerprint(p)
        # A different completed candidate with the same visual inputs is a new reference.
        ref=p['references']['character/mio/outing'];ref['candidate_id']='new-selection'
        self.assertTrue(images.is_stale(p,panel=panel));self.assertNotEqual(before,workspace.fingerprint(p))

    def test_config_roundtrip_excludes_local_reference_paths(self):
        p=self.registered();data=workspace.config_export('test','all')
        self.assertNotIn('references',data['inputs'])
        db.create('same','Same','source','qwen')
        result=workspace.config_import('same',0,data)
        self.assertEqual(result['references'],{})
        self.assertEqual(result['output_method'],'qwen')
        data['inputs']['references']=p['references']
        with self.assertRaises(db.StudioError):workspace.config_import('same',result['revision'],data)

    def test_external_png_preserves_bytes_and_provenance_without_generation(self):
        p=self.qproject();out=io.BytesIO();Image.new('RGBA',(40,60),'red').save(out,'PNG');png=out.getvalue()
        with patch.object(qwen,'http') as send:
            p=references.import_png('test',p['revision'],'character','mio',png,outfit_id='outing',source_name='game/hero.png')
            send.assert_not_called()
        target=references.profile(p,'character','mio','outing');ref=references.selected(p,target)
        self.assertEqual(references.read_image(p,ref),png)
        self.assertEqual(ref['origin'],'imported');self.assertEqual(ref['source_name'],'game/hero.png')
        self.assertFalse((db.directory('test')/'images'/'previews').exists())
        with self.assertRaisesRegex(db.StudioError,'編集競合'):
            references.import_png('test',p['revision']-1,'character','mio',png,outfit_id='outing')
        with self.assertRaisesRegex(db.StudioError,'有効なPNG'):
            references.import_png('test',p['revision'],'character','mio',b'not an image')
        self.assertEqual(db.load('test')['revision'],p['revision'])

    def test_frozen_job_and_wrong_adapter_cannot_switch_provider(self):
        p=self.qproject()
        with images.using(images.for_project(p)),patch.object(qwen,'http') as send:
            workspace.save('test',p['revision'],dict(image_settings=dict(p['image_settings'],reference_mode='prompt')))
            with self.assertRaisesRegex(db.StudioError,'設定が変更'):images.generate('test','p1-1','changed')
            send.assert_not_called()
        with patch.object(novelai,'request') as send:
            with self.assertRaisesRegex(db.StudioError,'NovelAI作品'):novelai.generate('test','p1-1','wrong')
            send.assert_not_called()

    def test_whole_design_method_mismatch_is_rejected_before_linked_rebuild(self):
        p=self.qproject();v=workspace.editable(p)
        v['pages'][0]['whole']['draft']['output_method']='novelai'
        with self.assertRaisesRegex(db.StudioError,'output_method'):workspace.save('test',p['revision'],v)
        for wrong in ('novelai',None):
            config=wholepage.settings(p,1)
            if wrong:config['draft']['output_method']=wrong
            else:config['draft'].pop('output_method')
            with self.assertRaisesRegex(db.StudioError,'output_method'):wholepage.save('test',p['revision'],1,config)
        self.assertEqual(db.load('test')['revision'],p['revision'])

    def test_whole_reference_formats_and_no_silent_truncation(self):
        p=self.registered()
        for fmt in ('prose','json','yaml'):
            p['pages'][0]['whole']['format']=fmt
            request=images.build_page_request(p,1)
            self.assertEqual(len(request['references']),2)
            self.assertIn('image2',request['input'])
            if fmt=='json':self.assertIn('reference_instructions',__import__('json').loads(request['input']))
            if fmt=='yaml':self.assertIn('reference_instructions:',request['input'])
        p['locations']=[dict(id=f'loc-{i}',name=f'Location {i}',description='wall') for i in range(17)]
        with patch.object(references,'selected') as select:
            with self.assertRaisesRegex(db.StudioError,'最大16枚'):references.collect(p,[dict(location_id=f'loc-{i}') for i in range(17)])
            select.assert_not_called()
if __name__=='__main__':unittest.main()
