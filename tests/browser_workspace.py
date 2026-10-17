"""Real UI/Strata translation and review; synthetic images for export, no image API."""
import json,sys,uuid,zipfile,shutil
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from studio import project as db,novelai

pid='ui-workspace-'+uuid.uuid4().hex[:8]; title='編集ワークスペース試験'
errors=[];saved=[]
with sync_playwright() as engine:
    browser=engine.firefox.launch(headless=True)
    page=browser.new_page(viewport={'width':1500,'height':1100},accept_downloads=True)
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.goto('http://127.0.0.1:8765',wait_until='networkidle')
    page.locator('#new-project').click()
    page.locator('#new-title').fill(title);page.locator('#new-id').fill(pid)
    page.locator('#new-source').fill('成人の澪と蓮が雨の町で傘を持って話す。澪は落ち着いた丁寧語。')
    page.get_by_role('button',name='作品を作成（まだ生成しません）',exact=True).click()
    expect(page.locator('#title')).to_have_text(title);expect(page.locator('#refresh')).to_be_enabled()
    expect(page.locator('#status')).to_contain_text('原作を保存しました')
    board=db.read_json(ROOT/'templates/board.example.json');board['title']=title
    page.locator('#import-section summary').click();page.locator('#import-json').fill(json.dumps(board,ensure_ascii=False));page.locator('#import-board').click()
    expect(page.locator('#status')).to_have_text('ページを組み直しました',timeout=30000)
    page.locator('#style-preset').select_option('style-02')
    expect(page.locator('#refresh')).to_be_enabled()
    page.locator('#catalog-details > summary').click();page.locator('.catalog-edit').first.locator(':scope > summary').click()
    appearance=page.locator('[data-config-path="characters.0.appearance"]');appearance.fill('adult woman, short blonde hair, blue coat')
    page.locator('[data-config-path="characters.0.voice_ja"]').fill('穏やかな丁寧語。「です」「ます」を使う。')
    page.locator('.catalog-edit').first.get_by_role('button',name='英語 → 日本語',exact=True).first.click()
    expect(page.locator('#status')).to_have_text('翻訳を入力欄へ反映しました。内容を確認して保存してください',timeout=180000)
    assert page.locator('[data-config-path="characters.0.appearance_ja"]').input_value()
    page.locator('#save-common').click();expect(page.locator('#status')).to_have_text('作品の設定を保存しました')
    assert db.load(pid)['characters'][0]['appearance']=='adult woman, short blonde hair, blue coat'
    page.locator('#tab-btn-page').click();page.locator('#layout-details summary').click();page.locator('#layout-select').select_option('P4-211');page.locator('#apply-layout').click()
    expect(page.locator('#status')).to_contain_text('コマ枠を変更しました')
    assert len(db.load(pid)['pages'][0]['panels'])==4
    page.locator('#panels button').last.click();expect(page.locator('#refresh')).to_be_enabled()
    page.locator('#scene').fill('窓辺で雨を見る')
    page.locator('#prompt-ja').fill('窓辺、雨の町並み、静かな室内')
    page.locator('#panel-prompt-ja').get_by_role('button',name='日本語 → 英語',exact=True).click()
    expect(page.locator('#status')).to_have_text('翻訳を入力欄へ反映しました。内容を確認して保存してください',timeout=180000)
    assert page.locator('#prompt').input_value()
    page.locator('#save-panel').click();expect(page.locator('#status')).to_have_text('ページを組み直しました',timeout=30000)
    page.locator('#tab-btn-work').click();page.locator('#produce-all').click()
    expect(page.locator('#ignore-warnings')).to_be_visible(timeout=180000)
    assert not any(r['project_id']==pid for r in novelai.ledger()['requests'].values())
    # Deterministic placeholder art exercises backend archives without spending Anlas.
    p=db.load(pid);folder=db.directory(pid)/'images';folder.mkdir(exist_ok=True)
    for i,c in enumerate(db.panels(p)):
        target=folder/(c['id']+'.png');Image.new('RGB',(1216,832),['#dbe8db','#b2d0e5','#e5d0b2','#d5c2dc'][i]).save(target)
    p=db.mutate(pid,p['revision'],lambda x:[c.update(image='images/'+c['id']+'.png') for c in db.panels(x)])
    page.locator('#refresh').click();expect(page.locator('#status')).to_have_text('最新の保存内容を読み込みました')
    out=ROOT/'.runtime/browser-export';out.mkdir(exist_ok=True)
    for kind,text in [('png','yes'),('png','no'),('psd','yes')]:
        page.locator('#export-format').select_option(kind);page.locator('#export-text').select_option(text)
        with page.expect_download(timeout=60000) as download:
            page.locator('#export-pages').click()
        file=out/(kind+'-'+text+'.zip');download.value.save_as(file);saved.append(file.name)
        with zipfile.ZipFile(file) as z:
            assert 'dialogue.md' in z.namelist()
            assert json.loads(z.read('manifest.json'))['include_text']==(text=='yes')
            if kind=='psd':assert 'page-001.psd' in z.namelist()
        expect(page.locator('#refresh')).to_be_enabled()
    for selector,scope in [('#export-system','system'),('#export-settings','all')]:
        with page.expect_download() as download:page.locator(selector).click()
        file=out/(scope+'.json');download.value.save_as(file)
        data=json.loads(file.read_text(encoding='utf-8'));assert ('system' in data)==(scope=='system')
        expect(page.locator('#refresh')).to_be_enabled()
    page.locator('#import-settings-file').set_input_files(out/'system.json');expect(page.locator('#status')).to_have_text('設定JSONを取り込みました')
    page.screenshot(path=str(ROOT/'.runtime/ui-workspace-test.png'),full_page=True)
    assert not errors,errors
    browser.close()
assert db.directory(pid).resolve().is_relative_to((ROOT/'work').resolve()) and pid.startswith('ui-workspace-')
shutil.rmtree(db.directory(pid))
print(json.dumps(dict(status='passed',translation_directions=['en-ja','ja-en'],warning_gate=True,downloads=saved,image_api_requests=0,console_errors=errors)))
