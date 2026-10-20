"""Isolated HTTP + Firefox regression. Synthetic images only, no production AI/API calls.
Run with PYTHONPATH=.runtime/test-deps and PLAYWRIGHT_BROWSERS_PATH=.runtime/browsers.
Keeps screenshots and fixtures under .runtime/ui-browser-<id> for inspection.
"""
import copy
import json
from pathlib import Path
import shutil
import sys
import threading
import uuid
from unittest.mock import patch
from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from studio import project as db, server, novelai, qwen, rendering, workspace, wholepage

run = ROOT / '.runtime' / ('ui-browser-' + uuid.uuid4().hex[:8]); run.mkdir()
patches = [patch.object(db, 'WORK', run / 'work'),
           patch.object(novelai, 'ledger_path', lambda: run / 'ledger.json'),
           patch.object(qwen, 'ledger_path', lambda: run / 'qwen-ledger.json'),
           patch.object(workspace, 'system_defaults', return_value=dict(workspace.DEFAULT_SYSTEM)),
           patch.object(server, 'start_job', side_effect=AssertionError('Unexpected AI job'))]
for p in patches: p.start()

def fixture(pid, pages=3, mode='panels'):
    db.create(pid, '作品 ' + pid, '## 章\nテスト原作')
    c = dict(id='p1', scene='色見本', prompt='colored stripes', characters=[], dialogue=[], crop=[.5,.5])
    board = dict(output_method='novelai', title='作品 ' + pid, style='illustration', characters=[], pages=[dict(template='splash', panels=[dict(c,id='p'+str(n))]) for n in range(1,pages+1)])
    p = db.import_board(pid, 0, board)
    folder = db.directory(pid) / 'images'; folder.mkdir()
    im = Image.new('RGB', (600,400), '#478c74'); ImageDraw.Draw(im).rectangle((300,0,599,399),fill='#e1ad63'); im.save(folder/'art.png')
    def fill(p):
        p['render_mode'] = mode
        for page in p['pages']:
            for panel in page['panels']: panel['image']='images/art.png'
    db.mutate(pid, p['revision'], fill)
    rendering.compose(pid)

fixture('alpha'); fixture('beta',2); fixture('whole',1,'whole')
fixture('incoming',1)
external = run / 'outside-work'
db.directory('incoming').rename(external)
http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
worker = threading.Thread(target=http.serve_forever, daemon=True); worker.start()
errors=[]
try:
    with sync_playwright() as engine:
        browser=engine.firefox.launch(headless=True)
        page=browser.new_page(viewport={'width':1500,'height':1100})
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.goto(f'http://127.0.0.1:{http.server_port}', wait_until='networkidle')
        def ready(): expect(page.locator('#refresh')).to_be_enabled(timeout=30000)
        def choose(pid):
            page.locator('#projects .project-card').filter(has=page.locator('small',has_text=' · '+pid)).click()
            expect(page.locator('#title')).to_have_text('作品 '+pid); ready()
        choose('alpha'); page.locator('#page-select').select_option('2'); ready()
        choose('beta'); expect(page.locator('#page-select')).to_have_value('0')
        page.locator('#page-select').select_option('1'); ready()
        choose('alpha'); expect(page.locator('#page-select')).to_have_value('2')
        page.reload(wait_until='networkidle'); choose('beta'); expect(page.locator('#page-select')).to_have_value('1')
        choose('alpha'); expect(page.locator('#page-select')).to_have_value('2')
        page.locator('#tab-btn-page').click()
        before=db.load('alpha'); other=db.directory('alpha')/'output/page-001.png'; mtime=other.stat().st_mtime_ns
        page.locator('#panel-image').dblclick(); expect(page.locator('#position-dialog')).to_be_visible(); ready()
        page.locator('#position-x').fill('20'); page.locator('#position-cancel').click()
        assert db.load('alpha')==before
        page.locator('#adjust-position').click(); expect(page.locator('#position-dialog')).to_be_visible(); ready()
        box=page.locator('#position-canvas').bounding_box()
        page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2);page.mouse.down();page.mouse.move(box['x']+box['width']/2+30,box['y']+box['height']/2+20);page.mouse.up()
        assert float(page.locator('#position-x').input_value())>0
        page.locator('#position-contain').click(); assert int(page.locator('#position-scale').input_value())<100
        page.locator('#position-x').fill('25'); page.locator('#position-y').fill('0')
        page.locator('#position-scale').evaluate("el=>{el.value='30';el.dispatchEvent(new Event('input',{bubbles:true}));}")
        page.screenshot(path=str(run/'position-desktop.png'))
        page.locator('#position-save').click()
        expect(page.locator('#position-dialog')).not_to_be_visible()
        expect(page.locator('#status')).to_contain_text('PAGE 3 をPNGで組版しました',timeout=30000)
        assert db.load('alpha')['pages'][2]['panels'][0]['image_transform']==dict(x=.25,y=0,scale=.3)
        assert other.stat().st_mtime_ns==mtime
        page.screenshot(path=str(run/'composed-desktop.png'))
        page.locator('#adjust-position').click();ready(); expect(page.locator('#position-x')).to_have_value('25.0')
        stale=db.load('alpha');db.edit_panel('alpha',stale['revision'],'p3',{'scene':'another editor'})
        page.locator('#position-save').click();expect(page.locator('#position-error')).to_contain_text('競合');ready()
        expect(page.locator('#position-dialog')).to_be_visible();page.locator('#position-cancel').click()
        page.locator('#refresh').click();ready()
        # Narrow viewport: dialog and canvas stay inside it.
        page.set_viewport_size({'width':390,'height':844})
        page.locator('#adjust-position').click();ready()
        assert page.locator('#position-dialog').bounding_box()['width']<=390
        assert page.locator('#position-canvas').bounding_box()['width']<390
        page.screenshot(path=str(run/'position-mobile.png'))
        page.locator('#position-cancel').click();page.set_viewport_size({'width':1500,'height':1100})
        # A compose failure must close the dialog, retain the saved placement, and say it was saved.
        page.route('**/api/compose-page', lambda route: route.fulfill(status=400, content_type='application/json', body=json.dumps({'error':'test compose failure'})))
        page.locator('#adjust-position').click();ready();page.locator('#position-x').fill('15');page.locator('#position-save').click()
        expect(page.locator('#status')).to_contain_text('位置は保存済みですがPNG組版に失敗しました');ready()
        expect(page.locator('#position-dialog')).not_to_be_visible()
        assert db.load('alpha')['pages'][2]['panels'][0]['image_transform']['x']==.15
        page.unroute('**/api/compose-page');rendering.compose('alpha',page_number=3)
        choose('whole');page.locator('#tab-btn-page').click();expect(page.locator('#adjust-position')).to_be_hidden()
        page.locator('#panel-image').dblclick();ready();expect(page.locator('#position-dialog')).not_to_be_visible()
        choose('alpha')
        page.locator('#manage-library').click();expect(page.locator('#library-dialog')).to_be_visible();ready()
        row=lambda pid:page.locator(f'.library-manager-row[data-project-id="{pid}"]')
        row('alpha').get_by_role('button',name='除外…',exact=True).click()
        expect(page.locator('#library-exclude-go')).to_be_disabled()
        expect(page.locator('#library-exclude')).to_contain_text('一覧に戻す')
        page.screenshot(path=str(run/'library-desktop.png'))
        page.locator('#library-confirm').check();expect(page.locator('#library-exclude-go')).to_be_enabled()
        page.locator('#library-exclude-go').click();expect(page.locator('#library-message')).to_contain_text('除外しました');ready()
        assert db.directory('alpha').is_dir()
        expect(page.locator('#projects .project-card').filter(has_text='alpha')).to_have_count(0)
        row('alpha').get_by_role('button',name='一覧に戻す',exact=True).click();expect(page.locator('#library-message')).to_contain_text('一覧に戻しました');ready()
        row('beta').get_by_role('button',name='除外…',exact=True).click();expect(page.locator('#library-confirm')).not_to_be_checked();expect(page.locator('#library-exclude-go')).to_be_disabled()
        page.locator('#library-refresh').click();ready();expect(page.locator('#library-exclude-go')).to_be_disabled()
        page.locator('#library-path').fill(str(external));page.locator('#library-import').click()
        expect(page.locator('#library-message')).to_contain_text('コピーして読み込みました');ready()
        assert db.directory('incoming').is_dir() and external.is_dir()
        page.locator('#library-import').click();expect(page.locator('#library-message')).to_contain_text('同じID');ready()
        page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(run/'library-mobile.png'))
        assert page.locator('#library-dialog').bounding_box()['width']<=390
        page.locator('#library-close').click();page.set_viewport_size({'width':1500,'height':1100})
        choose('alpha');expect(page.locator('#page-select')).to_have_value('2')
        choose('beta')
        p=db.load('alpha');db.mutate('alpha',p['revision'],lambda p:p.update(pages=p['pages'][:1]))
        choose('alpha');expect(page.locator('#page-select')).to_have_value('0')
        # Excluding the last visible work leaves a usable empty workspace; restoring works again.
        page.locator('#manage-library').click();ready()
        for pid in ['beta','whole','incoming','alpha']:
            row(pid).get_by_role('button',name='除外…',exact=True).click();page.locator('#library-confirm').check();page.locator('#library-exclude-go').click();ready()
        expect(page.locator('#projects .project-card')).to_have_count(0)
        row('alpha').get_by_role('button',name='一覧に戻す',exact=True).click();ready();page.locator('#library-close').click()
        choose('alpha');expect(page.locator('#studio')).to_be_visible()
        assert not errors, errors
        browser.close()
    print(json.dumps(dict(ok=True, screenshots=str(run), errors=errors),ensure_ascii=False))
finally:
    http.shutdown();http.server_close();worker.join()
    for p in reversed(patches): p.stop()
