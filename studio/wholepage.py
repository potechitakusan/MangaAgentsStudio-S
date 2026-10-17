"""Editable page plans and three prompt encodings; not a NovelAI JSON schema."""
from __future__ import annotations
import copy
import json
from . import project as db, profiles

FORMATS = ('prose', 'json', 'yaml')
POSITIONS = {
    'three': ['top wide panel', 'bottom right panel', 'bottom left panel'],
    'three-wide': ['top horizontal panel', 'middle horizontal panel', 'bottom horizontal panel'],
    'four': ['top right panel', 'top left panel', 'middle wide panel', 'bottom wide panel'],
    'two': ['top panel', 'bottom panel'], 'splash': ['full-page panel'],
}

def page_at(project, number):
    if type(number) is not int or not 1 <= number <= len(project['pages']):
        raise db.StudioError('ページ番号が不正です')
    return project['pages'][number-1]

def from_panels(project, number):
    page = page_at(project, number)
    panels = [{k:copy.deepcopy(c[k]) for k in db.PANEL_FIELDS if k in c and k not in ('id','seed','crop')} for c in page['panels']]
    for panel in panels:
        characters = panel.get('characters', [])
        panel.setdefault('speakers', [characters[0]['id'] if len(characters)==1 else '' for _ in panel.get('dialogue', [])])
    return dict(template=page['template'], panels=panels)

def settings(project, number):
    page = page_at(project, number)
    saved = page.get('whole', {})
    return dict(format=saved.get('format', 'prose'), seed=saved.get('seed'),
                draft=copy.deepcopy(saved.get('draft') or from_panels(project, number)))

def validate(project, number, value):
    page_at(project, number)
    if not isinstance(value, dict) or set(value) - {'format','seed','draft'}: raise db.StudioError('ページ設定はformat/seed/draftです')
    if value.get('format') not in FORMATS: raise db.StudioError('送信形式はprose/json/yamlです')
    seed = value.get('seed')
    if seed is not None and (type(seed) is not int or not 0 <= seed <= 4294967295): raise db.StudioError('Seedは空欄または0〜4294967295です')
    draft = value.get('draft')
    if not isinstance(draft, dict) or set(draft) != {'template','panels'}: raise db.StudioError('設計JSONはtemplateとpanelsを含めてください')
    if not isinstance(draft['panels'], list) or len(draft['panels']) > 7: raise db.StudioError('ページ一括は1〜7コマです')
    clean = copy.deepcopy(draft)
    for i, panel in enumerate(clean['panels'], 1):
        if not isinstance(panel, dict) or set(panel) - ((set(db.PANEL_FIELDS) - {'id','seed','crop'}) | {'speakers'}):
            raise db.StudioError('設計のコマには場面・プロンプト・人物・台詞・場所・小物だけを指定してください')
        panel['id'] = f'wp{number}-{i}'
    check = {**project, 'pages':[clean]}
    db.validate_board(check,allow_incomplete=project.get('workspace_version')==2)
    for panel in clean['panels']:
        speakers = panel.get('speakers', ['']*len(panel.get('dialogue', [])))
        ids = {c['id'] for c in panel.get('characters', [])}
        if not isinstance(speakers,list) or len(speakers)!=len(panel.get('dialogue',[])) or any(not isinstance(s,str) or (s and s not in ids) for s in speakers):
            raise db.StudioError('speakersは台詞と同じ個数の配列です。登場人物IDまたは空文字を指定してください')
    text = '\n\n'.join(line for panel in clean['panels'] for line in panel.get('dialogue', []))
    if project.get('text_mode','embedded')=='embedded' and len(text) > 374: raise db.StudioError('一括描画の台詞は改行込み374文字以内に短くしてください')
    if sum(len(c.get('characters', [])) for c in clean['panels']) > 22: raise db.StudioError('1ページの人物登場数は合計22までです')
    return clean

def save(project_id, revision, number, value):
    def apply(project):
        validate(project, number, value)
        page_at(project, number).setdefault('whole', {}).update(copy.deepcopy(value))
    return db.mutate(project_id, revision, apply)

def set_mode(project_id, revision, mode):
    if mode not in ('panels','whole'): raise db.StudioError('生成モードが不正です')
    return db.mutate(project_id, revision, lambda p:p.update(render_mode=mode))

def yaml_text(value, depth=0):
    """Emit a small JSON-compatible YAML subset. No YAML parsing or dependencies."""
    indent = '  ' * depth
    scalar = lambda x:json.dumps(x, ensure_ascii=False)
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            prefix = indent + str(key) + ':'
            nested = isinstance(item,(dict,list)) and bool(item)
            lines.append(prefix + ('\n' + yaml_text(item,depth+1) if nested else ' '+scalar(item)))
        return '\n'.join(lines)
    if isinstance(value, list):
        return '\n'.join(indent+'-\n'+yaml_text(x,depth+1) if isinstance(x,(dict,list)) else indent+'- '+scalar(x) for x in value)
    return indent+scalar(value)

def compile_prompt(project, number, value=None):
    value = value or settings(project, number)
    draft = validate(project, number, value)
    cast = {c['id']:c for c in project['characters']}
    locations = {x['id']:x for x in project.get('locations', [])}
    props = {x['id']:x for x in project.get('props', [])}
    scenes, actors, texts = [], [], []
    shared = {}
    positions=POSITIONS.get(draft['template']) or [f'panel {i}, center x={x+w/2:.2f}, y={y+h/2:.2f}, width={w:.2f}, height={h:.2f}' for i,(x,y,w,h) in enumerate(db.TEMPLATES[draft['template']],1)]
    for i, (panel, rect, position) in enumerate(zip(draft['panels'], db.TEMPLATES[draft['template']], positions),1):
        place = panel.get('location_id')
        if place: shared[place] = locations[place]['description']
        items = [props[k]['description'] for k in panel.get('prop_ids', [])]
        lines = panel.get('dialogue', []) if project.get('text_mode','embedded')=='embedded' else []
        speakers = panel.get('speakers', ['']*len(lines))
        scene = dict(panel=i, position=position, scene=profiles.scene_prompt(panel))
        if place: scene['setting'] = place
        if items: scene['visible_props'] = items
        if lines: scene['dialogue'] = [dict(speaker=speaker, text=line) for speaker,line in zip(speakers,lines)]
        scenes.append(scene)
        texts.extend(lines)
        x,y,w,h = rect
        for character in panel.get('characters', []):
            centers = [dict(x=round(x+w*character.get('x',.5),4), y=round(y+h*character.get('y',.5),4))]
            caption = f"Panel {i}, {position}, character {character['id']}. {profiles.caption(cast[character['id']], character)}"
            spoken = [line for speaker,line in zip(speakers,lines) if speaker==character['id']]
            if spoken: caption += f'. Speaking in a speech bubble inside panel {i}. Text: '+ '\n\n'.join(spoken)
            actors.append(dict(char_caption=caption, centers=centers))
    plan = dict(manga='one complete comic page, right to left reading order', panel_count=len(scenes),
                style=project['style'], settings=shared, panels=scenes)
    if value['format'] == 'json': body = json.dumps(plan,ensure_ascii=False,separators=(',',':'))
    elif value['format'] == 'yaml': body = yaml_text(plan)
    else:
        parts = [f"One complete {len(scenes)}-panel Japanese manga page, right-to-left reading order. {project['style']}."]
        parts += [f"Setting {key}: {description}." for key,description in shared.items()]
        for scene in scenes:
            part = f"Panel {scene['panel']}, {scene['position']}: {scene['scene']}."
            if scene.get('setting'): part += f" Setting: {scene['setting']}."
            if scene.get('visible_props'): part += ' Visible objects: '+', '.join(scene['visible_props'])+'.'
            if scene.get('dialogue'): part += ' Speech bubbles inside this panel in reading order: '+ ' / '.join((s['speaker']+' says ' if s['speaker'] else '')+'「'+s['text']+'」' for s in scene['dialogue'])+'.'
            parts.append(part)
        body = '\n'.join(parts)
    caption = 'comic, manga, multiple panels, white gutters, text, japanese text, speech bubble, very aesthetic, masterpiece.\n'+body
    if texts: caption += '\nText: '+'\n\n'.join(texts)
    else: caption = caption.replace('text, japanese text, speech bubble, ', '') + '\nNo text or speech bubbles.'
    if len(caption) > 6500: raise db.StudioError('ページの説明が長すぎます。各コマのpromptを短くしてください（送信文6500文字まで）')
    return dict(caption=caption, actors=actors, seed=value.get('seed'), format=value['format'],
                text_characters=len('\n\n'.join(texts)), prompt_characters=len(caption), plan=plan)

def candidates_target(project, number):
    return page_at(project, number).setdefault('whole', {})

def adopt(project_id, revision, number, candidate_id):
    def apply(project):
        target = candidates_target(project, number)
        candidate = next((c for c in target.get('candidates', []) if c['id']==candidate_id), None)
        if not candidate or not db.asset_path(project_id,candidate['image']).exists(): raise db.StudioError('ページ候補がありません')
        target.update(image=candidate['image'], adopted_request=candidate_id)
        target['generated_text_mode']=candidate.get('text_mode','embedded')
    return db.mutate(project_id, revision, apply)
