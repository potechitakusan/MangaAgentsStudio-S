import io
import json
import unittest
from unittest.mock import patch
from PIL import Image
from studio import project as db, images, qwen, novelai, workspace
import test_profiles

class QwenTests(unittest.TestCase):
    prepared=test_profiles.ProfileTests.prepared
    def setUp(self):
        test_profiles.ProfileTests.setUp(self)
        p=db.load('test');p['output_method']='qwen';
        for page in p['pages']: page.get('whole',{}).pop('draft',None)
        p['image_settings']['reference_mode']='prompt'
        db.dump(db.directory('test')/'project.json',p)
        p=patch.object(qwen,'ledger_path',lambda:self.root/'qwen-ledger.json');p.start();self.patches.append(p)
        self.context=images.using({'provider':'qwen'});self.context.__enter__()

    def tearDown(self):
        self.context.__exit__(None,None,None)
        test_profiles.ProfileTests.tearDown(self)

    def stage(self,g,record,stage,persist):
        dims=g['11']['inputs']
        out=io.BytesIO();Image.new('RGB',(dims['width']*2,dims['height']*2),'white').save(out,'PNG')
        record.setdefault('stages',{})[stage]={'elapsed_seconds':1.2,'prompt_id':'test'}
        return out.getvalue()

    def test_qwen_selected_outfit_and_provider_staleness(self):
        p=self.prepared();panel=db.panels(p)[0]
        r=qwen.build_request(p,panel)
        self.assertIn('blue dress',r['input']);self.assertNotIn('green apron',r['input'])
        self.assertIn('crying',r['input']);self.assertEqual(r['parameters']['steps'],8)
        with patch.object(qwen,'preflight'),patch.object(qwen,'run_stage',side_effect=self.stage),patch.object(novelai,'request') as remote:
            result=images.generate('test',panel['id'],'qwen-one',p['revision'])
            self.assertEqual(result['status'],'complete');remote.assert_not_called()
        p=db.load('test');panel=db.panels(p)[0]
        self.assertFalse(images.is_stale(p,panel=panel))
        with images.using({'provider':'novelai'}): self.assertFalse(images.is_stale(p,panel=panel))
        p['image_settings']['anime_finish']=True
        self.assertTrue(images.is_stale(p,panel=panel))

    def test_empty_scenery_does_not_request_anatomy_from_style(self):
        p=self.prepared();p['style']='monochrome, rounded faces, large expressive eyes, delicate lineart, slightly oversized heads'
        panel=db.panels(p)[0]
        self.assertIn('rounded faces',qwen.build_request(p,panel)['input'])
        panel['characters']=[];panel['dialogue']=[];panel['speakers']=[]
        text=qwen.build_request(p,panel)['input']
        self.assertNotIn('rounded faces',text);self.assertNotIn('expressive eyes',text)
        self.assertIn('delicate lineart',text);self.assertIn('There are no people',text)
        trial=qwen.preview(p,dict(kind='location',id=p['locations'][0]['id']))
        self.assertNotIn('rounded faces',trial['request']['input'])
        self.assertIn('delicate lineart',trial['request']['input'])

    def test_qwen_trial_and_idempotency(self):
        p=self.prepared();options=dict(kind='character',id='mio',outfit_id='outing',expression='smile')
        with patch.object(qwen,'preflight'),patch.object(qwen,'run_stage',side_effect=self.stage) as send:
            first=images.generate('test',None,'qwen-trial',p['revision'],preview=options)
            again=images.generate('test',None,'qwen-trial',preview=options)
            self.assertEqual(first,again);self.assertEqual(send.call_count,1)
            with self.assertRaises(db.StudioError): images.generate('test','p1-1','qwen-trial')
        self.assertEqual(db.load('test'),p)

    def test_qwen_unknown_stops_new_ids_and_novelai(self):
        p=self.prepared()
        with patch.object(qwen,'preflight'),patch.object(qwen,'run_stage',side_effect=TimeoutError) as send:
            with self.assertRaises(TimeoutError): images.generate('test','p1-1','uncertain')
            self.assertEqual(images.generate('test','p1-1','uncertain')['status'],'unknown')
            with self.assertRaisesRegex(db.StudioError,'未確認'): images.generate('test','p1-1','another')
            with self.assertRaisesRegex(db.StudioError,'未確認'): novelai.generate('test','p1-1','nai-after')
            self.assertEqual(send.call_count,1)
        self.assertEqual(db.load('test'),p)

    def test_qwen_edit_conflict_keeps_candidate(self):
        self.prepared()
        def changed(*args):
            p=db.load('test');db.edit_panel('test',p['revision'],'p1-1',{'scene':'new edit'})
            return self.stage(*args)
        with patch.object(qwen,'preflight'),patch.object(qwen,'run_stage',side_effect=changed):
            images.generate('test','p1-1','conflict')
        p=db.load('test');panel=db.panels(p)[0]
        self.assertEqual(panel['scene'],'new edit');self.assertFalse(panel.get('image'));self.assertEqual(len(panel['candidates']),1)

    def test_qwen_settings_and_no_partner_nodes(self):
        for bad in ({'provider':'cloud'},{'denoise':.9},{'anime_finish':'yes'},{'url':'https://example.com'}):
            with self.assertRaises(db.StudioError):images.validate(bad)
        p=self.prepared();r=qwen.build_request(p,db.panels(p)[0]);g=qwen.graph(r,'test')
        self.assertEqual(g['8']['inputs']['cfg'],1)
        self.assertEqual(g['11']['inputs']['width']*2,r['parameters']['width'])
        fg=qwen.finish_graph(r,'local.png','test')
        self.assertEqual(fg['22']['inputs']['denoise'],.3)
        self.assertEqual(sum(n['class_type']=='LoraLoader' for n in fg.values()),3)
        with patch.object(qwen,'http',return_value={n['class_type']:{'api_node':True} for n in g.values()}):
            with self.assertRaises(db.StudioError):qwen.preflight(g)

    def base_panel(self):
        self.prepared()
        with patch.object(qwen,'preflight'),patch.object(qwen,'run_stage',side_effect=self.stage):
            images.generate('test','p1-1','base')
        return db.load('test')

    def test_finish_existing_reuses_exact_art_and_is_idempotent(self):
        p=self.base_panel();base=db.panel_by_id(p,'p1-1')['image'];raw=db.asset_path('test',base).read_bytes()
        def finish(g,record,stage,persist):
            self.assertEqual(stage,'anima');self.assertFalse(any(n['class_type']=='TextEncodeQwenImage21' for n in g.values()))
            self.assertEqual(g['22']['inputs']['denoise'],.3)
            return raw
        with patch.object(qwen,'preflight'),patch.object(qwen,'upload',return_value='base.png') as upload,patch.object(qwen,'run_stage',side_effect=finish) as send:
            r=qwen.finish_existing('test','p1-1','finished',p['revision'])
            self.assertEqual(qwen.finish_existing('test','p1-1','finished',p['revision']),r)
            self.assertEqual(send.call_count,1);self.assertEqual(upload.call_args.args[0],raw)
            with self.assertRaises(db.StudioError):qwen.finish_existing('test','p1-2','finished',p['revision'])
        p=db.load('test');panel=db.panel_by_id(p,'p1-1')
        self.assertEqual(len(panel['candidates']),2);self.assertEqual(r['source_image'],base)
        self.assertEqual(db.asset_path('test',base).read_bytes(),raw)
        self.assertFalse(qwen.is_stale(p,panel));self.assertFalse(p['image_settings']['anime_finish'])
        self.assertTrue(db.read_json(db.asset_path('test',panel['image']).with_suffix('.finish-request.json'))['finish']['enabled'])
        with self.assertRaisesRegex(db.StudioError,'重ね掛け'):
            qwen.finish_existing('test','p1-1','again',p['revision'])

    def test_finish_unknown_stops_resend(self):
        p=self.base_panel()
        with patch.object(qwen,'preflight'),patch.object(qwen,'upload',return_value='base.png'),patch.object(qwen,'run_stage',side_effect=TimeoutError) as send:
            with self.assertRaises(TimeoutError):qwen.finish_existing('test','p1-1','unknown-finish',p['revision'])
            self.assertEqual(qwen.finish_existing('test','p1-1','unknown-finish',p['revision'])['status'],'unknown')
            with self.assertRaisesRegex(db.StudioError,'未確認'):qwen.finish_existing('test','p1-1','no-retry',p['revision'])
            self.assertEqual(send.call_count,1)
        self.assertEqual(db.panel_by_id(db.load('test'),'p1-1')['adopted_request'],'base')

    def test_finish_edit_conflict_preserves_original_adoption(self):
        p=self.base_panel();raw=db.asset_path('test',db.panel_by_id(p,'p1-1')['image']).read_bytes()
        def finish(*args):
            db.edit_panel('test',p['revision'],'p1-1',{'scene':'changed while finishing'})
            return raw
        with patch.object(qwen,'preflight'),patch.object(qwen,'upload',return_value='base.png'),patch.object(qwen,'run_stage',side_effect=finish):
            qwen.finish_existing('test','p1-1','conflict-finish',p['revision'])
        panel=db.panel_by_id(db.load('test'),'p1-1')
        self.assertEqual(panel['adopted_request'],'base');self.assertEqual(len(panel['candidates']),2)

    def test_run_stage_never_resubmits_when_poll_fails(self):
        record={};calls=[]
        def http(path,payload=None):
            calls.append(path)
            if path=='/prompt': return {'prompt_id':'saved-prompt'}
            raise db.StudioError('connection lost')
        with patch.object(qwen,'http',side_effect=http):
            with self.assertRaises(db.StudioError): qwen.run_stage({},record,'qwen',lambda:None)
        self.assertEqual(calls.count('/prompt'),1)
        self.assertEqual(record['stages']['qwen']['prompt_id'],'saved-prompt')

    def test_qwen_review_fingerprint_changes(self):
        p=self.prepared();a=workspace.fingerprint(p)
        with images.using({'provider':'novelai'}):self.assertEqual(a,workspace.fingerprint(p))
        p['image_settings']['reference_mode']='references'
        self.assertNotEqual(a,workspace.fingerprint(p))

    def test_job_context_freezes_copy(self):
        settings={'provider':'qwen','anime_finish':True,'denoise':.25}
        with images.using(settings):
            settings['provider']='novelai'
            a=images.current();a['denoise']=.35
            self.assertEqual(images.current()['provider'],'qwen')
            self.assertEqual(images.current()['denoise'],.25)

    def test_whole_formats_include_cast_and_respect_lettering(self):
        p=self.prepared();p['text_mode']='overlay'
        for fmt in ['prose','json','yaml']:
            p['pages'][0]['whole']={'format':fmt}
            request=qwen.build_page_request(p,1)
            self.assertIn('blue dress',request['input'])
            self.assertIn('No text',request['input'])
            self.assertNotIn('Speech bubbles in reading order',request['input'])
            if fmt=='json':self.assertEqual(len(json.loads(request['input'])['panels']),3)
        p['text_mode']='embedded';p['pages'][0]['whole']['format']='json'
        plan=json.loads(qwen.build_page_request(p,1)['input'])
        self.assertEqual(plan['panels'][0]['dialogue'][0]['text'],p['pages'][0]['panels'][0]['dialogue'][0])

if __name__=='__main__':unittest.main()
