"""Real UI: immutable model at creation, per-work Qwen options, references and import rejection.
Uses the completed local qwen-reference-check fixture. Generates no images.
"""
import json
import uuid
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
BASE='http://127.0.0.1:8765'
SHOTS=ROOT/'.runtime/qwen-reference-shots';SHOTS.mkdir(exist_ok=True)
errors=[]
with sync_playwright() as p:
    browser=p.firefox.launch(headless=True)
    page=browser.new_page(viewport={'width':1920,'height':1080})
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.on('dialog',lambda d:d.accept())
    page.goto(BASE,wait_until='networkidle')
    page.locator('#tab-btn-general').click()
    page.locator('#llm-provider').select_option('grok')
    expect(page.locator('#image-provider')).to_have_count(0)
    expect(page.locator('#work-image-settings')).to_be_hidden()
    created=[]
    for provider in ('novelai','qwen'):
        pid='provider-ui-'+uuid.uuid4().hex[:8];created.append(pid)
        page.locator('#new-project').click()
        page.locator('#new-title').fill('方式固定UI検証 '+provider)
        page.locator('#new-id').fill(pid)
        page.locator('#new-source').fill('成人女性が雨上がりの窓辺に立つ。')
        page.locator('#new-image-provider').select_option(provider)
        page.locator('#create-form button[type=submit]').click()
        expect(page.locator('#create-dialog')).not_to_be_visible()
        expect(page.locator('#title')).to_contain_text(provider)
        page.locator('#tab-btn-work').click()
        expect(page.locator('#image-provider-label')).to_have_text('NovelAI' if provider=='novelai' else 'QwenImage 2.1')
        expect(page.locator('#work-image-settings select')).to_have_count(1)
        page.reload(wait_until='networkidle')
        expect(page.locator('#image-provider-label')).to_have_text('NovelAI' if provider=='novelai' else 'QwenImage 2.1')
        if provider=='qwen':
            expect(page.locator('#reference-mode')).to_have_value('references')
            page.locator('#reference-mode').select_option('prompt')
            page.locator('#anime-finish').check();page.locator('#anime-denoise').fill('0.25')
            page.locator('#save-image-provider').click()
            expect(page.locator('#image-provider-status')).to_contain_text('保存しました')
            page.reload(wait_until='networkidle')
            expect(page.locator('#reference-mode')).to_have_value('prompt')
            expect(page.locator('#anime-finish')).to_be_checked()
            expect(page.locator('#anime-denoise')).to_have_value('0.25')
            page.locator('#check-image-provider').click()
            expect(page.locator('#image-provider-status')).to_contain_text('ComfyUI接続成功',timeout=30000)
            # UI file import must reject a different provider and leave the work intact.
            token=page.request.get(BASE+'/api/session').json()['token']
            value=page.request.get(BASE+'/api/project/'+created[0]).json()['inputs']
            data=json.dumps(dict(schema='manga-studio-inputs-v4',output_method='novelai',inputs=value)).encode()
            page.locator('#import-settings-file').set_input_files(dict(name='wrong.json',mimeType='application/json',buffer=data))
            expect(page.locator('#status')).to_contain_text('output_method',timeout=10000)
            assert page.request.get(BASE+'/api/project/'+pid).json()['project']['output_method']=='qwen'
            # API cannot change a method or insert a provider into per-work options either.
            project=page.request.get(BASE+'/api/project/'+pid).json()['project']
            res=page.request.post(BASE+'/api/workspace',headers={'X-Studio-Token':token},data=dict(project_id=pid,revision=project['revision'],inputs=dict(output_method='novelai')))
            assert res.status==400
    # Inspect real reused references without changing the generated work.
    page.locator('#projects .project-card').filter(has_text='Qwen参照画像・再利用の動作確認').click()
    expect(page.locator('#image-provider-label')).to_have_text('QwenImage 2.1')
    expect(page.locator('#reference-catalog img')).to_have_count(2)
    expect(page.locator('#reference-catalog')).to_contain_text('登録済み')
    expect(page.locator('#reference-mode')).to_have_value('references')
    before=page.request.get(BASE+'/api/project/qwen-reference-check').json()['project']
    page.locator('#trial-history').evaluate('(e)=>e.open=true')
    page.locator('#trial-history-items button').filter(has_text='澪').first.click()
    expect(page.locator('#trial-register')).to_be_visible()
    page.locator('#trial-register').click()
    expect(page.locator('#status')).to_contain_text('参照画像を登録しました')
    after=page.request.get(BASE+'/api/project/qwen-reference-check').json()['project']
    assert before['references']==after['references']
    assert [c['image'] for c in before['pages'][0]['panels']]==[c['image'] for c in after['pages'][0]['panels']]
    if page.locator('[data-unavailable]').count():expect(page.locator('[data-unavailable]').first).to_be_disabled()
    page.locator('#work-image-settings').scroll_into_view_if_needed()
    page.screenshot(path=str(SHOTS/'desktop.png'),full_page=True)
    page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(300)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
    page.screenshot(path=str(SHOTS/'mobile.png'),full_page=True)
    # Remove only the two empty works made by this test using the normal reversible trash action.
    for pid in created:
        token=page.request.get(BASE+'/api/session').json()['token']
        res=page.request.post(BASE+'/api/project-delete',headers={'X-Studio-Token':token},data=dict(project_id=pid,confirm=pid))
        assert res.ok,res.text()
    assert not errors,errors
    browser.close()
print('Qwen references / creation lock / JSON mismatch / 1920 + 390 px: PASS')
