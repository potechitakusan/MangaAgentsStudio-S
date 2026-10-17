"""Shared character wardrobe/expression compiler and isolated catalog trials."""
from __future__ import annotations
import copy
import re
from . import project as db

EXPRESSIONS = [
    dict(id='custom', name='文章の指定に従う', prompt='', prompt_ja=''),
    dict(id='neutral', name='普通', prompt='neutral expression, closed mouth', prompt_ja='自然な表情、口を閉じる'),
    dict(id='smile', name='笑い', prompt='happy, smile', prompt_ja='嬉しそうな笑顔'),
    dict(id='surprised', name='驚き', prompt='surprised, wide-eyed, open mouth', prompt_ja='驚き、目を見開き、口を開ける'),
    dict(id='crying', name='泣き顔', prompt='crying, tears, sad', prompt_ja='泣き顔、涙、悲しげ'),
    dict(id='angry', name='怒り', prompt='angry, frown', prompt_ja='怒り、眉をひそめる'),
    dict(id='embarrassed', name='照れ', prompt='embarrassed, blush', prompt_ja='照れた表情、頬を赤らめる'),
]
EXPRESSION_MAP = {x['id']: x for x in EXPRESSIONS}
OUTFIT_FIELDS = ('id', 'name', 'prompt', 'prompt_ja')
EXPRESSION_TAGS = {tag.strip().lower() for e in EXPRESSIONS for tag in e['prompt'].split(',') if tag.strip()} | {
    'smiling','gentle smile','soft smile','happy expression','surprised expression','sad expression','wide eyes',
    'angry expression','crying face','teary eyes','expressionless','laughing','serious expression',
    '笑顔','微笑み','穏やかな微笑み','驚いた表情','悲しい表情','怒った表情','泣き顔','涙','無表情','普通の表情'}

def action_prompt(value, expression):
    if expression == 'custom': return value
    # Only standalone known emotion tags; never rewrite arbitrary actions or dialogue.
    return ', '.join(tag.strip() for tag in re.split(r'[,、]',value) if tag.strip().lower() not in EXPRESSION_TAGS)

def scene_prompt(panel):
    explicit = any(a.get('expression','custom')!='custom' for a in panel.get('characters',[]))
    return action_prompt(panel['prompt'],'neutral' if explicit else 'custom')

def validate_character(character, incomplete=False):
    outfits = character.get('outfits', [])
    if not isinstance(outfits, list) or len(outfits) > 10:
        raise db.StudioError('衣装は人物ごとに最大10パターンです')
    ids = set()
    for outfit in outfits:
        if not isinstance(outfit, dict): raise db.StudioError('衣装はオブジェクトです')
        oid = db.identifier(outfit.get('id'))
        if oid in ids: raise db.StudioError('同じ人物の衣装IDが重複しています')
        ids.add(oid)
        db.text_field(outfit.get('name'), '衣装名', 80)
        db.text_field(outfit.get('prompt'), '衣装prompt', 2000, incomplete)
        db.text_field(outfit.get('prompt_ja', ''), '衣装prompt_ja', 2000, True)
    default = character.get('default_outfit_id', '')
    if not isinstance(default, str) or (default and default not in ids):
        raise db.StudioError('既定の衣装IDが未登録です')

def validate_actor(character, actor):
    outfit = actor.get('outfit_id', '')
    expression = actor.get('expression', 'custom')
    if not isinstance(outfit, str) or (outfit and outfit not in {o['id'] for o in character.get('outfits', [])}):
        raise db.StudioError(f"{character['name']}: 未登録の衣装IDです。使用中の衣装を削除する前にコマの指定を変更してください")
    if not isinstance(expression, str) or expression not in EXPRESSION_MAP:
        raise db.StudioError('未登録の表情です')

def outfit_for(character, actor):
    validate_actor(character, actor)
    outfits = character.get('outfits', [])
    oid = actor.get('outfit_id') or character.get('default_outfit_id') or (outfits[0]['id'] if outfits else '')
    return next((o for o in outfits if o['id'] == oid), None)

def caption(character, actor, language='en'):
    outfit = outfit_for(character, actor)
    suffix = '_ja' if language == 'ja' else ''
    # Retain legacy request bytes when there are no wardrobe/expression choices.
    action = action_prompt(actor.get('prompt'+suffix, ''), actor.get('expression','custom'))
    expression = EXPRESSION_MAP[actor.get('expression', 'custom')]['prompt'+suffix]
    if not outfit and not expression: return character.get('appearance'+suffix, '') + ', ' + action
    return ', '.join(filter(None, [character.get('appearance'+suffix,''),action,outfit.get('prompt'+suffix,'') if outfit else '',expression]))

def preview(project, options):
    from . import novelai
    if not isinstance(options, dict) or set(options) - {'kind','id','outfit_id','expression','framing','seed'}:
        raise db.StudioError('試し描きの指定が不正です')
    kind = options.get('kind')
    if kind not in ('character','location'): raise db.StudioError('人物または背景を指定してください')
    entries = project.get('characters' if kind == 'character' else 'locations', [])
    entry = next((e for e in entries if e['id'] == options.get('id')), None)
    if not entry: raise db.StudioError('試し描きの対象がありません')
    seed = options.get('seed')
    if seed is not None and (type(seed) is not int or not 0 <= seed <= 4294967295): raise db.StudioError('Seedは0〜4294967295です')
    actors = []
    if kind == 'character':
        validate_character(entry, True)
        db.text_field(entry.get('appearance'), '固定外見', 2000)
        framing = options.get('framing', 'full')
        if framing not in ('full','portrait'): raise db.StudioError('全身または顔中心を選んでください')
        actor = dict(prompt='', prompt_ja='', outfit_id=options.get('outfit_id',''), expression=options.get('expression','neutral'))
        selected_outfit = outfit_for(entry, actor)
        if selected_outfit: db.text_field(selected_outfit.get('prompt'), '選択した衣装の英語prompt', 2000)
        description = caption(entry, actor)
        description_ja = caption(entry, actor, 'ja')
        pose = 'full body, standing, looking at viewer' if framing == 'full' else 'portrait, upper body, looking at viewer'
        base = 'solo, single character, ' + pose + ', simple background, plain light gray background'
        actors = [dict(char_caption=description, centers=[dict(x=.5,y=.5)])]
        width, height = (832,1216) if framing == 'full' else (1024,1024)
    else:
        db.text_field(entry.get('description'), '背景の固定特徴', 2000)
        base = 'scenery, background only, no humans, establishing shot, ' + entry['description']
        description_ja = entry.get('description_ja','')
        width, height = 1216,832
    base += ', ' + project.get('style','') + ', very aesthetic, masterpiece, no text'
    negative = ', '.join(filter(None, [novelai.NEGATIVE, project.get('style_negative',''), 'person, people, human' if kind=='location' else 'multiple characters']))
    payload = novelai.request_payload(base, negative, actors, seed, width, height)
    return dict(request=payload, description_ja=description_ja, style_ja=project.get('style_ja',''),
                snapshot=dict(options=copy.deepcopy(options), entry=copy.deepcopy(entry), style=project.get('style',''), style_negative=project.get('style_negative','')))

def history(project_id):
    records = []
    for path in (db.directory(project_id)/'images'/'previews').glob('*.record.json'):
        record = db.read_json(path)
        if record.get('status') == 'complete' and isinstance(record.get('preview'),dict): records.append(record)
    return sorted(records, key=lambda r:r['started_at'], reverse=True)[:100]
