import copy
import io
import json
import unittest
from unittest.mock import patch
from PIL import Image
from studio import project as db, profiles, novelai, wholepage, workspace, strata_tasks
import test_studio as baseline

class ProfileTests(unittest.TestCase):
    setUp=baseline.StudioTests.setUp
    tearDown=baseline.StudioTests.tearDown

    def prepared(self):
        p=db.load('test');v=workspace.editable(p)
        c=v['characters'][0]
        c.update(appearance='adult woman, black bob, brown eyes',appearance_ja='成人女性、黒いボブ、茶色の目',default_outfit_id='work',
                 outfits=[dict(id='work',name='作業着',prompt='green apron, white shirt',prompt_ja='緑のエプロン、白いシャツ'),
                          dict(id='outing',name='外出着',prompt='blue dress, brown boots',prompt_ja='青いワンピース、茶色のブーツ')])
        v['locations']=[dict(id='shop',name='店',description='wooden counter, round window',description_ja='木のカウンター、丸窓')]
        for panel in v['pages'][0]['panels']:
            panel['location_id']='shop';panel['characters'][0].update(prompt='standing',outfit_id='outing',expression='crying')
        v['pages'][0]['whole']['linked']=True
        return workspace.save('test',p['revision'],v)

    def test_selected_outfit_expression_in_all_formats_and_roundtrip(self):
        p=self.prepared();actor=db.panels(p)[0]['characters'][0]
        db.panels(p)[0]['prompt'] += ', soft smile'
        p['pages'][0]['whole']['draft']['panels'][0]['prompt'] += ', soft smile'
        caption=novelai.build_request(p,db.panels(p)[0])['parameters']['v4_prompt']['caption']['char_captions'][0]['char_caption']
        self.assertIn('blue dress',caption);self.assertIn('crying, tears',caption);self.assertNotIn('green apron',caption)
        self.assertNotIn('soft smile',novelai.build_request(p,db.panels(p)[0])['input'])
        for fmt in wholepage.FORMATS:
            settings=wholepage.settings(p,1);settings['format']=fmt
            self.assertIn(caption,wholepage.compile_prompt(p,1,settings)['actors'][0]['char_caption'])
            self.assertNotIn('soft smile',wholepage.compile_prompt(p,1,settings)['caption'])
        data=workspace.config_export('test','all');db.create('copy','複製','原作')
        restored=workspace.config_import('copy',0,data)
        self.assertEqual(restored['characters'],p['characters'])
        self.assertEqual(db.panels(restored)[0]['characters'][0],actor)
        default={**actor,'outfit_id':''};self.assertIn('green apron',profiles.caption(p['characters'][0],default))

    def test_limit_duplicates_and_references(self):
        p=self.prepared();c=p['characters'][0]
        c['outfits']=[dict(id=f'o{i}',name=f'服{i}',prompt='shirt',prompt_ja='シャツ') for i in range(10)]
        c['default_outfit_id']='o0';profiles.validate_character(c)
        c['outfits'].append(dict(c['outfits'][0],id='o10'))
        with self.assertRaisesRegex(db.StudioError,'10'): profiles.validate_character(c)
        c['outfits'].pop();c['outfits'][1]['id']='o0'
        with self.assertRaisesRegex(db.StudioError,'重複'): profiles.validate_character(c)
        v=workspace.editable(db.load('test'));v['characters'][0]['outfits'].pop()
        with self.assertRaisesRegex(db.StudioError,'未登録の衣装'): workspace.save('test',v.get('revision',db.load('test')['revision']),v)
        actor=db.panels(db.load('test'))[0]['characters'][0];actor['expression']='invalid'
        with self.assertRaisesRegex(db.StudioError,'表情'): profiles.validate_actor(db.load('test')['characters'][0],actor)

    def send_image(self,endpoint,payload):
        out=io.BytesIO();Image.new('RGB',(payload['parameters']['width'],payload['parameters']['height']),'#456789').save(out,'PNG');return out.getvalue()

    def test_trial_no_adoption_idempotency_and_history(self):
        p=self.prepared();options=dict(kind='character',id='mio',outfit_id='outing',expression='smile',framing='full',seed=123)
        with patch.object(novelai,'status',return_value=self.state),patch.object(novelai,'request',side_effect=self.send_image) as send:
            first=novelai.generate('test',None,'trial-one',p['revision'],preview=options)
            again=novelai.generate('test',None,'trial-one',p['revision'],preview=options)
            self.assertEqual(first,again);self.assertEqual(send.call_count,1)
            with self.assertRaises(db.StudioError): novelai.generate('test',None,'trial-one',preview={**options,'expression':'crying'})
        self.assertEqual(db.load('test'),p)
        self.assertEqual(first['actual_anlas'],0)
        self.assertEqual(profiles.history('test')[0]['id'],'trial-one')
        snapshot=db.read_json(db.asset_path('test',first['image']).with_suffix('.plan.json'))
        self.assertEqual(snapshot['entry'],p['characters'][0])
        payload=profiles.preview(p,dict(kind='location',id='shop'))['request']
        self.assertIn('no humans',payload['input']);self.assertFalse(payload['parameters']['v4_prompt']['caption']['char_captions'])
        self.assertEqual(profiles.preview({**p,'pages':[]},options)['request'],profiles.preview(p,options)['request'])
        p['characters'][0]['outfits'].append(dict(id='draft',name='未入力',prompt='',prompt_ja=''))
        profiles.preview(p,options)  # Other draft outfits do not block this selected complete outfit.
        with self.assertRaisesRegex(db.StudioError,'選択した衣装'):profiles.preview(p,{**options,'outfit_id':'draft'})

    def test_trial_unknown_and_cost_guards(self):
        p=self.prepared();options=dict(kind='character',id='mio')
        with patch.object(novelai,'status',return_value={**self.state,'tier':2}),patch.object(novelai,'request') as send:
            with self.assertRaisesRegex(db.StudioError,'Opus'): novelai.generate('test',None,'blocked',preview=options)
            send.assert_not_called()
        with patch.object(novelai,'status',return_value=self.state),patch.object(novelai,'request',side_effect=TimeoutError) as send:
            with self.assertRaises(TimeoutError): novelai.generate('test',None,'unknown',preview=options)
            self.assertEqual(novelai.generate('test',None,'unknown',preview=options)['status'],'unknown')
            with self.assertRaisesRegex(db.StudioError,'未確認'): novelai.generate('test',None,'next',preview=options)
            self.assertEqual(send.call_count,1)
        self.assertEqual(db.load('test'),p)

    def test_unused_outfit_does_not_stale_and_legacy_unchanged(self):
        p=self.prepared();panel=db.panels(p)[0]
        with patch.object(novelai,'status',return_value=self.state),patch.object(novelai,'request',side_effect=self.send_image):
            novelai.generate('test',panel['id'],'panel-one')
        p=db.load('test');v=workspace.editable(p);v['characters'][0]['outfits'][0]['prompt']='yellow jacket'
        p=workspace.save('test',p['revision'],v);self.assertFalse(novelai.is_stale(p,panel=db.panels(p)[0]))
        v=workspace.editable(p);v['characters'][0]['outfits'][1]['prompt']='purple dress'
        p=workspace.save('test',p['revision'],v);self.assertTrue(novelai.is_stale(p,panel=db.panels(p)[0]))
        self.assertEqual(profiles.caption(dict(id='old',name='旧',appearance='short hair, red coat'),dict(id='old',prompt='sitting')),'short hair, red coat, sitting')
        self.assertEqual(profiles.action_prompt('looking at viewer, gentle smile','crying'),'looking at viewer')
        self.assertEqual(profiles.action_prompt('looking at viewer, gentle smile','custom'),'looking at viewer, gentle smile')

    def test_wardrobe_preserves_identity_and_bounds_attempts(self):
        p=self.prepared();c=p['characters'][0];value={k:copy.deepcopy(c[k]) for k in ('appearance','appearance_ja','outfits','default_outfit_id')}
        def valid(*args):return dict(role='assistant',tool_calls=[dict(function=dict(name='submit_result',arguments=json.dumps(value)))]),{}
        result=strata_tasks.wardrobe('test',p['revision'],'mio','外出着',valid)
        for key in ('id','name','voice','voice_ja'):self.assertEqual(c[key],result['characters'][0][key])
        calls=[]
        def bad(*args): calls.append(1);return dict(role='assistant',content='loop'),{}
        with self.assertRaises(db.StudioError):strata_tasks.wardrobe('test',result['revision'],'mio','',bad)
        self.assertEqual(len(calls),2)

if __name__=='__main__':unittest.main()
