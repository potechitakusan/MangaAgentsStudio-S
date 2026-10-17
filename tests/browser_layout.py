"""UI test of the tabbed workspace in an isolated Firefox. No image generation; creates and then trashes one throw-away work.
Run: PYTHONPATH=.runtime/test-deps PLAYWRIGHT_BROWSERS_PATH=.runtime/browsers python -X utf8 tests/browser_layout.py [port]"""
import json
import re
import shutil
import sys
import uuid
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
BASE = 'http://127.0.0.1:' + (sys.argv[1] if len(sys.argv) > 1 else '8765')
SHOTS = ROOT / '.runtime' / 'layout-shots'
SHOTS.mkdir(parents=True, exist_ok=True)
errors = []
test_id = 'layout-test-' + uuid.uuid4().hex[:6]
width = lambda page, selector: page.locator(selector).bounding_box()['width']
top = lambda page, selector: page.locator(selector).first.bounding_box()['y']

with sync_playwright() as p:
    browser = p.firefox.launch(headless=True)
    page = browser.new_page(viewport={'width': 1920, 'height': 1080})
    page.on('pageerror', lambda e: errors.append(str(e)))
    dialogs = []
    page.on('dialog', lambda d: (dialogs.append(d.message), d.accept()))
    page.goto(BASE, wait_until='networkidle')

    # --- tabs: 汎用 holds only the AI choice and the all-works system prompts ---------------------
    page.locator('#tab-btn-general').click()
    expect(page.locator('#tab-general')).to_be_visible()
    expect(page.locator('#tab-work')).to_be_hidden()
    expect(page.locator('#tab-btn-general')).to_have_attribute('aria-selected', 'true')
    expect(page.locator('#llm-provider')).to_be_visible()
    expect(page.locator('#global-system-fields textarea')).to_have_count(4)
    expect(page.locator('#style-preset')).to_be_hidden()
    page.locator('#tab-btn-general').press('ArrowRight')
    expect(page.locator('#tab-work')).to_be_visible()
    page.locator('#tab-btn-work').press('ArrowLeft')
    expect(page.locator('#tab-general')).to_be_visible()

    # all-works prompts are saved on their own (the request is answered locally so the real settings stay untouched)
    posted = []
    def fake_save(route):
        posted.append(json.loads(route.request.post_data)['system'])
        route.fulfill(status=200, content_type='application/json', body=json.dumps(posted[-1]))
    page.route('**/api/default-system', fake_save)
    page.locator('[data-global-system="review"]').fill('GENERAL-REVIEW-TEST')
    page.locator('#save-global').click()
    expect(page.locator('#status')).to_contain_text('汎用の共通システムプロンプトを保存しました')
    assert posted and posted[0]['review'] == 'GENERAL-REVIEW-TEST' and set(posted[0]) == {'storyboard', 'translation', 'review', 'constraints'}
    page.unroute('**/api/default-system')
    page.reload(wait_until='networkidle')
    page.locator('#tab-btn-work').click()

    # --- library collapse ---------------------------------------------------------------------------
    before = page.locator('.workspace').bounding_box()['width']
    page.locator('#library-toggle').click()
    expect(page.locator('main')).to_have_class('library-collapsed')
    expect(page.locator('#projects')).to_be_hidden()
    page.wait_for_timeout(500)                                        # width transition
    assert page.locator('.workspace').bounding_box()['width'] > before + 120
    page.reload(wait_until='networkidle')
    expect(page.locator('main')).to_have_class('library-collapsed')   # remembered
    page.locator('#library-toggle').click()
    expect(page.locator('#projects')).to_be_visible()

    # --- an older work: pager, zoom, page generation, work tab content ------------------------------------
    page.get_by_role('button', name='lantern-12', exact=False).first.click()
    expect(page.locator('#subtitle')).to_contain_text('12 / 24 ページ', timeout=15000)
    expect(page.locator('#page-image')).to_be_visible(timeout=15000)
    total = int(page.locator('#page-position').inner_text().split('/')[1])
    assert total >= 8, total
    expect(page.locator('#page-position')).to_have_text(f'1 / {total}')
    expect(page.locator('#page-prev')).to_be_disabled()
    page.locator('#page-next').click()
    expect(page.locator('#page-position')).to_have_text(f'2 / {total}')
    page.locator('#page-prev').click()
    expect(page.locator('#page-position')).to_have_text(f'1 / {total}')
    page.locator('#page-select').select_option(str(total - 1))
    expect(page.locator('#page-position')).to_have_text(f'{total} / {total}')
    expect(page.locator('#page-next')).to_be_disabled()
    page.locator('#page-select').select_option('0')
    expect(page.locator('#page-position')).to_have_text(f'1 / {total}')

    # zoom: x1.0 / x1.5 / x2.0 (linear; x2.0 is four times the area), remembered for every work
    expect(page.locator('.zoom-button')).to_have_count(3)
    expect(page.locator('.zoom-button[data-zoom="1"]')).to_have_attribute('aria-pressed', 'true')
    base = width(page, '#page-wrap')
    assert width(page, '.stage-side') >= 800, width(page, '.stage-side')      # the right panel is wide at x1.0
    page.locator('.zoom-button[data-zoom="1.5"]').click()
    assert abs(width(page, '#page-wrap') / base - 1.5) < 0.05, (base, width(page, '#page-wrap'))
    page.locator('.zoom-button[data-zoom="2"]').click()
    assert abs(width(page, '#page-wrap') / base - 2.0) < 0.05
    expect(page.locator('.zoom-button[data-zoom="2"]')).to_have_attribute('aria-pressed', 'true')
    expect(page.locator('#zoom-hint')).to_have_text('')                         # on the 1920px design screen even x2.0 fits the lane
    assert width(page, '.stage-side') >= 500
    page.set_viewport_size({'width': 1440, 'height': 900})                      # a smaller screen: the page no longer fits, so scroll and hint
    expect(page.locator('#zoom-hint')).to_contain_text('横にスクロール')
    page.set_viewport_size({'width': 1920, 'height': 1080})
    page.wait_for_timeout(400)
    page.screenshot(path=str(SHOTS / 'zoom-2.0-1920.png'))
    page.reload(wait_until='networkidle')
    page.get_by_role('button', name='雨の日の青い傘', exact=False).first.click()   # a different work keeps the same choice
    expect(page.locator('#subtitle')).to_contain_text('/ 24 ページ', timeout=15000)
    expect(page.locator('.zoom-button[data-zoom="2"]')).to_have_attribute('aria-pressed', 'true')
    page.get_by_role('button', name='lantern-12', exact=False).first.click()
    expect(page.locator('#subtitle')).to_contain_text('12 / 24 ページ', timeout=15000)
    page.locator('.zoom-button[data-zoom="1"]').click()

    # page generation lives in the centre lane and asks before it saves and generates
    expect(page.locator('.stage-center #produce-page')).to_be_visible()
    expect(page.locator('#tab-work #produce-page')).to_have_count(0)
    expect(page.locator('#tab-work #produce-all')).to_have_count(1)
    page.locator('#produce-page').click()
    expect(page.locator('#produce-dialog')).to_be_visible()
    expect(page.locator('#produce-title')).to_contain_text('PAGE 1')
    expect(page.locator('#produce-points li').first).to_contain_text('保存')
    page.screenshot(path=str(SHOTS / 'produce-dialog.png'))
    page.locator('#produce-cancel').click()
    expect(page.locator('#produce-dialog')).to_be_hidden()
    assert not [x for x in page.evaluate('performance.getEntriesByType("resource")') if re.search(r'/api/job(\?|$)', x['name'])]  # nothing started

    # review warnings: a banner in the centre lane leads to the list in the work tab; each warning can open its page
    page.evaluate("report => renderReview(report)", {'revision': 1, 'warnings': [{'path': 'characters.0.voice_ja', 'message': '口調が揃っていません', 'page': 2, 'source': 'Strata'}]})
    expect(page.locator('#review-banner')).to_be_visible()
    expect(page.locator('#review-banner')).to_contain_text('1件')
    page.locator('#tab-btn-page').click()
    page.locator('#review-banner').click()
    expect(page.locator('#tab-work')).to_be_visible()
    expect(page.locator('#review-results')).to_contain_text('PAGE 2 · Strata — characters.0.voice_ja')
    page.locator('#review-results button', has_text='このページを開く').click()
    expect(page.locator('#page-position')).to_have_text(f'2 / {total}')
    expect(page.locator('#tab-page')).to_be_visible()
    # where the novel stands: chapters show their used pages, and the previous ending is shown for adding pages
    options = page.locator('#source-section option').all_inner_texts()
    assert any('使用済み: PAGE 1〜4' in o for o in options), options
    assert '前回の終わり' in page.locator('#source-progress').text_content()

    # the production AI fixes the warnings: rewrite each affected page with the warnings as the instruction, then review again
    page.evaluate("report => renderReview(report)", {'revision': 1, 'warnings': [{'path': 'characters.0.voice_ja', 'message': '口調が揃っていません', 'page': 2, 'source': 'Strata'}]})
    page.locator('#tab-btn-work').click()
    expect(page.locator('#fix-warnings')).to_be_visible()
    fixed = []
    def fake_job(route):
        body = json.loads(route.request.post_data); fixed.append(body)
        result = {'revision': 1, 'warnings': []} if body['kind'] == 'review' else {}
        route.fulfill(status=200, content_type='application/json', body=json.dumps({'id': 't', 'status': 'complete', 'message': 'ok', 'result': result}))
    page.route('**/api/job', fake_job)
    page.locator('#fix-warnings').click()
    expect(page.locator('#status')).to_contain_text('再点検の警告はありません', timeout=15000)
    page.unroute('**/api/job')
    assert [b['kind'] for b in fixed] == ['rewrite', 'review'], fixed
    assert fixed[0]['page_number'] == 2 and '口調が揃っていません' in fixed[0]['instruction'] and 'characters.0.voice_ja' in fixed[0]['instruction']
    assert any('PAGE 2' in m and '改稿' in m for m in dialogs), dialogs
    expect(page.locator('#review-banner')).to_be_hidden()
    page.evaluate("() => renderReview(null)")
    expect(page.locator('#review-banner')).to_be_hidden()
    page.locator('#page-select').select_option('0')
    expect(page.locator('#page-position')).to_have_text(f'1 / {total}')

    # work tab: no numbering, fixed generation method, layered system prompt (older works stay standalone)
    page.locator('#tab-btn-work').click()
    work_text = page.locator('#tab-work').inner_text()
    assert not re.search('[①②③④⑤⑥⑦⑧]', work_text), 'circled numbers remain'
    expect(page.locator('#mode-label')).to_contain_text('コマ別生成')
    expect(page.locator('#generation-mode')).to_be_hidden()           # only inside the folded advanced section
    expect(page.locator('#system-layered')).not_to_be_checked()
    expect(page.locator('#system-layer-note')).to_contain_text('従来どおり')
    expect(page.locator('#style-preset')).to_be_visible()
    page.locator('#system-details > summary').click() if page.locator('#system-details[open]').count() == 0 else None
    page.screenshot(path=str(SHOTS / 'work-tab-1920.png'))

    # panel tab: Japanese first, English marked as the text that is sent, props can be un-ticked
    page.locator('#page-hotspots button').nth(1).click()
    expect(page.locator('#tab-page')).to_be_visible()
    expect(page.locator('#panel-title')).to_have_text('コマ p1-2')
    expect(page.locator('#page-tab-body .lang-note').first).to_contain_text('英語が原則')
    assert top(page, '#panel-prompt-ja textarea') < top(page, '#prompt')
    assert top(page, '#panel-negative-ja textarea') < top(page, '#negative')
    ja, en = page.locator('#characters [data-field="prompt_ja"]').first, page.locator('#characters [data-field="prompt"]').first
    assert ja.bounding_box()['y'] < en.bounding_box()['y']
    expect(page.locator('#tab-page')).to_contain_text('英語・NovelAIへ送信')
    expect(page.locator('#tab-page')).to_contain_text('日本語・確認用')
    boxes = page.locator('#prop-ids input[type=checkbox]')
    assert boxes.count() >= 1
    boxes.first.check(); expect(boxes.first).to_be_checked()
    boxes.first.uncheck(); expect(boxes.first).not_to_be_checked()
    page.locator('#scene').fill(page.locator('#scene').input_value() + ' ')
    expect(page.locator('#side-dirty')).to_have_text('未保存')
    expect(page.locator('#dirty-list')).to_contain_text('このコマに映る固定小物')     # says what is unsaved
    expect(page.locator('#dirty-list')).to_contain_text('場面メモ')
    page.screenshot(path=str(SHOTS / 'page-tab-1920.png'))
    page.get_by_role('button', name='雨の日の青い傘', exact=False).first.click()   # asks before discarding, and names the edits
    expect(page.locator('#subtitle')).to_contain_text('/ 24 ページ', timeout=15000)
    assert any('未保存の項目' in m and '固定小物' in m and '場面メモ' in m for m in dialogs), dialogs
    expect(page.locator('#dirty-list')).to_be_hidden()
    page.reload(wait_until='networkidle')                             # an unsaved edit is gone after a reload
    expect(page.locator('#side-dirty')).to_have_text('保存済み')

    # --- page-batch work shows the page-level editor inside the same tab -------------------------------
    page.get_by_role('button', name='wholepage-demo', exact=False).first.click()
    expect(page.locator('#mode-label')).to_contain_text('ページ一括', timeout=15000)
    expect(page.locator('#page-image')).to_be_visible(timeout=15000)
    page.locator('#tab-btn-page').click()
    expect(page.locator('#whole-editor')).to_be_visible()
    page.screenshot(path=str(SHOTS / 'page-tab-whole-1920.png'))

    # --- narrow screens: single column, no horizontal overflow --------------------------------------
    for narrow in (1000, 700):
        page.set_viewport_size({'width': narrow, 'height': 900})
        assert page.evaluate('document.documentElement.scrollWidth - document.documentElement.clientWidth') <= 2, narrow
    page.set_viewport_size({'width': 1920, 'height': 1080})

    # --- a new work: the generation method is chosen here, own prompts start empty and layer on the general ones
    page.locator('#new-project').click()
    expect(page.locator('input[name="new-render-mode"]')).to_have_count(2)
    expect(page.locator('input[name="new-render-mode"][value="panels"]')).to_be_checked()
    page.locator('#new-title').fill('レイアウト試験')
    page.locator('#new-id').fill(test_id)
    page.locator('#new-source').fill('テスト用の短い原作。')
    page.locator('input[name="new-render-mode"][value="whole"]').check()
    page.screenshot(path=str(SHOTS / 'create-dialog.png'))
    page.locator('#create-form button[type=submit]').click()
    expect(page.locator('#title')).to_have_text('レイアウト試験', timeout=15000)
    page.locator('#tab-btn-work').click()
    expect(page.locator('#mode-label')).to_contain_text('ページ一括')
    expect(page.locator('#system-layered')).to_be_checked()
    expect(page.locator('#system-layer-note')).to_contain_text('連結')
    assert all(v == '' for v in page.locator('#system-fields textarea').evaluate_all('els => els.map(e => e.value)'))
    page.locator('#system-details > summary').click()
    page.locator('#system-preview-button').click()
    expect(page.locator('#system-preview')).to_contain_text('原作の因果関係')    # the general (default) text flows in
    saved = json.loads((ROOT / 'work' / test_id / 'project.json').read_text(encoding='utf-8'))
    assert saved['system_layered'] is True and saved['render_mode'] == 'whole', saved.get('render_mode')
    expect(page.locator('#board-instruction')).to_have_value('')   # no canned request: the host states the job
    assert '駅' not in page.locator('#board-instruction').input_value()
    # save everything at once: an edit of this work plus an edit of the all-works prompts (answered locally, real settings untouched)
    expect(page.locator('#save-all')).to_be_disabled()
    saved_general = []
    def fake_general(route):
        saved_general.append(json.loads(route.request.post_data)['system'])
        route.fulfill(status=200, content_type='application/json', body=json.dumps(saved_general[-1]))
    page.route('**/api/default-system', fake_general)
    page.locator('#source-details > summary').click()
    page.locator('#edit-title').fill('レイアウト試験（改）')
    page.locator('#tab-btn-general').click()
    page.locator('[data-global-system="review"]').fill('GENERAL-REVIEW-TEST-2')
    expect(page.locator('#dirty-list')).to_contain_text('汎用システムプロンプト')
    expect(page.locator('#dirty-list')).to_contain_text('作品名')
    expect(page.locator('#save-all')).to_be_enabled()
    page.locator('#save-all').click()
    expect(page.locator('#status')).to_contain_text('すべて保存しました（2項目）')
    expect(page.locator('#save-all')).to_be_disabled()
    expect(page.locator('#dirty-list')).to_be_hidden()
    assert saved_general and saved_general[0]['review'] == 'GENERAL-REVIEW-TEST-2'
    assert json.loads((ROOT / 'work' / test_id / 'project.json').read_text(encoding='utf-8'))['title'] == 'レイアウト試験（改）'
    page.locator('#tab-btn-work').click()
    page.locator('#edit-title').fill('レイアウト試験（Ctrl+S）')
    page.keyboard.press('Control+s')
    for _ in range(40):                                               # the file changes once the save has run
        if json.loads((ROOT / 'work' / test_id / 'project.json').read_text(encoding='utf-8'))['title'] == 'レイアウト試験（Ctrl+S）': break
        page.wait_for_timeout(250)
    else:
        raise AssertionError('Ctrl+S did not save')
    expect(page.locator('#dirty-list')).to_be_hidden()
    page.unroute('**/api/default-system')
    page.locator('#tab-btn-page').click()
    expect(page.locator('#page-tab-empty')).to_be_visible()           # no pages yet
    expect(page.locator('#page-tab-body')).to_be_hidden()
    page.locator('#tab-btn-work').click()

    # --- back up and trash it --------------------------------------------------------------------------------
    expect(page.locator('#storage-info')).to_contain_text('作品フォルダ')
    page.locator('#backup-light').click()
    expect(page.locator('#status')).to_contain_text('バックアップを作成しました', timeout=15000)
    assert len(list((ROOT / 'work' / '_backups' / test_id).glob('*-manual-light.zip'))) == 1

    page.locator('#delete-project').click()
    expect(page.locator('#delete-dialog')).to_be_visible()
    expect(page.locator('#delete-go')).to_be_disabled()
    page.locator('#delete-confirm').fill('wrong-id')
    expect(page.locator('#delete-go')).to_be_disabled()
    page.locator('#delete-confirm').fill(test_id)
    expect(page.locator('#delete-go')).to_be_enabled()
    with page.expect_navigation(wait_until='networkidle'):            # the page reloads after the move
        page.locator('#delete-go').click()
    expect(page.get_by_role('button', name=test_id, exact=False)).to_have_count(0)
    assert not (ROOT / 'work' / test_id).exists()
    trashed = list((ROOT / 'work' / '_trash').glob(test_id + '-*'))
    assert len(trashed) == 1 and (trashed[0] / 'project.json').exists(), trashed
    browser.close()

# clean the throw-away artefacts of this test only
for path in list((ROOT / 'work' / '_trash').glob(test_id + '-*')) + [ROOT / 'work' / '_backups' / test_id]:
    shutil.rmtree(path, ignore_errors=True)
assert not errors, errors
print('browser layout test passed; screenshots in', SHOTS)
