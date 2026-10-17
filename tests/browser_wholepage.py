"""Page-mode UI test. --live generates exactly one image for each of 3 formats."""
import argparse
import hashlib
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(); parser.add_argument('--live',action='store_true'); args=parser.parse_args()
folder=ROOT/'work/wholepage-demo'
before=json.loads((folder/'project.json').read_text(encoding='utf-8'))
original=before['pages'][0]['panels']
image_hashes={p['image']:hashlib.sha256((folder/p['image']).read_bytes()).hexdigest() for p in original}
errors=[]
with sync_playwright() as playwright:
    browser=playwright.firefox.launch(headless=True)
    page=browser.new_page(viewport={'width':1500,'height':1080})
    page.on('pageerror',lambda error:errors.append(str(error)))
    def set_mode(mode):
        page.locator('#tab-btn-work').click();page.locator('#mode-change').evaluate('d => { d.open = true }');page.locator('#generation-mode').select_option(mode)
    page.goto('http://127.0.0.1:8765',wait_until='networkidle')
    page.get_by_role('button',name='ページ一括モード比較',exact=False).click()
    expect(page.locator('#title')).to_have_text('ページ一括モード比較')
    expect(page.locator('#refresh')).to_be_enabled()
    set_mode('whole');page.locator('#tab-btn-page').click()
    expect(page.locator('#whole-editor')).to_be_visible()
    expect(page.locator('#save-whole')).to_be_enabled()
    expect(page.locator('#panel-editor')).to_be_visible()  # Structured panel editing is shared by both modes.
    valid=page.locator('#whole-draft').input_value()
    page.locator('#whole-draft').fill('{ broken json')
    page.locator('#save-whole').click()
    expect(page.locator('#status')).to_have_class('error')
    page.locator('#whole-draft').fill(valid)
    counts=[]
    for kind in ('prose','json','yaml'):
        page.locator('#whole-format').select_option(kind)
        page.locator('#whole-seed').fill('20261014')
        page.locator('#preview-whole').click()
        expect(page.locator('#status')).to_have_text('送信内容を表示しました（画像生成なし）')
        payload=json.loads(page.locator('#whole-preview').text_content())
        assert payload['parameters']['width']==832
        assert 'Text: ' in payload['input'] and 'no text' not in payload['input']
        if kind=='json': assert '"panel_count":3' in payload['input']
        if kind=='yaml': assert 'panel_count: 3' in payload['input']
        if args.live:
            old_count=page.locator('#whole-candidates button').count()
            page.locator('#generate-whole').click()
            expect(page.locator('#status')).to_have_text('1ページの一括生成が完了しました',timeout=240000)
            expect(page.locator('#whole-candidates button')).to_have_count(old_count+1)
            counts.append(kind)
    if page.locator('#whole-candidates button').count():
        page.locator('#whole-candidates button').filter(has_text='prose').last.click()
        expect(page.locator('#status')).to_have_text('ページ候補を採用しました')
    page.locator('#whole-format').select_option('prose')
    page.locator('#save-whole').click()
    expect(page.locator('#save-whole')).to_be_enabled()
    set_mode('panels');page.locator('#tab-btn-page').click()
    expect(page.locator('#panel-editor')).to_be_visible()
    expect(page.locator('#page-hotspots button')).to_have_count(3)
    set_mode('whole');page.locator('#tab-btn-page').click()
    expect(page.locator('#whole-editor')).to_be_visible()
    expect(page.locator('#page-hotspots button')).to_have_count(0)
    page.reload(wait_until='networkidle')
    page.get_by_role('button',name='ページ一括モード比較',exact=False).click()
    expect(page.locator('#whole-editor')).to_be_visible()
    expect(page.locator('#refresh')).to_be_enabled()
    expect(page.locator('#whole-seed')).to_have_value('20261014')
    expect(page.locator('#whole-format')).to_have_value('prose')
    page.screenshot(path=str(ROOT/'.runtime/ui-wholepage.png'),full_page=True)
    page.set_viewport_size({'width':720,'height':1000})
    page.screenshot(path=str(ROOT/'.runtime/ui-wholepage-narrow.png'),full_page=True)
    assert not errors,errors
    browser.close()
after=json.loads((folder/'project.json').read_text(encoding='utf-8'))
def retained(old,new):
    if isinstance(old,dict): return all(k in new and retained(v,new[k]) for k,v in old.items())
    if isinstance(old,list): return len(old)==len(new) and all(retained(a,b) for a,b in zip(old,new))
    return old==new
assert retained(original,after['pages'][0]['panels'])  # New bilingual metadata may be added.
assert all(hashlib.sha256((folder/path).read_bytes()).hexdigest()==checksum for path,checksum in image_hashes.items())
print(json.dumps(dict(status='passed',live=args.live,generated_formats=counts,original_panels_preserved=True,console_errors=errors)))
