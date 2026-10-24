"""Editable NovelAI conditioning. No arbitrary API parameters or cost overrides."""
from . import project as db

ACTOR_FIELDS = {'id', 'prompt', 'prompt_ja', 'negative', 'negative_ja', 'x', 'y', 'outfit_id', 'expression'}
FLAGS = ('use_coords', 'use_order', 'negative_use_coords', 'negative_use_order')


def conditioning(panel):
    present = bool(panel.get('characters'))
    return dict(use_coords=present, use_order=True, negative_use_coords=present,
                negative_use_order=False) | panel.get('conditioning', {})


def validate(panel):
    if panel.get('prompt_mode', 'catalog') not in ('catalog', 'literal'):
        raise db.StudioError('prompt_modeはcatalogまたはliteralです')
    value = panel.get('conditioning', {})
    if not isinstance(value, dict) or set(value) - set(FLAGS) or any(type(v) is not bool for v in value.values()):
        raise db.StudioError('conditioningは配置・順序の4種類の真偽値です')
    for actor in panel.get('characters', []):
        if not isinstance(actor, dict) or set(actor) - ACTOR_FIELDS:
            raise db.StudioError('コマ人物に未対応の項目があります')
        for key in ('negative', 'negative_ja'):
            db.text_field(actor.get(key, ''), '人物' + key, 3000, True)


def special(panel):
    defaults = conditioning({'characters': panel.get('characters', [])})
    return (panel.get('prompt_mode', 'catalog') != 'catalog' or conditioning(panel) != defaults
            or any(a.get('negative') for a in panel.get('characters', [])))


def require_panel_mode(project):
    if project.get('render_mode') == 'whole' and any(special(p) for p in db.panels(project)):
        raise db.StudioError('人物別除外・個別の配置設定・原文プロンプトはNovelAIのコマ別生成で使ってください')


def schema():
    return {'type': 'object', 'properties': {k: {'type': 'boolean'} for k in FLAGS}, 'additionalProperties': False}
