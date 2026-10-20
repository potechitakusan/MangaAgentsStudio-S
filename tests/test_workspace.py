import copy
import io
import json
import zipfile
import unittest
from unittest.mock import patch
from PIL import Image,ImageChops
from psd_tools import PSDImage
from studio import project as db, workspace, strata_tasks, novelai, production, exporting, layouts
from studio.compose import compose
import test_studio as baseline

class WorkspaceTests(unittest.TestCase):
    setUp=baseline.StudioTests.setUp
    tearDown=baseline.StudioTests.tearDown

    def upgrade(self):
        p=db.load('test'); v=workspace.editable(p);v['text_mode']='overlay'
        return workspace.save('test',p['revision'],v)

    def images(self):
        p=self.upgrade()
        def send(endpoint,payload):
            out=io.BytesIO(); Image.new('RGB',(payload['parameters']['width'],payload['parameters']['height']),'#5874b0').save(out,'PNG');return out.getvalue()
        with patch.object(novelai,'status',return_value=self.state),patch.object(novelai,'request',side_effect=send):
            for i,panel in enumerate(db.panels(p)): novelai.generate('test',panel['id'],'image-'+str(i))
        return db.load('test')

    def test_project_list_is_newest_first(self):
        for pid,stamp in (('old-one','2031-10-01T00:00:00+00:00'),('new-one','2031-10-16T00:00:00+00:00'),('mid-one','2031-10-10T00:00:00+00:00')):
            with patch.object(db,'now',return_value=stamp): db.create(pid,pid,'原作')
        ids=[x['id'] for x in db.list_projects()]
        self.assertEqual(ids[:3],['new-one','mid-one','old-one'])   # created_at, newest first
        self.assertEqual(ids[3],'test')   # the fixture work was created now, i.e. before these future dates
        self.assertEqual(len(ids),4)

    def test_style_presets_exclude_the_opposite_colour_mode(self):
        for style in workspace.STYLE_PRESETS:
            positive,negative=style['style_prompt'].lower(),style['negative_style'].lower()
            with self.subTest(style=style['id']):
                if style['id']=='style-07':
                    self.assertEqual(negative,'')   # no colour direction at all: nothing to exclude
                elif 'monochrome' in positive:
                    self.assertTrue('color' in negative and 'colored' in negative and 'monochrome' not in negative)
                    self.assertIn('カラー',style['negative_style_ja'])
                else:
                    self.assertTrue('monochrome' in negative and 'greyscale' in negative)
                    self.assertIn('モノクロ',style['negative_style_ja'])

    def test_common_edits_mark_images_stale_but_keep_candidates(self):
        p=self.images(); self.assertFalse(novelai.is_stale(p,panel=db.panels(p)[0]))
        old=copy.deepcopy(db.panels(p)[0]);v=workspace.editable(p)
        v['characters'][0]['appearance']='long purple hair, white coat';v['characters'][0]['voice_ja']='丁寧語';v['style']='watercolor painting'
        p=workspace.save('test',p['revision'],v)
        self.assertTrue(novelai.is_stale(p,panel=db.panels(p)[0]))
        self.assertEqual(db.panels(p)[0]['image'],old['image']);self.assertEqual(db.panels(p)[0]['candidates'],old['candidates'])
        self.assertIn('long purple hair',novelai.build_request(p,db.panels(p)[0])['parameters']['v4_prompt']['caption']['char_captions'][0]['char_caption'])

    def test_layout_resize_polygon_and_archived_content(self):
        p=self.images(); old=copy.deepcopy(p['pages'][0]['panels'])
        p=workspace.resize_page('test',p['revision'],1,'P4-211')
        self.assertEqual(p['pages'][0]['panels'][:3],old)
        self.assertTrue(p['pages'][0]['panels'][3]['needs_edit'])
        self.assertTrue(any('未入力' in x['message'] for x in strata_tasks.local_warnings(p)))
        p=workspace.resize_page('test',p['revision'],1,'P3-12-lean-right')
        manifest=compose('test'); poly=manifest['pages'][0]['panels'][1]['polygon']
        self.assertGreater(len({x for x,y in poly}),2)
        self.assertEqual(len(layouts.IMPORTED),79)
        self.assertTrue((db.directory('test')/'history'/f'project-{p["revision"]-1:05}.json').exists())

    def test_config_roundtrip_system_and_paths(self):
        p=self.upgrade();v=workspace.editable(p);v['system']['review']='確認を日本語で';v['ui_inputs']={'agent-instruction':'表情を変える'}
        p=workspace.save('test',p['revision'],v); exported=workspace.config_export('test','all')
        db.create('imported','取込','仮の原作');p2=workspace.config_import('imported',0,exported)
        self.assertEqual(workspace.editable(p2),workspace.editable(p))
        self.assertEqual(set(workspace.config_export('test','system')),{'schema','system','output_method'})
        bad=copy.deepcopy(exported);bad['inputs']['pages'][0]['panels'][0]['image']='../../.env'
        with self.assertRaises(db.StudioError): workspace.config_import('imported',p2['revision'],bad)

    def test_translate_preserves_ids_and_limits_retries(self):
        def transport(messages,tools,timeout):
            return dict(role='assistant',tool_calls=[dict(id='one',function=dict(name='submit_result',arguments=json.dumps({'items':[{'id':'a','text':'白い帽子'}]})))]),{}
        self.assertEqual(strata_tasks.translate('test',[{'id':'a','text':'white hat'}],'ja',transport=transport)[0]['text'],'白い帽子')
        calls=[]
        def bad(*args):calls.append(1);return dict(role='assistant',content='again'),{}
        with self.assertRaises(db.StudioError): strata_tasks.translate('test',[{'id':'a','text':'x'}],'ja',transport=bad)
        self.assertEqual(len(calls),2)

    def test_review_warning_gate_and_stale_review(self):
        p=self.upgrade(); report=dict(id='review',complete=True,fingerprint=workspace.fingerprint(p),revision=p['revision'],warnings=[dict(path='style',message='確認')])
        db.dump(db.directory('test')/'review.json',report)
        with patch.object(novelai,'generate') as generate:
            result=production.produce('test',p['revision'],'batch');self.assertEqual(result['stage'],'warnings');generate.assert_not_called()
        v=workspace.editable(p);v['system']['constraints']+='更新';p=workspace.save('test',p['revision'],v)
        fresh={**report,'fingerprint':workspace.fingerprint(p)}
        with patch.object(strata_tasks,'review',return_value=fresh) as review:
            production.produce('test',p['revision'],'batch2');review.assert_called_once()

    def two_pages(self):
        p=self.upgrade();v=workspace.editable(p)
        v['pages'].append(copy.deepcopy(v['pages'][0]))
        v['pages'][1]['id']='page-two'
        for i,c in enumerate(v['pages'][1]['panels']): c['id']=f'q2-{i+1}'
        return workspace.save('test',p['revision'],v)

    def test_page_fingerprint_follows_only_its_page_and_the_common_settings(self):
        p=self.two_pages();one=workspace.fingerprint(p,[1]);whole=workspace.fingerprint(p)
        v=workspace.editable(p);v['pages'][1]['panels'][0]['scene']='別のページの変更';q=workspace.save('test',p['revision'],v)
        self.assertEqual(workspace.fingerprint(q,[1]),one)
        self.assertNotEqual(workspace.fingerprint(q,[2]),workspace.fingerprint(p,[2]))
        self.assertNotEqual(workspace.fingerprint(q),whole)
        v=workspace.editable(q);v['style']='watercolor painting';r=workspace.save('test',q['revision'],v)
        self.assertNotEqual(workspace.fingerprint(r,[1]),one)    # common settings count for every page

    def test_page_scope_reviews_only_that_page_and_ignores_other_pages_warnings(self):
        p=self.two_pages()
        def checked(pages):
            seen=[]
            def transport(messages,tools,timeout):
                seen.append(json.loads(messages[-1]['content'])['page_number'] if 'page_number' in messages[-1]['content'] else None)
                return dict(role='assistant',tool_calls=[dict(id='r',function=dict(name='submit_result',arguments=json.dumps({'warnings':[]})))]),{}
            report=strata_tasks.review('test',transport=transport,pages=pages);return report,seen
        report,seen=checked([2])
        self.assertEqual(report['pages'],[2]);self.assertEqual(len(seen),1)
        self.assertTrue((db.directory('test')/'review-page-2.json').exists());self.assertFalse((db.directory('test')/'review.json').exists())
        full,seen_all=checked(None)
        self.assertEqual(len(seen_all),2);self.assertTrue((db.directory('test')/'review.json').exists())
        with self.assertRaises(db.StudioError): strata_tasks.review('test',pages=[3])
        mixed=dict(warnings=[dict(path='style',message='共通'),dict(path='pages/0/panels/0',message='1ページ目'),dict(path='x',message='2ページ目',page=2)])
        self.assertEqual([w['message'] for w in strata_tasks.scoped_warnings(mixed,[2])],['共通','2ページ目'])

    def test_generating_one_page_ignores_warnings_of_other_pages_and_uses_a_valid_full_review(self):
        p=self.two_pages()
        full=dict(id='full',complete=True,fingerprint=workspace.fingerprint(p),revision=p['revision'],
                  warnings=[dict(path='pages/0/panels/0',message='1ページ目の警告',page=1,source='Strata')])
        db.dump(db.directory('test')/'review.json',full)
        with patch.object(strata_tasks,'review') as review,patch.object(novelai,'generate') as generate,patch.object(production,'compose'):
            generate.return_value=dict(status='complete',id='img')
            blocked=production.produce('test',p['revision'],'b1',scope='page',page_number=1)
            self.assertEqual(blocked['stage'],'warnings');self.assertEqual([w['page'] for w in blocked['review']['warnings']],[1])
            generate.assert_not_called()
            other=production.produce('test',p['revision'],'b2',scope='page',page_number=2,regenerate=False)   # page 2 has no warning of its own
            self.assertEqual(other['stage'],'complete')
            review.assert_not_called()                                                                  # the full review was reused
            # an edit on page 2 invalidates the full review but not page 1's own check
            v=workspace.editable(p);v['pages'][1]['panels'][0]['scene']='編集';q=workspace.save('test',p['revision'],v)
            review.return_value=dict(id='r',complete=True,fingerprint=workspace.fingerprint(q,[2]),revision=q['revision'],warnings=[],pages=[2])
            production.produce('test',q['revision'],'b3',scope='page',page_number=2)
            review.assert_called_once();self.assertEqual(review.call_args.kwargs['pages'],[2])

    def test_batch_halts_on_concurrent_input_edit(self):
        p=self.upgrade();report=dict(id='review',complete=True,fingerprint=workspace.fingerprint(p),revision=p['revision'],warnings=[])
        db.dump(db.directory('test')/'review.json',report)
        def send(endpoint,payload):
            current=db.load('test');workspace.save('test',current['revision'],{'title':'別の編集'})
            image=io.BytesIO();Image.new('RGB',(payload['parameters']['width'],payload['parameters']['height']),'white').save(image,'PNG');return image.getvalue()
        with patch.object(novelai,'status',return_value=self.state),patch.object(novelai,'request',side_effect=send) as request,patch.object(production.time,'sleep'):
            with self.assertRaisesRegex(db.StudioError,'入力が変更'): production.produce('test',p['revision'],'concurrent')
        self.assertEqual(request.call_count,1);self.assertEqual(db.load('test')['title'],'別の編集')
        self.assertFalse(db.panels(db.load('test'))[0].get('image'));self.assertEqual(len(db.panels(db.load('test'))[0]['candidates']),1)

    def test_backend_png_psd_and_dialogue_exports(self):
        p=self.images()
        with_text=exporting.bundle('test','png',True);without_text=exporting.bundle('test','png',False);psd=exporting.bundle('test','psd',True)
        for result in (with_text,without_text,psd):
            with zipfile.ZipFile(db.directory('test')/result['path']) as z:
                self.assertIn('dialogue.md',z.namelist());self.assertIn('まだ降ってる',z.read('dialogue.md').decode('utf-8'))
        with zipfile.ZipFile(db.directory('test')/with_text['path']) as z: a=Image.open(io.BytesIO(z.read('page-001.png'))).copy()
        with zipfile.ZipFile(db.directory('test')/without_text['path']) as z: b=Image.open(io.BytesIO(z.read('page-001.png'))).copy()
        self.assertIsNotNone(ImageChops.difference(a,b).getbbox())
        with zipfile.ZipFile(db.directory('test')/psd['path']) as z:
            image=PSDImage.open(io.BytesIO(z.read('page-001.psd')))
            self.assertTrue(any('台詞' in layer.name for layer in image.descendants()))
            report=json.loads(z.read('manifest.json'));self.assertLessEqual(report['pages'][0]['psd_verification']['composite_max_difference'],1)

    def test_embedded_text_cannot_be_falsely_exported_without_text(self):
        p=self.upgrade();folder=db.directory('test')/'images';folder.mkdir(exist_ok=True);Image.new('RGB',(832,1216),'white').save(folder/'whole.png')
        p=db.mutate('test',p['revision'],lambda x:(x.update(render_mode='whole'),x['pages'][0].setdefault('whole',{}).update(image='images/whole.png',generated_text_mode='embedded')))
        with self.assertRaisesRegex(db.StudioError,'焼き込まれ'): exporting.bundle('test','png',False)
        p=db.mutate('test',p['revision'],lambda x:x['pages'][0]['whole'].update(generated_text_mode='overlay'))
        result=exporting.bundle('test','png',False);self.assertTrue((db.directory('test')/result['path']).exists())

    def test_fixed_seed_change_requires_regeneration(self):
        p=self.images();panel=db.panels(p)[0]
        request=db.read_json(db.asset_path('test',panel['image']).with_suffix('.request.json'))
        panel['seed']=request['parameters']['seed']
        self.assertFalse(novelai.is_stale(p,panel=panel))
        panel['seed']=(panel['seed']+1)%4294967296
        self.assertTrue(novelai.is_stale(p,panel=panel))

    def test_new_workspace_board_stays_identical_on_later_save(self):
        p=self.upgrade();old=copy.deepcopy(p['pages'][0]);board=copy.deepcopy(self.board)
        board['characters']=[];board['locations']=[];board['props']=[];board.pop('style',None)
        p=db.append_board('test',p['revision'],board)
        first=copy.deepcopy(p['pages'][0]);added=copy.deepcopy(p['pages'][1])
        p=workspace.save('test',p['revision'],workspace.editable(p))
        self.assertEqual(first,p['pages'][0]);self.assertEqual(added,p['pages'][1])

    def test_agent_rejects_missing_japanese_before_saving(self):
        from studio.agent import bilingual_board
        with self.assertRaisesRegex(db.StudioError,'日本語訳'):bilingual_board(dict(pages=[dict(panels=[dict(prompt='blue sky',characters=[])])]))
        bilingual_board(dict(pages=[dict(panels=[dict(prompt='blue sky',prompt_ja='青空',characters=[])])]))

    def test_dialogue_only_production_reuses_art_and_composes(self):
        p=self.images();compose('test');path=db.directory('test')/'output/page-001.png';before=path.read_bytes()
        v=workspace.editable(p);v['pages'][0]['panels'][0]['dialogue']=['ありがとうございます']
        v['pages'][0]['panels'][0]['speakers']=['']
        p=workspace.save('test',p['revision'],v)
        db.dump(db.directory('test')/'review.json',dict(id='review',complete=True,fingerprint=workspace.fingerprint(p),revision=p['revision'],warnings=[]))
        with patch.object(novelai,'generate') as generate:
            result=production.produce('test',p['revision'],'dialogue-only')
            generate.assert_not_called();self.assertEqual(result['generated'],0)
        self.assertNotEqual(path.read_bytes(),before)

    def test_voice_pass_preserves_art_speakers_and_rejects_wrong_ids(self):
        p=self.upgrade();panels=copy.deepcopy(p['pages'][0]['panels'])
        def transport(messages,tools,timeout):
            result={'panels':[{'id':c['id'],'dialogue':['ありがとうございます' for _ in c['dialogue']]} for c in panels if c.get('dialogue')]}
            return dict(role='assistant',tool_calls=[dict(id='voice',function=dict(name='submit_result',arguments=json.dumps(result)))]),{}
        result=strata_tasks.voice_panels('test',p['characters'],panels,transport)
        self.assertEqual([{k:v for k,v in c.items() if k!='dialogue'} for c in result],[{k:v for k,v in c.items() if k!='dialogue'} for c in panels])
        self.assertNotEqual(result,panels)
        def wrong(*args):
            return dict(role='assistant',tool_calls=[dict(id='voice',function=dict(name='submit_result',arguments='{"panels":[]}'))]),{}
        with self.assertRaises(db.StudioError):strata_tasks.voice_panels('test',p['characters'],panels,wrong)

if __name__=='__main__':unittest.main()
