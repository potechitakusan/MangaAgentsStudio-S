"""Validated, revisioned project storage shared by agents and the UI."""
from __future__ import annotations
import copy
import json
import re
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from . import layouts

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'work'
LOCK = threading.RLock()
MAX_PAGES = 24
PANEL_FIELDS = ('id', 'scene', 'prompt', 'prompt_ja', 'negative', 'negative_ja', 'characters', 'dialogue', 'speakers', 'seed', 'crop', 'image_transform', 'location_id', 'prop_ids', 'needs_edit')
TEMPLATES = {
    'three': [(0, 0, 1, .44), (.51, .46, .49, .54), (0, .46, .49, .54)],
    'three-wide': [(0, 0, 1, .30), (0, .32, 1, .30), (0, .64, 1, .36)],
    'four': [(.51, 0, .49, .42), (0, 0, .49, .42), (0, .44, 1, .24), (0, .70, 1, .30)],
    'two': [(0, 0, 1, .42), (0, .44, 1, .56)],
    'splash': [(0, 0, 1, 1)],
}
TEMPLATES.update(layouts.RECTANGLES)

class StudioError(Exception):
    pass

def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def dump(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)

def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', value):
        raise StudioError('IDは英数字・ハイフン・アンダースコアで1〜64文字です')
    return value

def directory(project_id):
    path = WORK / identifier(project_id)
    if not path.resolve().is_relative_to(WORK.resolve()):
        raise StudioError('作品フォルダ外にはアクセスできません')
    return path

def asset_path(project_id, relative):
    if not isinstance(relative, str) or '\\' in relative or ':' in relative:
        raise StudioError('画像パスが不正です')
    base = directory(project_id).resolve()
    target = (base / relative).resolve()
    if not target.is_relative_to(base) or target.suffix.lower() not in ('.png', '.json', '.zip', '.psd', '.md'):
        raise StudioError('作品の画像・設定・書出しファイルのみ利用できます')
    return target

@contextmanager
def exclusive(path):
    """Cross-process lock. Stale locks deliberately require inspection, never steal."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('x', encoding='utf-8') as f:
            f.write(now())
    except FileExistsError:
        raise StudioError(f'処理中または前回処理の要確認: {path.name}') from None
    try:
        yield
    finally:
        path.unlink(missing_ok=True)

def load(project_id):
    path = directory(project_id) / 'project.json'
    if not path.exists():
        raise StudioError('作品がありません')
    result = read_json(path)
    from . import images
    images.for_project(result)
    return result

def text_field(value, name, limit=4000, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise StudioError(f'{name}: 文字列（1〜{limit}文字）が必要です')

def validate_board(board, allow_incomplete=False):
    from . import profiles
    if not isinstance(board, dict):
        raise StudioError('字コンテはJSONオブジェクトです')
    text_field(board.get('title'), 'title', 120)
    text_field(board.get('style'), 'style', 2000, allow_incomplete)
    cast = board.get('characters')
    if not isinstance(cast, list) or len(cast) > 12:
        raise StudioError('charactersは最大12人の配列です')
    ids = set()
    for c in cast:
        if not isinstance(c, dict): raise StudioError('人物はオブジェクトです')
        cid = identifier(c.get('id'))
        if cid in ids: raise StudioError('人物IDが重複しています')
        ids.add(cid)
        text_field(c.get('name'), 'name', 80)
        text_field(c.get('appearance'), 'appearance', 2000, allow_incomplete)
        for field in ('appearance_ja','voice','voice_ja'):
            text_field(c.get(field,''), field, 2000, True)
        profiles.validate_character(c, allow_incomplete)
    location_ids = validate_catalog(board.get('locations', []), 'locations', allow_incomplete)
    prop_ids = validate_catalog(board.get('props', []), 'props', allow_incomplete)
    pages = board.get('pages')
    if not isinstance(pages, list) or not 1 <= len(pages) <= MAX_PAGES:
        raise StudioError(f'pagesは1〜{MAX_PAGES}ページの配列です')
    pids = set()
    for page_number, page in enumerate(pages, 1):
        if not isinstance(page, dict): raise StudioError('ページはオブジェクトです')
        template = page.get('template')
        if template not in TEMPLATES: raise StudioError('template: ' + ', '.join(TEMPLATES))
        panels = page.get('panels')
        if not isinstance(panels, list) or len(panels) != len(TEMPLATES[template]):
            actual = len(panels) if isinstance(panels, list) else '配列以外'
            raise StudioError(f'{page_number}ページ目の{template}: コマ数は{len(TEMPLATES[template])}個が必要ですが現在{actual}個です。templateとpanelsを一致させてください')
        for panel in panels:
            if not isinstance(panel, dict): raise StudioError('コマはオブジェクトです')
            pid = identifier(panel.get('id'))
            if pid in pids: raise StudioError('コマIDが重複しています')
            pids.add(pid)
            validate_panel(panel, ids, allow_incomplete)
            for actor in panel.get('characters', []):
                profiles.validate_actor(next(c for c in cast if c['id']==actor['id']), actor)
            location_id = panel.get('location_id')
            if location_id and location_id not in location_ids: raise StudioError('未登録のlocation_idです')
            if location_ids and not location_id: raise StudioError(f'{pid}: location_idで背景を指定してください')
            items = panel.get('prop_ids', [])
            if not isinstance(items, list) or len(items) > 8 or any(not isinstance(x, str) or x not in prop_ids for x in items):
                raise StudioError('prop_idsは登録済み小物IDの配列です')
    return board

def validate_catalog(catalog, name, allow_incomplete=False):
    if not isinstance(catalog, list) or len(catalog) > 32: raise StudioError(f'{name}は最大32項目です')
    ids = set()
    for c in catalog:
        if not isinstance(c, dict): raise StudioError(f'{name}はオブジェクトの配列です')
        cid = identifier(c.get('id'))
        if cid in ids: raise StudioError(f'{name}のIDが重複しています')
        ids.add(cid)
        text_field(c.get('name'), name + '.name', 80)
        text_field(c.get('description'), name + '.description', 2000, allow_incomplete)
        text_field(c.get('description_ja',''), name + '.description_ja', 2000, True)
    return ids

def source_sections(project_id):
    saved=load(project_id)
    source = saved.get('source',(directory(project_id) / 'source.txt').read_text(encoding='utf-8-sig'))
    matches = list(re.finditer(r'^##\s+(.+)$', source, re.M))
    if not matches: return [dict(index=0, title='原作全体', text=source)]
    preface = source[:matches[0].start()].strip()
    return [dict(index=i, title=match.group(1).strip(), text=((preface + '\n\n') if preface else '') + source[match.start():matches[i+1].start() if i+1<len(matches) else len(source)].strip())
            for i, match in enumerate(matches)]

def continuity_context(p):
    return dict(title=p['title'], style=p['style'], characters=p['characters'], locations=p.get('locations', []),
                props=p.get('props', []), completed_pages=len(p['pages']), next_page=len(p['pages'])+1,
                recent_handoffs=[dict(start_page=b['start_page'], end_page=b['end_page'], handoff=b['handoff']) for b in p.get('batches', [])[-3:]],
                previous_page=[{k: panel[k] for k in PANEL_FIELDS if k in panel} for panel in p['pages'][-1]['panels']] if p['pages'] else [])

def validate_panel(p, cast_ids, allow_incomplete=False):
    text_field(p.get('scene'), 'scene', 2000, allow_incomplete)
    text_field(p.get('prompt'), 'prompt', 5000, allow_incomplete)
    text_field(p.get('negative', ''), 'negative', 3000, True)
    for field in ('prompt_ja','negative_ja'): text_field(p.get(field,''),field,5000,True)
    chars = p.get('characters', [])
    if not isinstance(chars, list) or len(chars) > 6: raise StudioError('コマ人物は最大6人です')
    used = set()
    for c in chars:
        if not isinstance(c, dict) or c.get('id') not in cast_ids: raise StudioError('未登録の人物IDです')
        if c['id'] in used: raise StudioError('コマ内の人物IDが重複しています')
        used.add(c['id'])
        text_field(c.get('prompt'), '人物prompt', 2000, allow_incomplete)
        text_field(c.get('prompt_ja',''), '人物prompt_ja', 2000, True)
        for key in ('x', 'y'):
            v = c.get(key, .5)
            if type(v) not in (int, float) or not 0 <= v <= 1: raise StudioError('人物座標は0〜1です')
    dialogue = p.get('dialogue', [])
    if not isinstance(dialogue, list) or len(dialogue) > 3: raise StudioError('dialogueは最大3個です')
    for line in dialogue: text_field(line, 'dialogue', 80)
    speakers=p.get('speakers')
    if speakers is not None and (not isinstance(speakers,list) or len(speakers)!=len(dialogue) or any(not isinstance(s,str) or (s and s not in used) for s in speakers)):
        where = f"{p.get('id') or 'コマ'}: " if isinstance(p, dict) else ''
        raise StudioError(f'{where}speakersは台詞と同じ個数で、このコマの人物IDまたは空文字です。台詞{len(dialogue)}個・speakers{speakers}・このコマの人物{sorted(used)}。画面に出ない人の台詞はspeakersを空文字にするか、その人物をcharactersに加える')
    seed = p.get('seed')
    if seed is not None and (type(seed) is not int or not 0 <= seed <= 4294967295):
        raise StudioError('seedは空欄または0〜4294967295の整数です')
    crop = p.get('crop', [.5, .5])
    if not isinstance(crop, list) or len(crop) != 2 or any(type(v) not in (int, float) or not 0 <= v <= 1 for v in crop):
        raise StudioError(f'cropは画像を当てる中心座標[x,y]（各0〜1）の2個です。受け取った値: {str(crop)[:60]}')
    if 'image_transform' in p:
        validate_image_transform(p['image_transform'])

def validate_image_transform(value):
    if not isinstance(value, dict) or set(value) != {'x', 'y', 'scale'}:
        raise StudioError('画像位置はx・y・scaleで指定してください')
    for key, low, high in (('x', -2, 2), ('y', -2, 2), ('scale', .1, 4)):
        if type(value[key]) not in (int, float) or not low <= value[key] <= high:
            raise StudioError(f'画像位置の{key}は{low}〜{high}です')

def create(project_id, title, source, output_method='novelai'):
    from . import images
    images.method(output_method)
    text_field(title, '作品名', 120)
    text_field(source, '原作', 200000)
    folder = directory(project_id)
    with LOCK:
        if folder.exists(): raise StudioError('同じIDの作品が存在します')
        folder.mkdir(parents=True)
        (folder / 'source.txt').write_text(source, encoding='utf-8')
        for name in ('AGENTS.md', 'SKILL.md', 'board.example.json'):
            origin = ROOT / 'templates' / name if name != 'SKILL.md' else ROOT / 'skills' / 'manga-production' / name
            (folder / name).write_text(origin.read_text(encoding='utf-8'), encoding='utf-8')
        project = dict(id=project_id, title=title, revision=0, style='anime coloring, clean lineart, cinematic lighting',
                       characters=[], locations=[], props=[], batches=[], pages=[], created_at=now())
        project.update(output_method=output_method, image_settings=dict(anime_finish=False, denoise=.3,
                       reference_mode='references' if output_method == 'qwen' else 'prompt'), references={})
        example = read_json(folder / 'board.example.json')
        example['output_method'] = output_method
        dump(folder / 'board.example.json', example)
        dump(folder / 'project.json', project)
        return project

def mutate(project_id, revision, fn):
    with LOCK, exclusive(directory(project_id) / '.project.lock'):
        old = load(project_id)
        if type(revision) is not int or revision != old['revision']:
            raise StudioError('編集競合: 最新の作品を読み直してください（未保存の内容はコピーして退避できます）')
        new = copy.deepcopy(old)
        fn(new)
        from . import images
        images.require_match(old, new)
        images.for_project(new)
        if new['pages']: validate_board(new, allow_incomplete=new.get('workspace_version')==2)
        new['revision'] += 1
        new['updated_at'] = now()
        dump(directory(project_id) / 'history' / f'project-{old["revision"]:05}.json', old)
        dump(directory(project_id) / 'project.json', new)
        return new

def import_board(project_id, revision, board):
    validate_board(board)
    def apply(p):
        from . import images
        images.require_match(p, board)
        if p['pages']: raise StudioError('既存作品への全体上書き取込はできません。新規作品で取り込んでください')
        # Input cannot smuggle image paths, histories or revision fields.
        p.update({k: copy.deepcopy(board[k]) for k in ('title', 'style', 'characters')})
        p.update({k: copy.deepcopy(board.get(k, [])) for k in ('locations', 'props')})
        p['pages'] = [{'template': page['template'], 'panels': [
            {k: copy.deepcopy(panel[k]) for k in PANEL_FIELDS if k in panel}
            for panel in page['panels']]} for page in board['pages']]
    return mutate(project_id, revision, apply)

def append_board(project_id, revision, board, source_section=0, expected_pages=None):
    """Merge fixed catalogs, then append only. Existing pages/images never enter the patch."""
    if not isinstance(board, dict) or not isinstance(board.get('pages'), list): raise StudioError('pagesを含むJSONが必要です')
    count = len(board['pages'])
    if not 1 <= count <= 4 or (expected_pages is not None and count != expected_pages):
        want = expected_pages or '1〜4'
        raise StudioError(f'pagesが{count}ページ受信されました。今回はちょうど{want}ページです。pagesを{want}要素にし、原作が残る分は次回に回してhandoffへ書いてください')
    sections = source_sections(project_id)
    if type(source_section) is not int or not 0 <= source_section < len(sections): raise StudioError('原作の章番号が不正です')
    text_field(board.get('handoff', ''), 'handoff', 3000, True)
    ignored = []
    def apply(p):
        ignored.clear()
        if len(p['pages']) + count > MAX_PAGES: raise StudioError('作品は最大24ページです')
        if p['pages'] and board.get('style', p['style']) != p['style']:
            raise StudioError('共通styleは変更できません。既存のstyleをそのまま使うか省略してください')
        if not p['pages']:
            p['title'] = board.get('title', p['title'])
            if p.get('workspace_version')!=2: p['style'] = board.get('style', p['style'])
        for key in ('characters', 'locations', 'props'):
            known = {x['id']: x for x in p.get(key, [])}
            incoming = board.get(key, [])
            if not isinstance(incoming, list): raise StudioError(key + 'は配列です')
            for entry in incoming:
                if not isinstance(entry, dict): raise StudioError(key + 'の項目が不正です')
                cid = identifier(entry.get('id'))
                fields = ('id','name','appearance','appearance_ja','voice','voice_ja','outfits','default_outfit_id') if key == 'characters' else ('id','name','description','description_ja')
                clean = {field: entry.get(field,'') for field in fields if field in entry or field not in ('outfits','default_outfit_id')}
                if cid in known:
                    # Existing fixed entries are never overwritten; a re-output is dropped and noted, not a failure.
                    # The one allowed growth is a brand-new outfit id on an existing character.
                    if any(field in entry and clean[field] != known[cid].get(field,'') for field in fields if field not in ('outfits','default_outfit_id')): ignored.append(f'{key}/{cid}')
                    if key == 'characters' and isinstance(entry.get('outfits'), list):
                        have = {o['id'] for o in known[cid].get('outfits', [])}
                        fresh = [o for o in entry['outfits'] if isinstance(o, dict) and o.get('id') not in have]
                        if fresh: known[cid] = {**known[cid], 'outfits': known[cid].get('outfits', []) + copy.deepcopy(fresh)}
                else: known[cid] = clean
            p[key] = list(known.values())
        first = len(p['pages']) + 1
        for offset, page in enumerate(board['pages']):
            if not isinstance(page, dict) or not isinstance(page.get('panels'), list): raise StudioError('ページ形式が不正です')
            clean_panels = [{k: copy.deepcopy(panel[k]) for k in PANEL_FIELDS if k in panel} for panel in page['panels'] if isinstance(panel,dict)]
            if len(clean_panels) != len(page['panels']): raise StudioError('コマはオブジェクトです')
            for i, panel in enumerate(clean_panels,1): panel['id'] = f'p{first + offset}-{i}'
            if p.get('workspace_version')==2:
                from .workspace import clean_panel
                clean_panels=[clean_panel(c,None,p['locations']) for c in clean_panels]
            added=dict(number=first + offset, template=page.get('template'), panels=clean_panels)
            p['pages'].append(added)
            if p.get('workspace_version')==2:
                from . import wholepage
                added.update(id=f'page-{first+offset}',whole=dict(format='prose',seed=None,linked=True,draft=wholepage.from_panels(p,first+offset)))
        handoff = board.get('handoff') or ' / '.join(c['scene'] for c in p['pages'][-1]['panels'])
        # The composition wish typed on screen was for this batch; once it is saved, the next batch starts from an empty box.
        (p.get('ui_inputs') or {}).pop('board-instruction', None)
        p.setdefault('batches', []).append(dict(start_page=first, end_page=first+count-1, source_section=source_section,
                                               source_title=sections[source_section]['title'], handoff=handoff, created_at=now(),
                                               **({'ignored_catalog': sorted(set(ignored))} if ignored else {})))
    return mutate(project_id, revision, apply)

def generation_targets(p, panel_id=None, start_page=None, end_page=None):
    if panel_id: return [panel_by_id(p, panel_id)['id']]
    first = 1 if start_page is None else start_page
    last = len(p['pages']) if end_page is None else end_page
    if type(first) is not int or type(last) is not int or not 1 <= first <= last <= len(p['pages']):
        raise StudioError('生成するページ範囲が不正です')
    if last - first >= 4: raise StudioError('一度に4ページ以内の範囲を指定してください')
    targets = [c['id'] for page in p['pages'][first-1:last] for c in page['panels'] if not c.get('image')]
    if len(targets) > 16: raise StudioError('一度に16コマまでです。4ページ以内の範囲を指定してください')
    if not targets: raise StudioError('この範囲に未生成コマはありません')
    return targets

def panels(project):
    return [panel for page in project['pages'] for panel in page['panels']]

def panel_by_id(project, panel_id):
    for panel in panels(project):
        if panel['id'] == panel_id: return panel
    raise StudioError('コマがありません')

def edit_panel(project_id, revision, panel_id, patch, consumed_input=None):
    """consumed_input: a remembered screen field (the AI request) that this successful edit used up and clears."""
    allowed = set(PANEL_FIELDS) - {'id'}
    if not isinstance(patch, dict) or not patch: raise StudioError('patchに変更項目を入れてください')
    if set(patch) - allowed:
        raise StudioError('patchから削除すべき項目: ' + ', '.join(sorted(set(patch) - allowed)) + '。対象コマIDはホストが設定済みです')
    def apply(p):
        target=panel_by_id(p,panel_id)
        target.update(copy.deepcopy(patch))
        if 'dialogue' in patch and 'speakers' not in patch and 'speakers' in target and len(target['speakers'])!=len(target['dialogue']): target['speakers']=['']*len(target['dialogue'])
        target['needs_edit']=False
        if consumed_input: (p.get('ui_inputs') or {}).pop(consumed_input, None)
        from . import wholepage
        for i,page in enumerate(p['pages'],1):
            if any(c['id']==panel_id for c in page['panels']) and page.get('whole',{}).get('linked',p.get('workspace_version')==2):
                page.setdefault('whole',{}).update(linked=True,draft=wholepage.from_panels(p,i))
    return mutate(project_id, revision, apply)

def list_projects():
    WORK.mkdir(exist_ok=True)
    items = []
    for f in sorted(WORK.glob('*/project.json')):
        try:
            identifier(f.parent.name)
            if not f.resolve().is_relative_to(WORK.resolve()): continue
            p = read_json(f)
            if p['id'] != f.parent.name: continue
            items.append((p.get('created_at', ''), dict(id=p['id'], title=p['title'], revision=p['revision'], pages=len(p['pages']), output_method=p.get('output_method'), compatible=p.get('output_method') in ('novelai','qwen'))))
        except (StudioError, OSError, ValueError, KeyError, TypeError):
            continue
    return [item for _, item in sorted(items, key=lambda x: x[0], reverse=True)]   # newest first; ISO UTC strings sort by time
