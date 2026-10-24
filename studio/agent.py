"""Bounded tool agent for the local Strata endpoint; no shell or network tools."""
from __future__ import annotations
import hashlib
import json
import time
import uuid
from collections import Counter
from . import project as db, profiles, llm, pageplan, local_ai, prompt_options

def tool(name, description, properties=None, required=None):
    return {'type': 'function', 'function': {'name': name, 'description': description,
            'parameters': {'type': 'object', 'properties': properties or {},
                           'required': required or [], 'additionalProperties': False}}}

CROP_SCHEMA = {'type':'array','items':{'type':'number','minimum':0,'maximum':1},'minItems':2,'maxItems':2,
               'description':'画像を枠に当てる中心の座標[x,y]（各0〜1）。省略可。枠のx,y,幅,高さではない'}

def tidy_crop(panel):
    """crop only says where the picture is centred in its panel. Repair what can be repaired and drop the rest, rather than stopping a whole run."""
    if not isinstance(panel, dict) or 'crop' not in panel: return
    crop = panel['crop']
    numbers = isinstance(crop, list) and all(type(v) in (int, float) for v in crop)
    if numbers and len(crop) == 2: center = crop
    elif numbers and len(crop) == 4: center = [crop[0] + crop[2] / 2, crop[1] + crop[3] / 2]   # a box [x, y, width, height]: use its centre
    else:
        del panel['crop']; return
    panel['crop'] = [min(1.0, max(0.0, float(v))) for v in center]

def tidy_speakers(panel):
    """A line spoken from off-screen is normal in manga, so a speaker who is not drawn in the panel becomes blank (unknown) instead of stopping the run.
    speakers is padded or cut to the number of lines. Returns what was changed, so the repair is recorded."""
    if not isinstance(panel, dict) or not isinstance(panel.get('dialogue'), list) or 'speakers' not in panel or not isinstance(panel['speakers'], list): return None
    shown = {c.get('id') for c in panel.get('characters', []) if isinstance(c, dict)}
    fixed = [s if isinstance(s, str) and (not s or s in shown) else '' for s in panel['speakers']][:len(panel['dialogue'])]
    fixed += [''] * (len(panel['dialogue']) - len(fixed))
    if fixed == panel['speakers']: return None
    note = f"{panel.get('id') or 'コマ'}: speakers {panel['speakers']} → {fixed}"
    panel['speakers'] = fixed
    return note

def tidy_board(board):
    """Repairs mechanical slips in place and returns notes about the speaker repairs."""
    notes = []
    for page in board.get('pages', []) if isinstance(board, dict) else []:
        for panel in page.get('panels', []) if isinstance(page, dict) else []:
            tidy_crop(panel)
            note = tidy_speakers(panel)
            if note: notes.append(note)
    return notes

# Shared by the storyboard builder and the page rewrite, so both keep faces and expressions consistent.
EXPRESSION_RULE = '各コマ人物のexpressionは、標準の表情（neutral/smile/surprised/crying/angry/embarrassed）がそのコマの感情にはっきり合い、かつ顔が画面に十分見える構図のときだけ指定する。息を切らす・疲れ・緊張・ためらいなど標準にない表情、手元や小物の寄り・後ろ姿・遠景など顔が見えない・小さい構図、動作promptに表情を書いたコマはcustomにする。neutralは本当に無表情のときだけ使い、迷ったらcustomにする。customの表情は動作promptへ英語で書く。'
PROMPT_MODE_RULE = 'prompt_mode=literalのコマは場面promptと人物promptが完成した送信本文で、台帳や品質語を合成しない。この場合は既存の外見・衣装・画風を本文から削らない。catalogでは台帳を合成する。既存のprompt_mode・conditioning・人物negative/negative_jaは依頼と関係なければ保持する。'

def object_schema(properties, required=()):
    return {'type':'object', 'properties':properties, 'required':list(required), 'additionalProperties':False}

def board_schema(page_count=None, panel_ja=True):
    """panel_ja=False leaves the panel-level Japanese out of a new storyboard: the host translates it afterwards
    (pageplan.fill_ja), which keeps Strata's reply budget (8,192 tokens) for the English prompts that are sent."""
    string = {'type':'string'}
    array = lambda item: {'type':'array','items':item}
    ja = (lambda **fields: fields) if panel_ja else (lambda **fields: {})
    need = ('prompt_ja',) if panel_ja else ()
    character = object_schema(dict(id=string, prompt=string, **ja(prompt_ja=string), negative=string, **ja(negative_ja=string), outfit_id=string, expression={'type':'string','enum':list(profiles.EXPRESSION_MAP)}, x={'type':'number'}, y={'type':'number'}), ('id','prompt',*need,'outfit_id','expression','x','y'))
    panel = object_schema(dict(id=string, scene=string, prompt=string, **ja(prompt_ja=string), negative=string, **ja(negative_ja=string), location_id=string,
                               prop_ids=array(string), characters=array(character), dialogue=array(string), speakers=array(string),
                               seed={'type':['integer','null']}, crop=CROP_SCHEMA, prompt_mode={'type':'string','enum':['catalog','literal']}, conditioning=prompt_options.schema()),
                          ('scene','prompt',*need,'location_id','characters','dialogue','speakers'))
    preferred=['three','three-wide','four','two','splash','P3-21-straight','P3-12-lean-right','P4-211','P4-22-stagger','P5-122-equal-normal-straight','P6-222-equal-normal-straight']
    choices=[k for k in preferred if k in db.TEMPLATES]
    page = object_schema(dict(template={'type':'string','enum':choices, 'description':', '.join(f'{k}={len(db.TEMPLATES[k])}コマ' for k in choices)}, panels={**array(panel),'minItems':1,'maxItems':7}), ('template','panels'))
    outfit = object_schema({k:string for k in profiles.OUTFIT_FIELDS}, profiles.OUTFIT_FIELDS)
    cast = object_schema(dict(id=string, name=string, appearance=string, appearance_ja=string, voice=string, voice_ja=string,
                             outfits={'type':'array','items':outfit,'minItems':1,'maxItems':10}, default_outfit_id=string),
                         ('id','name','appearance','appearance_ja','voice','voice_ja','outfits','default_outfit_id'))
    catalog = object_schema(dict(id=string, name=string, description=string, description_ja=string), ('id','name','description','description_ja'))
    return object_schema(dict(title=string, style=string, characters=array(cast), locations=array(catalog),
                              props=array(catalog), pages={**array(page), **({'minItems':page_count,'maxItems':page_count} if page_count else {})}, handoff=string), ('pages','handoff'))

def bilingual_board(board, project=None, allow_untranslated_panels=False):
    """Requires the Japanese side of the catalogs. Panel-level Japanese (prompt_ja, negative_ja, each person's prompt_ja) is also required
    unless allow_untranslated_panels: then it is counted and returned, to be filled by the translation step."""
    missing=[]; untranslated=[]
    known = {k: {x.get('id') for x in (project or {}).get(k, [])} for k in ('characters','locations','props')}
    if not isinstance(board, dict): raise db.StudioError('字コンテはJSONオブジェクトです')
    def objects(value, path):
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise db.StudioError(path+'はJSONオブジェクトの配列です。文字列で包まないでください')
        return value
    def require(item,fields,path,panel=False):
        for field in fields:
            if not isinstance(item.get(field),str) or not item[field].strip(): (untranslated if panel and allow_untranslated_panels else missing).append(path+'/'+field)
    for kind in ('characters','locations','props'):
        for i,item in enumerate(objects(board.get(kind,[]),kind)):
            if item.get('id') in known[kind]:  # existing entry: only new outfits are kept, the rest is ignored by the host
                if kind=='characters':
                    for j,outfit in enumerate(objects(item.get('outfits',[]),f'characters/{i}/outfits')): require(outfit,('prompt','prompt_ja'),f'characters/{i}/outfits/{j}')
                continue
            require(item,('appearance_ja','voice','voice_ja') if kind=='characters' else ('description_ja',),f'{kind}/{i}')
            if kind=='characters':
                if not item.get('outfits'): missing.append(f'characters/{i}/outfits')
                for j,outfit in enumerate(objects(item.get('outfits',[]),f'characters/{i}/outfits')): require(outfit,('prompt','prompt_ja'),f'characters/{i}/outfits/{j}')
    for i,page in enumerate(objects(board.get('pages',[]),'pages')):
        for j,panel in enumerate(objects(page.get('panels',[]),f'pages/{i}/panels')):
            base=f'pages/{i}/panels/{j}';require(panel,('prompt_ja',),base,True)
            if panel.get('negative'):require(panel,('negative_ja',),base,True)
            for k,actor in enumerate(objects(panel.get('characters',[]),base+'/characters')):
                require(actor,('prompt_ja',),base+f'/characters/{k}',True)
                if actor.get('negative'): require(actor,('negative_ja',),base+f'/characters/{k}',True)
    if missing:raise db.StudioError('日本語訳・口調の未入力を補い、全体を再送してください: '+', '.join(missing[:12]))
    return len(untranslated)

def chat(messages, tools, timeout):
    if llm.current() == 'grok':
        return llm.grok_chat(messages, tools, timeout)
    return local_ai.chat(messages, tools, timeout)

def user_request(instruction, page_count, title, panel_id=None):
    """The host states the fixed part of the job so a blank or short UI instruction still works."""
    if panel_id: return instruction
    base = f'原作「{title}」を、read_sourceとread_projectで確認してから正確に{page_count}ページの字コンテにし、save_storyboardで保存してください。'
    return base + (f'\n構成の希望: {instruction.strip()}' if instruction.strip() else '')

def run(project_id, instruction, page_count=1, panel_id=None, progress=None, transport=chat, append=False, source_section=None):
    db.text_field(instruction, '依頼', 4000, empty=not panel_id)
    if type(page_count) is not int or not 1 <= page_count <= 4:
        raise db.StudioError('1回の字コンテ作成は1〜4ページです')
    folder = db.directory(project_id)
    initial = db.load(project_id)
    if panel_id: db.panel_by_id(initial, panel_id)
    elif initial['pages'] and not append:
        raise db.StudioError('続きのページはappendを指定してください。既存ページは保持されます')
    if not panel_id and len(initial['pages']) + page_count > db.MAX_PAGES:
        raise db.StudioError('作品は最大24ページです')
    sections = db.source_sections(project_id)
    if source_section is None:
        page_number = next((n for n, page in enumerate(initial['pages'], 1) if any(c['id'] == panel_id for c in page['panels'])), 1)
        source_section = next((b['source_section'] for b in initial.get('batches', []) if b['start_page'] <= page_number <= b['end_page']), 0)
    if type(source_section) is not int or not 0 <= source_section < len(sections): raise db.StudioError('原作の章番号が不正です')
    selected_source = sections[source_section]
    # A page plan (pageplan.py) says what each new page holds; the call then draws only its rows, from the chapters they name.
    plan_rows = [] if panel_id else pageplan.rows_for(initial, len(initial['pages']) + 1, page_count)
    if plan_rows:
        used = sorted({r['section'] for r in plan_rows}); source_section = used[0]
        preface, bodies = pageplan.split_preface(sections)
        selected_source = dict(index=source_section, title='、'.join(sections[i]['title'] for i in used),
                               text='\n\n'.join(([preface] if preface else []) + [bodies[i]['text'] for i in used]))
        instruction = pageplan.instruction(plan_rows) + (f'\n{instruction.strip()}' if instruction.strip() else '')
    if len(selected_source['text']) > 30000: raise db.StudioError('今回の原作が30,000文字を超えています。## 章名 で区切ってください')
    run_id = uuid.uuid4().hex[:12]
    log_path = folder / 'agent-runs' / f'{run_id}.json'
    log = dict(id=run_id, started_at=db.now(), status='running', project_id=project_id, provider=llm.current(),
               panel_id=panel_id, instruction=instruction, source_section=source_section, source_title=selected_source['title'],
               page_start=len(initial['pages'])+1 if not panel_id else None, page_plan=bool(plan_rows), events=[])
    schema = db.read_json(db.ROOT / 'templates' / 'board.example.json')
    schema.pop('output_method', None)  # The host owns this immutable attribute.
    system = (folder / 'AGENTS.md').read_text(encoding='utf-8') + '\n' + (folder / 'SKILL.md').read_text(encoding='utf-8')
    if not panel_id:
        system += '\nJSON形式の参考例（内容は模倣せず原作を使う）:\n' + json.dumps(schema, ensure_ascii=False)
    system += '\n保存ツールにはJSONオブジェクトを渡す。説明文だけで完了しない。保存に成功した時点でホストが終了する。'
    from .workspace import effective_system
    settings=effective_system(initial)
    system += '\nユーザー共通制作指示:\n'+settings.get('storyboard','')+'\n'+settings.get('constraints','')
    if panel_id: system += '\n英語prompt/negativeを変えたら、同じ意味の日本語訳をprompt_ja/negative_jaにも入れる。'
    else: system += '\nコマのprompt_ja・negative_ja・コマ内人物のprompt_jaは出力しない（保存後にホストが英語から翻訳する。SKILL.mdの_jaの指示よりこちらを優先）。新規の人物・背景・小物・衣装の日本語（appearance_ja/description_ja/voice_ja/衣装のprompt_ja）は入れる。'
    system += '\n台帳の英語appearance/descriptionと同じ意味の日本語訳をappearance_ja/description_jaへ記載。人物の口調はvoice（英語）とvoice_ja（日本語）。台詞の話者はspeakersへ同じ順の人物ID（そのコマのcharactersに含む人物だけ。画面に出ない人の台詞は空文字）、不明は空文字。固定外見・背景・画風は各コマのpromptに複写せず、台帳をIDで参照。'
    system += '\n新規人物のappearanceは年齢・顔・髪・体格等で、衣装や場面の表情を入れない。原作に必要な衣装だけoutfitsへ1〜10件（id/name/prompt/prompt_ja）作りdefault_outfit_idを指定。コマ人物はoutfit_idで服を1種類選択しexpressionで表情を選ぶ。表情指定がcustom以外ならpromptには動作と向きだけを書き、服・表情を重複させない。既存人物に衣装がない場合outfit_idは空文字で固定外見を保つ。'
    system += '\n共通の絵柄styleはユーザー設定を優先し、最初のページでも変えない。'
    system += '\n' + EXPRESSION_RULE
    from . import images
    system += '\n' + images.prompt_rule(initial)
    if panel_id:
        system += f'\n今回は既存コマ {panel_id} だけ修正する。原作にない人物や結末は作らない。'
        system += '\nupdate_panelのpatchには変更項目だけ入れる。idやpanel_idは絶対に入れない（対象IDはホストが固定済み）。例: {"patch":{"dialogue":["明日ね"]}}'
    else:
        first = len(initial['pages'])+1
        system += f'\n今回は原作「{selected_source["title"]}」から正確に{page_count}ページ（通し{first}〜{first+page_count-1}）だけを追記する。'
        system += '\nread_sourceとread_projectを読み、直前の出来事・固定設定を継承する。原作全体を今回のページ内へ圧縮しない。'
        system += '\n既存style/characters/locations/propsは変更禁止。既存人物や場所はIDだけで参照し、台帳には新規項目だけ渡す。'
        system += '\n各コマにlocation_id、必要なprop_idsを書く。locationのdescriptionは固定した建物・家具・色・材質、天候とカメラは各コマpromptに分ける。'
        system += '\n同じ建物でも室内と外観は別location_id。小物のdescriptionには色・材質・形だけ。壊れている、置かれている場所、点灯など一時状態は各コマpromptにだけ書く。'
        if plan_rows: system += f'\npagesは必ず{page_count}要素。各ページは依頼文の「ページの割り当て」の出来事と台詞だけを、指定のコマ数前後で描く。割り当ての後の出来事は次回なので作らず、handoffに書く。'
        else: system += f'\npagesは必ず{page_count}要素。{page_count+1}ページ目以降は次回の依頼なので作らない。選択した章を{page_count}ページへ順序どおり均等に配分し、詰め込みすぎず1ページ3〜5コマで調整する。章が余りそうなら、余る分は作らずhandoffに書く。'
        system += '\n既存人物・背景・小物は台帳へ再出力しない（出しても無視され、変更もされない）。コマ側でIDとoutfit_idだけを参照する。台帳（characters/locations/props）には、まだ無い新規IDだけを入れ、無ければ空配列にする。例外として、既存人物に新しい衣装（浴衣・冬服など）が要るときは、その人物をidとoutfits（新しい衣装の1件以上、id/name/prompt/prompt_ja）だけで台帳に入れる。既存の衣装は入れない。コマのoutfit_idは台帳にある衣装IDだけを使う。'
        system += '\n1つの台詞は1つのコマにだけ入れる。長い台詞を分けるときは文を分け、同じ文を隣のコマへ繰り返さない。'
        system += '\n今回の終わりの状態と未解決事項をhandoff（日本語3000文字以内）に記録する。pagesには新しいページだけ。コマIDはホストが採番する。'
    tools = [tool('read_source', '作品の原作本文を読む'), tool('read_project', '現在の作品JSONを読む')]
    if panel_id:
        character_schema = board_schema()['properties']['pages']['items']['properties']['panels']['items']['properties']['characters']['items']
        patch_schema = {'type': 'object', 'properties': {
            'scene': {'type': 'string'}, 'prompt': {'type': 'string'}, 'prompt_ja': {'type':'string'}, 'negative': {'type': 'string'}, 'negative_ja': {'type':'string'},
            'location_id': {'type': 'string'}, 'prop_ids': {'type': 'array', 'items': {'type': 'string'}},
            'dialogue': {'type': 'array', 'items': {'type': 'string'}},
            'speakers': {'type':'array','items':{'type':'string'}},
            'characters': {'type': 'array', 'items': character_schema},
            'seed': {'type': ['integer', 'null']}, 'crop': CROP_SCHEMA,
            'prompt_mode': {'type':'string','enum':['catalog','literal']}, 'conditioning': prompt_options.schema()},
            'additionalProperties': False, 'description': '変更する項目だけ。idとpanel_idは禁止。'}
        tools.append(tool('update_panel', '指定されたコマを保存して完了する',
                          {'patch': patch_schema}, ['patch']))
    else:
        definition = tool('save_storyboard', '新しい1〜4ページを追記して完了。pagesはページの配列。handoffは全ページの後に置く')
        definition['function']['parameters'] = board_schema(page_count, panel_ja=False)
        tools.append(definition)
        system += '\n保存はsave_storyboardの引数へ直接pages, handoff, 新規台帳を渡す。storyboardで包まない。各ページはtemplate/panelsの2項目。handoffはページ内ではなく最後のトップレベル1個。'
    system += '\n' + PROMPT_MODE_RULE
    messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': user_request(instruction, page_count, selected_source['title'], panel_id)}]
    repeats = Counter()
    failures = 0
    read_ok = False
    context_ok = bool(panel_id)
    deadline = time.monotonic() + 600
    with db.exclusive(folder / '.agent.lock'):
        try:
            for turn in range(8):
                remaining = deadline - time.monotonic()
                if remaining <= 0: raise db.StudioError(llm.label()+'は10分の制限に達しました')
                if len(json.dumps(messages, ensure_ascii=False)) > 100000:
                    raise db.StudioError('入力が長すぎます。原作を章・場面ごとに分割してください')
                if progress: progress(f'{llm.label()}処理中 {turn + 1}/8')
                message, usage = transport(messages, tools, min(180, remaining))
                log['events'].append(dict(turn=turn + 1, message=message, usage=usage))
                db.dump(log_path, log)
                messages.append({k: message[k] for k in ('role', 'content', 'tool_calls') if k in message})
                calls = message.get('tool_calls') or []
                if not calls:
                    failures += 1
                    if failures >= 2: raise db.StudioError('保存ツールを使わず応答を繰り返したため停止しました')
                    messages.append({'role': 'user', 'content': '説明は不要です。必要な読取の後、保存ツールを呼んで完了してください。'})
                    continue
                for call in calls[:4]:
                    f = call.get('function', {})
                    name, args_text = f.get('name'), f.get('arguments', '{}')
                    signature = hashlib.sha256((str(name) + str(args_text)).encode()).hexdigest()
                    repeats[signature] += 1
                    if repeats[signature] > 2: raise db.StudioError('同じツール呼出しの反復を検出したため停止しました')
                    try:
                        args = json.loads(args_text) if isinstance(args_text, str) else args_text
                        if not isinstance(args, dict): raise db.StudioError('引数はJSONオブジェクトです')
                        if name == 'read_source':
                            output = dict(source=selected_source['text'], section=selected_source['title'])
                            if not panel_id: read_ok = True
                        elif name == 'read_project':
                            output = dict(title=initial['title'], style=initial['style'], characters=initial['characters'],
                                          locations=initial.get('locations', []), props=initial.get('props', []),
                                          target_panel=db.panel_by_id(initial, panel_id)) if panel_id else db.continuity_context(initial)
                            context_ok = True
                            if panel_id: read_ok = True
                        elif name == 'save_storyboard' and not panel_id:
                            if not read_ok: raise db.StudioError('先にread_sourceを呼んでください')
                            if not context_ok: raise db.StudioError('先にread_projectで共通設定と前回の状態を確認してください')
                            board = args
                            tidied = tidy_board(board)
                            if tidied: log['tidied'] = tidied
                            if initial.get('workspace_version')==2:
                                untranslated = bilingual_board(board, initial, allow_untranslated_panels=True)
                                if untranslated: log['untranslated_panel_fields'] = untranslated
                            result = db.append_board(project_id, initial['revision'], board, source_section, page_count)
                            log.update(status='complete', revision=result['revision'], finished_at=db.now())
                            return result
                        elif name == 'update_panel' and panel_id:
                            if not read_ok: raise db.StudioError('先にread_projectを呼んでください')
                            tidy_crop(args.get('patch'))
                            result = db.edit_panel(project_id, initial['revision'], panel_id, args.get('patch'), consumed_input='agent-instruction')
                            log.update(status='complete', revision=result['revision'], finished_at=db.now())
                            return result
                        else: raise db.StudioError('許可されていないツールです')
                    except (db.StudioError, ValueError, TypeError, KeyError) as exc:
                        failures += 1
                        output = {'error': str(exc)[:500]}
                        log['events'][-1].setdefault('tool_errors', []).append(dict(tool=name, error=output['error']))
                        if failures >= 2: raise db.StudioError('ツールの失敗が2回に達したため停止: ' + str(exc)[:300]) from None
                    messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': json.dumps(output, ensure_ascii=False)})
            raise db.StudioError(llm.label()+'は8ターンの制限に達しました')
        except Exception as exc:
            log.update(status='failed', error=str(exc)[:500], finished_at=db.now())
            raise
        finally:
            db.dump(log_path, log)
