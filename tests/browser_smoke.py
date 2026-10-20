"""UI test in an isolated Firefox; --live explicitly enables real generation."""
import argparse
import json
import shutil
import uuid
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--live', action='store_true')
args = parser.parse_args()
errors = []
with sync_playwright() as p:
    browser = p.firefox.launch(headless=True)
    page = browser.new_page(viewport={'width':1440,'height':1080}, device_scale_factor=1)
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('http://127.0.0.1:8765', wait_until='networkidle')
    page.get_by_role('button', name='雨の日の青い傘', exact=False).click()
    expect(page.locator('#title')).to_have_text('雨の日の青い傘')
    expect(page.locator('#page-image')).to_be_visible()
    page.locator('#page-hotspots button').nth(1).click()
    expect(page.locator('#panel-title')).to_have_text('コマ p1-2')
    # Move the art in the placement dialog; no image API call.
    page.locator('#adjust-position').click()
    expect(page.locator('#position-y')).to_be_enabled()
    page.locator('#position-y').fill('10')
    page.locator('#position-save').click()
    expect(page.locator('#status')).to_contain_text('PNGで組版しました',timeout=30000)
    page.reload(wait_until='networkidle')
    page.get_by_role('button', name='雨の日の青い傘', exact=False).click()
    page.locator('#page-hotspots button').nth(1).click()
    page.locator('#adjust-position').click()
    expect(page.locator('#position-y')).to_have_value('10.0')
    page.locator('#position-cancel').click()
    page.locator('#page-hotspots button').nth(2).click()
    if args.live:
        page.locator('#agent-instruction').fill('二人が1本の傘を共有する絵になったので、澪は青い傘を右手で持ち、蓮は別の灰色の折り畳み傘を左手で持つ絵にしてください。二人の間に空間を空け、2本の開いた傘がはっきり見える引きの全身像にする。人数は1girl, 1boy、傘は2 umbrellasを明記。negativeにはshared umbrellaを追加。原作の台詞と人物の固定外見は維持。')
        page.get_by_role('button',name='保存してStrataに依頼',exact=True).click()
        expect(page.locator('#status')).to_have_text('Strataの編集と組版が完了しました',timeout=180000)
        prompt = page.locator('#prompt').input_value()
        assert 'umbrella' in prompt.lower(), prompt
        page.get_by_role('button',name='保存して再生成',exact=True).click()
        expect(page.locator('#status')).to_have_text('1コマの生成と組版が完了しました',timeout=180000)
        assert page.locator('#candidate-list button').count() >= 2
    if page.locator('#candidate-list button').count() >= 2:
        page.locator('#candidate-list button').first.click()
        expect(page.locator('#status')).to_have_text('候補画像を採用し、組版に反映しました',timeout=30000)
        expect(page.locator('#save-panel')).to_be_enabled()
        page.locator('#candidate-list button').last.click()
        expect(page.locator('#status')).to_have_text('候補画像を採用し、組版に反映しました',timeout=30000)
        expect(page.locator('#candidate-list button').last).to_have_text('採用中')
    expect(page.locator('#save-panel')).to_be_enabled()
    scratch_id = 'ui-test-' + uuid.uuid4().hex[:8]
    page.get_by_role('button', name='新しい作品', exact=True).click()
    page.locator('#new-title').fill('UI取込検証')
    page.locator('#new-id').fill(scratch_id)
    page.locator('#new-source').fill('澪は雨の駅前で赤い傘を開き、家へ向かった。')
    page.get_by_role('button', name='作品を作成（まだ生成しません）', exact=True).click()
    expect(page.locator('#title')).to_have_text('UI取込検証')
    page.get_by_text('作成済み字コンテJSONを取り込む', exact=True).click()
    page.locator('#import-json').fill((ROOT / 'templates/board.example.json').read_text(encoding='utf-8'))
    page.get_by_role('button', name='取り込む', exact=True).click()
    expect(page.locator('#status')).to_have_text('ページを組み直しました', timeout=30000)
    expect(page.locator('#title')).to_have_text('傘の約束')
    assert page.locator('#panels button').count() == 3
    page.get_by_role('button', name='雨の日の青い傘', exact=False).click()
    expect(page.locator('#title')).to_have_text('雨の日の青い傘')
    expect(page.locator('#save-panel')).to_be_enabled()
    scratch = (ROOT / 'work' / scratch_id).resolve()
    assert scratch.is_relative_to((ROOT / 'work').resolve()) and scratch.name.startswith('ui-test-')
    shutil.rmtree(scratch)
    page.reload(wait_until='networkidle')
    page.get_by_role('button', name='雨の日の青い傘', exact=False).click()
    page.locator('#page-hotspots button').nth(2).click()
    page.evaluate('window.scrollTo(0,0)')
    page.screenshot(path=str(ROOT / '.runtime/ui-desktop.png'), full_page=True)
    page.set_viewport_size({'width':720,'height':1000})
    page.screenshot(path=str(ROOT / '.runtime/ui-narrow.png'), full_page=True)
    if (ROOT / 'work/lantern-12/project.json').exists():
        page.set_viewport_size({'width':1440,'height':1080})
        page.get_by_role('button', name='雨待ち喫茶の灯り', exact=False).click()
        expect(page.locator('#title')).to_have_text('雨待ち喫茶の灯り')
        expect(page.locator('#page-select option')).to_have_count(12)
        expect(page.locator('#generate-from')).to_have_value('9')
        expect(page.locator('#generate-to')).to_have_value('12')
        page.locator('#page-select').select_option('11')
        expect(page.locator('#panel-title')).to_have_text('コマ p12-1')
        expect(page.locator('#location-id')).to_be_visible()
        page.locator('#new-pages').click()
        expect(page.locator('#batch-title')).to_have_text('13ページ目から字コンテを作成')
        expect(page.locator('#source-section option')).to_have_count(3)
        page.locator('#source-section').select_option('1')
        assert '雨宿りの記録' in page.locator('#source-preview').text_content()
        assert '## 第三章' not in page.locator('#source-preview').text_content()
        expect(page.locator('#page-count')).to_have_value('2')   # a short trial first; the page plan then covers the rest
        page.locator('#new-pages').click()
        page.evaluate('window.scrollTo(0,0)')
        page.screenshot(path=str(ROOT / '.runtime/ui-twelve.png'), full_page=True)
    assert not errors, errors
    browser.close()
print(json.dumps({'status':'passed','live':args.live,'browser':'Firefox','console_errors':errors},ensure_ascii=False))
