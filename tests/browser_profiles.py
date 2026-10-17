"""Wardrobe UI, bounded real Strata task; --live sends exactly one trial image."""
import argparse,json,sys,uuid,shutil
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from studio import project as db,workspace,novelai
from studio.compose import compose
parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');args=parser.parse_args()
pid='ui-profiles-'+uuid.uuid4().hex[:8];title='衣装UI検証 '+pid
db.create(pid,title,'服は作業着と外出着の2種類。莉奈の固定外見は身体と髪だけ。')
config=workspace.config_export('wardrobe-trial','all');config['inputs']['title']=title
workspace.config_import(pid,0,config);compose(pid)
errors=[]
with sync_playwright() as engine:
    browser=engine.firefox.launch(headless=True)
    page=browser.new_page(viewport={'width':1500,'height':1100})
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto('http://127.0.0.1:8765',wait_until='networkidle')
    page.get_by_role('button',name=title,exact=False).click()
    expect(page.locator('#title')).to_have_text(title);expect(page.locator('#refresh')).to_be_enabled()
    page.locator('#catalog-details > summary').click();page.locator('.catalog-edit').first.locator(':scope > summary').click()
    page.locator('[data-wardrobe-instruction]').first.fill('既存のworkとoutingの2種類を保つ。固定外見から服を除き、衣装の色と形を維持して日英の説明を整える。新しい衣装は増やさない。')
    page.get_by_role('button',name='制作AIで衣装を整理・追加',exact=True).first.click()
    expect(page.locator('#status')).to_contain_text('固定外見と衣装を保存しました',timeout=210000)
    p=db.load(pid);assert len(p['characters'][0]['outfits'])==2
    for count in range(2,10):
        page.locator('.catalog-edit').first.locator(':scope > summary').click() if page.locator('.catalog-edit').first.get_attribute('open') is None else None
        page.get_by_role('button',name='衣装を追加',exact=True).first.click();expect(page.locator('#refresh')).to_be_enabled()
        outfit=page.locator('.outfit-edit').last
        outfit.locator('[data-config-path$=".prompt"]').fill('white jacket, black trousers')
        outfit.locator('[data-config-path$=".prompt_ja"]').fill('白いジャケット、黒いズボン')
    page.get_by_role('button',name='衣装を追加',exact=True).first.click()
    expect(page.locator('#status')).to_contain_text('10種類まで')
    page.locator('#save-common').click();expect(page.locator('#status')).to_have_text('作品の設定を保存しました')
    assert len(db.load(pid)['characters'][0]['outfits'])==10
    page.locator('#tab-btn-page').click()
    page.locator('#characters [data-field="outfit_id"]').first.select_option('outing')
    page.locator('#characters [data-field="expression"]').first.select_option('crying')
    page.locator('#tab-btn-work').click();page.locator('#save-common').click();expect(page.locator('#status')).to_have_text('作品の設定を保存しました')
    actor=db.panels(db.load(pid))[0]['characters'][0];assert actor['outfit_id']=='outing' and actor['expression']=='crying'
    page.locator('#trial-kind').select_option('character');page.locator('#trial-target').select_option('c01')
    page.locator('#trial-outfit').select_option('work');page.locator('#trial-expression').select_option('surprised');page.locator('#trial-framing').select_option('portrait')
    page.locator('#trial-seed').fill('abc');page.locator('#trial-preview').click();expect(page.locator('#status')).to_contain_text('Seedは空欄または')
    page.locator('#trial-seed').fill('421020')
    page.locator('#trial-preview').click();expect(page.locator('#status')).to_contain_text('画像APIは呼んでいません')
    prompt=page.locator('#trial-prompt').input_value();selected_prompt=next(o['prompt'] for o in db.load(pid)['characters'][0]['outfits'] if o['id']=='work')
    assert selected_prompt in prompt and 'surprised' in prompt and 'indigo' not in prompt
    before=len(novelai.ledger()['requests'])
    if args.live:
        pages=db.load(pid)['pages']
        page.locator('#trial-generate').click();expect(page.locator('#status')).to_contain_text('試し描きを保存しました',timeout=210000)
        expect(page.locator('#trial-image')).to_be_visible();assert len(novelai.ledger()['requests'])==before+1
        assert db.load(pid)['pages']==pages
    page.locator('#trial-history > summary').click()
    page.locator('#trial-controls').scroll_into_view_if_needed();page.screenshot(path=str(ROOT/'.runtime/profiles-ui.png'))
    page.reload(wait_until='networkidle');page.get_by_role('button',name=title,exact=False).click();expect(page.locator('#refresh')).to_be_enabled()
    expect(page.locator('#trial-expression')).to_have_value('surprised');expect(page.locator('#trial-outfit')).to_have_value('work')
    page.locator('#trial-kind').select_option('location');page.locator('#trial-preview').click();expect(page.locator('#status')).to_contain_text('画像APIは呼んでいません')
    assert 'no humans' in page.locator('#trial-prompt').input_value();expect(page.locator('#trial-expression')).not_to_be_visible()
    expect(page.locator('#trial-image')).not_to_be_visible()
    assert len(novelai.ledger()['requests'])==before+(1 if args.live else 0)
    page.set_viewport_size({'width':700,'height':1000});page.locator('#trial-controls').scroll_into_view_if_needed();page.screenshot(path=str(ROOT/'.runtime/profiles-ui-mobile.png'))
    assert not errors,errors
    browser.close()
print(json.dumps(dict(project_id=pid,checks=['10衣装の編集・上限','実Strata衣装整理','コマ衣装・表情保存','人物と背景の送信確認','UI選択の再読込','ページ採用画像の保持'],image_api_calls=1 if args.live else 0,js_errors=errors),ensure_ascii=False))
if not args.live:
    folder=db.directory(pid).resolve();assert folder.parent==db.WORK.resolve() and folder.name.startswith('ui-profiles-');shutil.rmtree(folder)
