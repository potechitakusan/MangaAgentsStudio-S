"""Single-image, free-tier-first NovelAI adapter. Never retries generation."""
from __future__ import annotations
import base64
import hashlib
import io
import json
import os
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from PIL import Image
from . import project as db, profiles

API = 'https://image.novelai.net'
MODEL = 'nai-diffusion-5-curated'
SUGGEST = '/ai/generate-image/suggest-tags'
MAX_ANLAS = 5000
MAX_RESPONSE = 32 * 1024 * 1024
GEN_LOCK = threading.Lock()
NEGATIVE = ('lowres, artistic error, film grain, scan artifacts, worst quality, bad quality, jpeg artifacts, '
            'very displeasing, chromatic aberration, dithering, halftone, screentone, multiple views, logo, '
            'too many watermarks, negative space, blank page, text, speech bubble')

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise db.StudioError('NovelAIのリダイレクトを拒否しました')

def key():
    value = os.environ.get('NOVELAI_API_KEY', '').strip()
    if not value:
        path = db.ROOT / '.env'
        if path.exists():
            found = []
            for line in path.read_text(encoding='utf-8-sig').splitlines():
                name, sep, text = line.partition('=')
                if sep and name.strip().removeprefix('export ').strip() == 'NOVELAI_API_KEY':
                    found.append(text.strip().strip('\"\''))
            if len(found) > 1: raise db.StudioError('NOVELAI_API_KEYが重複しています')
            if found: value = found[0]
    if not value or any(ch.isspace() for ch in value):
        raise db.StudioError('.envのNOVELAI_API_KEYを確認してください')
    return value

def request(endpoint, payload=None):
    if endpoint not in ('/user/subscription', '/ai/generate-image') and not endpoint.startswith(SUGGEST + '?'):
        raise db.StudioError('APIエンドポイントが不正です')
    token = key()
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8') if payload is not None else None
    if body and token.encode() in body: raise db.StudioError('要求への秘密情報混入を検出しました')
    headers = {'Authorization': 'Bearer ' + token, 'User-Agent': 'MangaAgentsStudio-S/0.1', 'Accept': 'application/zip' if body else 'application/json'}
    if body: headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(API + endpoint, body, headers)
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=180 if body else 30) as response:
            data = response.read(MAX_RESPONSE + 1)
            if len(data) > MAX_RESPONSE: raise db.StudioError('NovelAI応答がサイズ上限を超えました')
            return data
    except urllib.error.HTTPError as exc:
        # Do not expose upstream text, headers or credentials.
        raise db.StudioError(f'NovelAI HTTP {exc.code}。生成は自動再送しません') from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise db.StudioError('NovelAI通信が完了しませんでした。生成は自動再送しません') from None

def suggest_tags(prompt, model=MODEL, lang='en'):
    """Read-only tag lookup. Returns [{tag, count, confidence}] for an incomplete tag; never generates."""
    text = str(prompt).strip()
    if not text or len(text) > 100 or '\n' in text: raise db.StudioError('タグ候補の問合せ文字列が不正です')
    query = urllib.parse.urlencode(dict(model=model, prompt=text, lang=lang))
    try:
        raw = json.loads(request(f'{SUGGEST}?{query}'))
        tags = raw['tags'] if isinstance(raw, dict) else raw
        if isinstance(tags, dict): tags = [tags]
        return [dict(tag=str(t['tag']), count=int(t['count']), confidence=float(t['confidence'])) for t in tags]
    except (ValueError, KeyError, TypeError):
        raise db.StudioError('タグ候補の応答形式を確認できません') from None

def status():
    try:
        raw = json.loads(request('/user/subscription'))
        result = {k: raw[k] for k in ('active', 'tier')}
        balance = raw['trainingStepsLeft']
        result['balance'] = sum(balance[k] for k in ('fixedTrainingStepsLeft', 'purchasedTrainingSteps'))
        result['usage'] = {k: raw.get('usage', {}).get(k) for k in ('percent', 'isNegative')}
        if type(result['active']) is not bool or type(result['tier']) is not int or type(result['balance']) is not int:
            raise ValueError()
        return result
    except (ValueError, KeyError, TypeError):
        raise db.StudioError('NovelAIの契約・残量形式を確認できません') from None

def free_guard(state):
    if state.get('active') is not True or state.get('tier') != 3:
        raise db.StudioError('Opus契約を確認できません。有料への自動切替はしません')
    usage = state.get('usage', {})
    percent = usage.get('percent')
    if type(percent) not in (int, float) or percent < 1 or usage.get('isNegative') is not False:
        raise db.StudioError('V5の無料利用枠が不足または不明です。回復後に再実行してください')

def build_request(project, panel):
    db.validate_board(project,allow_incomplete=project.get('workspace_version')==2)
    # Choose a normal-size aspect ratio close to the destination panel.
    ratio = 1
    for page in project['pages']:
        for candidate, box in zip(page['panels'], db.TEMPLATES[page['template']]):
            if candidate['id'] == panel['id']: ratio = box[2] * 1104 / (box[3] * 1704)
    width, height = (1216, 832) if ratio > 1.2 else ((832, 1216) if ratio < .8 else (1024, 1024))
    places = {x['id']: x for x in project.get('locations', [])}
    props = {x['id']: x for x in project.get('props', [])}
    fixed_place = places[panel['location_id']]['description'] if panel.get('location_id') else ''
    fixed_props = ', '.join(props[pid]['description'] for pid in panel.get('prop_ids', []))
    literal = panel.get('prompt_mode', 'catalog') == 'literal'
    caption = panel['prompt'] if literal else ', '.join(x for x in [profiles.scene_prompt(panel), fixed_place, fixed_props, project['style'], 'very aesthetic, masterpiece, no text'] if x)
    negative = panel.get('negative', '') if literal else ', '.join([NEGATIVE, project.get('style_negative',''), panel.get('negative', '')]).strip(', ')
    cast = {c['id']: c for c in project['characters']}
    characters = []
    exclusions = []
    for c in panel.get('characters', []):
        centers = [{'x': c.get('x', .5), 'y': c.get('y', .5)}]
        characters.append({'char_caption': c['prompt'] if literal else profiles.caption(cast[c['id']], c), 'centers': centers})
        exclusions.append({'char_caption': c.get('negative', ''), 'centers': centers})
    from . import prompt_options
    return request_payload(caption, negative, characters, panel.get('seed'), width, height,
                           exclusions=exclusions, conditioning=prompt_options.conditioning(panel))


def request_payload(caption, negative, characters, seed, width, height, *, exclusions=None, conditioning=None):
    exclusions = exclusions if exclusions is not None else [dict(char_caption='', centers=c['centers']) for c in characters]
    flags = conditioning or dict(use_coords=bool(characters), use_order=True, negative_use_coords=bool(characters), negative_use_order=False)
    params = dict(width=width, height=height, steps=23, n_samples=1, seed=seed if seed is not None else secrets.randbelow(4294967296),
                  sampler='k_euler_ancestral', noise_schedule='karras', scale=7.0, cfg_rescale=0.0,
                  negative_prompt=negative, sm=False, sm_dyn=False, dynamic_thresholding=False,
                  deliberate_euler_ancestral_bug=False, prefer_brownian=True, legacy_v3_extend=False,
                  image_format='png', straight_alpha=True)
    params['v4_prompt'] = dict(caption=dict(base_caption=caption, char_captions=characters), use_coords=flags['use_coords'], use_order=flags['use_order'], legacy_uc=False)
    params['v4_negative_prompt'] = dict(caption=dict(base_caption=negative, char_captions=exclusions), use_coords=flags['negative_use_coords'], use_order=flags['negative_use_order'], legacy_uc=False)
    return dict(action='generate', input=caption, model=MODEL, parameters=params)


def build_page_request(project, page_number):
    from . import wholepage
    from . import prompt_options
    prompt_options.require_panel_mode({**project, 'render_mode': 'whole'})
    compiled = wholepage.compile_prompt(project, page_number)
    negative = 'lowres, bad anatomy, bad hands, jpeg artifacts, watermark, logo, repeated text, furigana'
    if project.get('style_negative'): negative += ', '+project['style_negative']
    if project.get('text_mode','embedded')=='overlay': negative += ', text, speech bubble, letters'
    return request_payload(compiled['caption'], negative, compiled['actors'], compiled['seed'], 832, 1216)

def request_signature(payload):
    clean=json.loads(json.dumps(payload))
    clean['parameters']['seed']=0
    return hashlib.sha256(json.dumps(clean,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def is_stale(project, panel=None, page_number=None):
    target=project['pages'][page_number-1].get('whole',{}) if page_number is not None else panel
    if not target or not target.get('image'): return True
    path=db.asset_path(project['id'],target['image']).with_suffix('.request.json')
    if not path.exists(): return True
    current=build_page_request(project,page_number) if page_number is not None else build_request(project,panel)
    previous=db.read_json(path)
    if previous.get('provider')=='qwen': return True
    desired=project['pages'][page_number-1].get('whole',{}).get('seed') if page_number is not None else panel.get('seed')
    return (desired is not None and desired!=previous['parameters']['seed']) or request_signature(previous)!=request_signature(current)

def ledger_path(): return db.ROOT / '.runtime' / 'anlas-ledger.json'

def ledger():
    path = ledger_path()
    return db.read_json(path) if path.exists() else dict(limit=MAX_ANLAS, spent=0, requests={})

def budget_limit(state):
    """The verification budget the user set on screen (default MAX_ANLAS)."""
    return state.get('limit', MAX_ANLAS)

def ledger_summary():
    """What the screen shows: the counter, its limit, the safety stops, and when it was last reset or changed."""
    s = ledger()
    return dict(limit=budget_limit(s), spent=s['spent'], halted=s.get('halted'),
                unknown=sum(r.get('status') in ('sending', 'unknown') for r in s['requests'].values()),
                resets=s.get('resets', [])[-10:], limit_changes=s.get('limit_changes', [])[-10:])

def set_budget(limit=None, reset=False):
    """Change the limit and/or put the counter back to 0. Both are recorded with the time; per-request records are kept.
    The safety stops (an unconfirmed send, an unexpected Anlas decrease) are deliberately not cleared here."""
    if limit is None and not reset: raise db.StudioError('上限の変更か0リセットを指定してください')
    if limit is not None and (type(limit) is not int or not 1 <= limit <= 1_000_000):
        raise db.StudioError('上限は1〜1,000,000 Anlasの整数です')
    if not GEN_LOCK.acquire(blocking=False): raise db.StudioError('画像を生成中です。終わってから変更してください')
    try:
        with db.exclusive(ledger_path().with_name('novelai.lock')):
            state = ledger()
            state.setdefault('requests', {})
            if limit is not None and limit != budget_limit(state):
                state.setdefault('limit_changes', []).append(dict(at=db.now(), previous=budget_limit(state), limit=limit))
                state['limit'] = limit
            if reset:
                state.setdefault('resets', []).append(dict(at=db.now(), spent_before=state['spent'], limit=budget_limit(state)))
                state['spent'] = 0
            db.dump(ledger_path(), state)
    finally:
        GEN_LOCK.release()
    return ledger_summary()

def image_bytes(body):
    try:
        if body.startswith(b'PK'):
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                members = [x for x in archive.infolist() if x.filename.lower().endswith('.png') and not x.is_dir()]
                if len(members) != 1 or members[0].file_size > MAX_RESPONSE: raise ValueError()
                png = archive.read(members[0])
        elif body.startswith(b'\x89PNG'):
            png = body
        else:
            raw = json.loads(body)
            if len(raw['images']) != 1: raise ValueError()
            png = base64.b64decode(raw['images'][0]['image'], validate=True)
        with Image.open(io.BytesIO(png)) as im:
            if im.format != 'PNG' or im.width * im.height > 1048576: raise ValueError()
            im.verify()
        return png
    except Exception:
        raise db.StudioError('画像応答を確認できません。送信結果の再確認が必要です') from None

def generate(project_id, panel_id, request_id, expected_revision=None, *, page_number=None, preview=None):
    from . import wholepage
    db.identifier(request_id)
    if not GEN_LOCK.acquire(blocking=False): raise db.StudioError('NovelAIは別の画像を生成中です')
    try:
        with db.exclusive(ledger_path().with_name('novelai.lock')):
            state = ledger()
            if request_id in state['requests']:
                old = state['requests'][request_id]
                if old['project_id'] != project_id or old.get('panel_id') != panel_id or old.get('page_number') != page_number or old.get('preview') != preview:
                    raise db.StudioError('同じ要求IDを別の作品・コマには使えません')
                return state['requests'][request_id]  # Idempotent replay never sends again.
            from . import qwen
            if qwen.unresolved(): raise db.StudioError('QwenImageに未確認の生成があります。履歴を確認してください')
            if state.get('halted'):
                raise db.StudioError('費用台帳が停止中です。使い方ガイドの「困ったとき」を確認してください')
            if state['spent'] >= budget_limit(state):
                raise db.StudioError(f'検証費用が上限（{budget_limit(state)} Anlas）に達しました。右上の検証費用から、上限を変えるか0に戻してください')
            if any(r['status'] in ('sending', 'unknown') for r in state['requests'].values()):
                raise db.StudioError('未確認の生成があります。自動再送せず費用台帳を確認してください')
            project = db.load(project_id)
            if project['output_method']!='novelai': raise db.StudioError('NovelAI作品ではありません')
            if expected_revision is not None and project['revision'] != expected_revision:
                raise db.StudioError('編集競合: 最新の保存内容を読み直してください')
            if preview is not None:
                if panel_id is not None or page_number is not None: raise db.StudioError('試し描きと漫画の生成を同時に指定できません')
                trial = profiles.preview(project, preview)
                payload = trial['request']
            elif page_number is not None:
                if panel_id is not None: raise db.StudioError('コマとページは同時に生成指定できません')
                payload = build_page_request(project, page_number)
            else:
                payload = build_request(project, db.panel_by_id(project, panel_id))
            before = status()
            free_guard(before)
            folder = db.directory(project_id) / 'images' / ('previews' if preview is not None else (f'page-{page_number:03}' if page_number is not None else panel_id))
            folder.mkdir(parents=True, exist_ok=True)
            file = folder / (request_id + '.png')
            db.dump(file.with_suffix('.request.json'), payload)
            record = dict(id=request_id, project_id=project_id, panel_id=panel_id, status='sending',
                          started_at=db.now(), before=before, revision=project['revision'], seed=payload['parameters']['seed'],
                          request_sha256=hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
                          image=file.relative_to(db.directory(project_id)).as_posix())
            if preview is not None:
                record.update(preview=preview, name=trial['snapshot']['entry']['name'])
                db.dump(file.with_suffix('.plan.json'), trial['snapshot'])
            if page_number is not None:
                config = wholepage.settings(project, page_number)
                record.update(page_number=page_number, format=config['format'], text_mode=project.get('text_mode','embedded'))
                db.dump(file.with_suffix('.plan.json'), config)
            state['requests'][request_id] = record
            db.dump(ledger_path(), state)
            try:
                png = image_bytes(request('/ai/generate-image', payload))
                with Image.open(io.BytesIO(png)) as im:
                    if im.size != (payload['parameters']['width'], payload['parameters']['height']):
                        raise db.StudioError('返却画像寸法が要求と違います')
                file.write_bytes(png)
                after = status()
                delta = before['balance'] - after['balance']
                if delta < 0:
                    raise db.StudioError('生成中に残量が増えたため費用を確定できません')
                record.update(status='complete', after=after, actual_anlas=delta, finished_at=db.now())
                state['spent'] += delta
                if delta > 0:
                    state['halted'] = '無料対象設定でAnlasの減少を検出。並行利用・課金条件を要確認'
                db.dump(ledger_path(), state)
                db.dump(file.with_suffix('.record.json'), record)
            except Exception as exc:
                record.update(status='unknown', error=str(exc)[:300], finished_at=db.now())
                db.dump(ledger_path(), state)
                db.dump(file.with_suffix('.record.json'), record)
                raise
            if preview is not None: return record  # Trial images never adopt into the comic or bump its revision.
            # A concurrent editor is allowed to win. Keep the generated candidate, never restore stale prompts.
            with db.LOCK:
                current = db.load(project_id)
                def apply(p):
                    target = wholepage.candidates_target(p, page_number) if page_number is not None else db.panel_by_id(p, panel_id)
                    candidate = dict(id=request_id, image=record['image'], seed=record['seed'], revision=record['revision'])
                    if page_number is not None: candidate.update(format=record['format'],text_mode=record['text_mode'])
                    target.setdefault('candidates', []).append(candidate)
                    if current['revision'] == project['revision']:
                        target['image'] = record['image']
                        target['adopted_request'] = request_id
                        if page_number is not None: target['generated_text_mode']=record['text_mode']
                try:
                    db.mutate(project_id, current['revision'], apply)
                except db.StudioError:
                    record['adoption_note'] = '保存中に作品が変更されました。画像は履歴フォルダに保存済みです'
            return record
    finally:
        GEN_LOCK.release()

def adopt(project_id, revision, panel_id, candidate_id):
    def apply(p):
        target = db.panel_by_id(p, panel_id)
        candidate = next((c for c in target.get('candidates', []) if c['id'] == candidate_id), None)
        if not candidate or not db.asset_path(project_id, candidate['image']).exists(): raise db.StudioError('候補画像がありません')
        target.update(image=candidate['image'], adopted_request=candidate_id)
    return db.mutate(project_id, revision, apply)
