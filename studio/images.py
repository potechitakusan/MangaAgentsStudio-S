"""Image backend fixed at work creation; per-work options and bounded job context."""
from __future__ import annotations
import contextlib
import contextvars
import copy
from . import project as db, novelai

DEFAULTS = dict(provider='novelai', anime_finish=False, denoise=0.3, reference_mode='prompt')
_settings = contextvars.ContextVar('image_settings', default=None)


def validate(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise db.StudioError('画像生成設定が不正です')
    result = {**DEFAULTS, **value}
    method(result['provider'])
    if type(result['anime_finish']) is not bool:
        raise db.StudioError('アニメ仕上げは真偽値です')
    if type(result['denoise']) not in (int, float) or not .25 <= result['denoise'] <= .35:
        raise db.StudioError('denoiseは0.25〜0.35です')
    if result['reference_mode'] not in ('prompt', 'references'):
        raise db.StudioError('Qwenの制御はpromptまたはreferencesです')
    return result


def method(value):
    if value not in ('novelai', 'qwen'):
        raise db.StudioError('output_methodがありません、または未対応です。新しい形式の作品・JSONが必要です')
    return value


def for_project(project):
    provider = method(project.get('output_method'))
    value = project.get('image_settings')
    if not isinstance(value, dict) or set(value) != set(DEFAULTS) - {'provider'}:
        raise db.StudioError('作品のimage_settingsが不正です')
    return validate(dict(provider=provider, **value))


def require_match(project, data):
    if not isinstance(data, dict): raise db.StudioError('output_methodを含むJSONが必要です')
    if method(data.get('output_method')) != method(project.get('output_method')):
        raise db.StudioError('output_methodが作品と違います。画像モデルは作品作成時だけ選択できます')


def defaults(): return dict(DEFAULTS)
def current(): return copy.deepcopy(_settings.get() or defaults())


@contextlib.contextmanager
def using(value=None):
    token = _settings.set(validate(value if value is not None else defaults()))
    try: yield
    finally: _settings.reset(token)


def adapter(project=None):
    if (for_project(project) if project is not None else current())['provider'] == 'qwen':
        from . import qwen
        return qwen
    return novelai


def label(): return 'QwenImage 2.1' if current()['provider'] == 'qwen' else 'NovelAI'


def generate(project_id, *args, **kwargs):
    project = db.load(project_id)
    if _settings.get() is not None and current() != for_project(project):
        raise db.StudioError('処理開始後に画像設定が変更されました。最新の設定で実行し直してください')
    with using(for_project(project)):
        return adapter(project).generate(project_id, *args, **kwargs)


def is_stale(project, *args, **kwargs):
    try: return adapter(project).is_stale(project, *args, **kwargs)
    except (KeyError, TypeError): return True


def build_request(project, *args, **kwargs): return adapter(project).build_request(project, *args, **kwargs)
def build_page_request(project, *args, **kwargs): return adapter(project).build_page_request(project, *args, **kwargs)


def preview(project, options):
    from . import profiles
    return adapter(project).preview(project, options) if for_project(project)['provider']=='qwen' else profiles.preview(project, options)


def prompt_rule(project=None):
    if (for_project(project) if project is not None else current())['provider'] != 'qwen': return ''
    return ('画像モデルはQwenImage 2.1。NovelAI固有の重み記法、品質タグ、source#/target#は使わない。'
            '英語promptは短い自然文で、人数・構図・誰が何をするか・視線の相手・光を具体的に書く。'
            '人物ごとの演技も主語のある短い自然文。固定外見・衣装・背景は従来どおり台帳参照。'
            '参照画像の番号と割当てはホストが付けるためimage1等を自分で指定しない。'
            '文字後付けの絵に台詞・文字を描かせない。')
