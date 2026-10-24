"""Isolated A import editor regression; no AI, image generation or real work changes."""
import json
from pathlib import Path
import sys
import threading
import uuid
from unittest.mock import patch
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from test_a_import import AImportTests
from studio import project as db, server, workspace, novelai, qwen

run=ROOT/'.runtime'/('a-import-browser-'+uuid.uuid4().hex[:8]);run.mkdir(parents=True)
fixture=AImportTests();fixture.setUp();fixture.network.stop()
patches=[patch.object(server,'start_job',side_effect=AssertionError('No generation jobs')),
         patch.object(workspace,'system_defaults',return_value=dict(workspace.DEFAULT_SYSTEM)),
         patch.object(novelai,'ledger_path',lambda:run/'ledger.json'),
         patch.object(qwen,'ledger_path',lambda:run/'qwen-ledger.json')]
for p in patches:p.start()
http=None
try:
    fixture.apply()
    db.create('qwen-view','Qwen check','Test source','qwen')
    db.import_board('qwen-view',0,dict(output_method='qwen',title='Qwen check',style='illustration',characters=[],pages=[dict(template='splash',panels=[dict(id='q1',scene='Test',prompt='landscape',characters=[],dialogue=[])])]))
    http=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
    threading.Thread(target=http.serve_forever,daemon=True).start()
    errors=[]
    with sync_playwright() as engine:
        browser=engine.firefox.launch(headless=True)
        page=browser.new_page(viewport={'width':1500,'height':1100})
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(f'http://127.0.0.1:{http.server_port}',wait_until='networkidle')
        def choose(pid):
            page.locator('#projects .project-card').filter(has=page.locator('small',has_text=' · '+pid)).click()
            expect(page.locator('#refresh')).to_be_enabled(timeout=30000)
            page.locator('#tab-btn-page').click()
        choose('test')
        page.locator('#novelai-prompt-options summary').click()
        expect(page.locator('#prompt-mode')).to_have_value('literal')
        expect(page.locator('#condition-use_coords')).not_to_be_checked()
        neg=page.locator('#characters [data-field="negative"]')
        expect(neg).to_have_value('glasses')
        neg.fill('glasses, hat')
        page.locator('#characters [data-field="negative_ja"]').fill('眼鏡、帽子')
        page.locator('#condition-use_order').uncheck()
        page.locator('#save-all').click()
        expect(page.locator('#status')).to_contain_text('未保存の変更をすべて保存しました',timeout=30000)
        expect(page.locator('#refresh')).to_be_enabled(timeout=30000)
        expect(page.locator('#save-all')).to_be_disabled(timeout=30000)
        saved=db.load('test');panel=db.panels(saved)[0]
        assert panel['characters'][0]['negative']=='glasses, hat'
        assert panel['characters'][0]['negative_ja']=='眼鏡、帽子'
        assert panel['conditioning']['use_order'] is False
        assert saved['import_state']
        page.locator('details').filter(has=page.locator('#show-request')).locator('summary').click()
        page.locator('#show-request').click()
        expect(page.locator('#request-preview')).to_contain_text('glasses, hat',timeout=30000)
        sent=json.loads(page.locator('#request-preview').inner_text())
        assert sent['input']==panel['prompt']
        assert sent['parameters']['v4_prompt']['use_order'] is False
        assert sent['parameters']['v4_negative_prompt']['caption']['char_captions'][0]['char_caption']=='glasses, hat'
        page.locator('#novelai-prompt-options').scroll_into_view_if_needed()
        page.screenshot(path=str(run/'editor-desktop.png'))
        page.reload(wait_until='networkidle');choose('test')
        expect(page.locator('#characters [data-field="negative"]')).to_have_value('glasses, hat')
        page.set_viewport_size({'width':390,'height':844})
        page.locator('#novelai-prompt-options summary').click()
        page.locator('#novelai-prompt-options').scroll_into_view_if_needed()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(run/'editor-mobile.png'))
        page.set_viewport_size({'width':1500,'height':1100});choose('qwen-view')
        expect(page.locator('#novelai-prompt-options')).to_be_hidden()
        expect(page.locator('#characters [data-field="negative"]')).to_have_count(0)
        assert not errors,errors
        browser.close()
    print(json.dumps(dict(ok=True,screenshots=str(run),checks=['save-reload','literal-request','actor-negative','conditioning','provenance-retained','mobile-width','qwen-hidden']),ensure_ascii=False))
finally:
    if http:http.shutdown();http.server_close()
    for p in reversed(patches):p.stop()
    fixture.tearDown()
