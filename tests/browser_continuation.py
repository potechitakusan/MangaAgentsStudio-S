"""Real Strata, isolated browser, two 1-page batches; no image API call."""
import json
import shutil
import uuid
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
scratch_id = 'ui-continue-' + uuid.uuid4().hex[:8]
source = ('成人女性の花。短い黒髪、白いコート、赤いマフラー。小さな駅の青いベンチ。\n'
          '## 朝\n花は駅の青いベンチに座って電車を待つ。「まだかな」と時計を見る。\n'
          '## 到着\n電車が到着し、花はベンチから立ち上がる。「来た」と笑う。')
errors = []
with sync_playwright() as playwright:
    browser = playwright.firefox.launch(headless=True)
    page = browser.new_page(viewport={'width':1440, 'height':1000})
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('http://127.0.0.1:8765', wait_until='networkidle')
    page.get_by_role('button', name='新しい作品', exact=True).click()
    page.locator('#new-id').fill(scratch_id)
    page.locator('#new-title').fill('継続UI試験')
    page.locator('#new-source').fill(source)
    page.get_by_role('button', name='作品を作成（まだ生成しません）', exact=True).click()
    expect(page.locator('#title')).to_have_text('継続UI試験')
    page.locator('#page-count').fill('1')
    page.locator('#board-instruction').fill('今回の章を1ページにしてください。twoテンプレートで2コマ。場所と人物を登録して保存。')
    page.locator('#board-agent').click()
    expect(page.locator('#status')).to_contain_text('Strataの編集と組版が完了しました', timeout=180000)
    folder = ROOT / 'work' / scratch_id
    first = json.loads((folder/'project.json').read_text(encoding='utf-8'))
    assert len(first['pages']) == 1
    page.locator('#new-pages').click()
    expect(page.locator('#batch-title')).to_have_text('2ページ目から字コンテを作成')
    expect(page.locator('#source-section')).to_have_value('1')
    page.locator('#page-count').fill('1')
    page.locator('#board-instruction').fill('第二章の到着を続きの1ページにしてください。twoで2コマ。既存台帳はIDだけ参照して変更しない。')
    page.locator('#board-agent').click()
    expect(page.locator('#status')).to_contain_text('Strataの編集と組版が完了しました', timeout=180000)
    expect(page.locator('#page-select option')).to_have_count(2)
    second = json.loads((folder/'project.json').read_text(encoding='utf-8'))
    assert second['pages'][0] == first['pages'][0]
    assert second['characters'] == first['characters']
    assert second['locations'] == first['locations']
    assert second['batches'][1]['source_section'] == 1
    assert all(c['id'].startswith('p2-') for c in second['pages'][1]['panels'])
    assert not errors, errors
    result = dict(status='passed', pages=2, preserved_first_page=True, preserved_bible=True, console_errors=errors)
    (ROOT/'.runtime/ui-continuation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    browser.close()
target = folder.resolve()
assert target.is_relative_to((ROOT/'work').resolve()) and target.name.startswith('ui-continue-')
shutil.rmtree(target)
print(json.dumps(result))
