"""Read a bounded subset of an A work. Never execute A code or read its environment."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from . import project as db

MAX_FILE = 2_000_000
MAX_TOTAL = 20_000_000
MAX_JSON = 60_000_000  # Snapshot JSON escapes source text and also stores the normalized inputs.
FIXED = ('project.json', 'input/novelai/characters.json', 'docs/story/BRIEF.md',
         'docs/story/TEXT-STORYBOARD.md', 'docs/story/SCENE-TEMPLATE.md', 'docs/production/DIALOGUE.md')
TREES = ('input/pages/', 'input/typeset/', 'input/novelai/requests/', 'output/layout/')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def pairs(items):
    result = {}
    for key, value in items:
        if key in result: raise db.StudioError('JSONの項目名が重複しています')
        result[key] = value
    return result


def parse(text):
    try:
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        def check(item):
            if isinstance(item, dict):
                if any(k.lower() in {'api_key','authorization','password','access_token','refresh_token','client_secret'} for k in item):
                    raise db.StudioError('認証項目を含むJSONは取り込めません')
                for v in item.values(): check(v)
            elif isinstance(item, list):
                for v in item: check(v)
        check(value)
        return value
    except (ValueError, RecursionError):
        raise db.StudioError('JSONの構文・数値が不正です') from None


def safe_text(text):
    # Do not echo a suspect value in exceptions or reports.
    if re.search(r'(?im)^\s*(?:NOVELAI_API_KEY|LOCAL_AI_API_KEY|API_KEY)\s*=\s*\S|\bpst-[A-Za-z0-9_-]{20,}', text):
        raise db.StudioError('認証情報を含む可能性のあるファイルは取り込めません')
    return text


def read_json(path):
    path = Path(path)
    if path.stat().st_size > MAX_JSON: raise db.StudioError('取込JSONが大きすぎます')
    return parse(safe_text(path.read_text(encoding='utf-8-sig')))


class Reader:
    def __init__(self, root, documents=None):
        self.root = Path(root).absolute()
        if not self.root.is_dir(): raise db.StudioError('Aの作品フォルダがありません')
        self.documents = sorted(set(documents or []))
        for name in self.documents:
            if not isinstance(name, str) or not name.startswith(('input/', 'docs/story/', 'docs/production/')) or Path(name).suffix.lower() not in ('.md', '.txt'):
                raise db.StudioError('追加文書はinputまたはdocs/story・docs/production内のMarkdown／テキストです')
        self.files = {}
        self.total = 0

    def path(self, name):
        if not isinstance(name, str) or '\\' in name or ':' in name:
            raise db.StudioError('Aの参照は作品内の相対パスで指定してください')
        rel = PurePosixPath(name)
        if rel.is_absolute() or any(p in ('..', '.') or p.startswith('.') for p in rel.parts):
            raise db.StudioError('Aの参照パスが不正です')
        if name not in FIXED and name not in self.documents and not (name.endswith('.json') and name.startswith(TREES)):
            raise db.StudioError('Aの取込対象外のファイル参照です')
        path = self.root.joinpath(*rel.parts)
        for candidate in (self.root, *path.parents, path):
            if candidate.is_symlink() or (hasattr(candidate, 'is_junction') and candidate.is_junction()):
                raise db.StudioError('リンクされた取込元は使えません')
        if not path.resolve().is_relative_to(self.root.resolve()): raise db.StudioError('Aの作品外の参照です')
        return path

    def read(self, name):
        path = self.path(name)
        if name in self.files: return self.files[name]['text']
        if not path.is_file(): raise db.StudioError('Aの参照ファイルがありません: ' + name)
        if path.stat().st_size > MAX_FILE or len(self.files) >= 500: raise db.StudioError('Aの取込サイズ上限を超えました')
        raw = path.read_bytes(); self.total += len(raw)
        if self.total > MAX_TOTAL: raise db.StudioError('Aの取込総量が大きすぎます')
        try: text = safe_text(raw.decode('utf-8-sig'))
        except UnicodeDecodeError: raise db.StudioError('Aの文書はUTF-8で保存してください') from None
        if name.endswith('.json'): parse(text)
        self.files[name] = dict(sha256=hashlib.sha256(raw).hexdigest(), text=text)
        return text

    def json(self, name): return parse(self.read(name))

    def list(self, folder, pattern):
        base = self.path(folder + '/probe.json').parent
        if not base.exists(): return []
        paths = sorted(base.glob(pattern))
        if len(paths) > 500: raise db.StudioError('Aのファイル数が多すぎます')
        return [p.relative_to(self.root).as_posix() for p in paths if p.is_file()]


def snapshot(root, source_id=None, documents=None):
    reader = Reader(root, documents)
    for name in FIXED:
        if reader.path(name).is_file(): reader.read(name)
    for name in reader.documents: reader.read(name)
    pages = reader.list('input/pages', 'page-*.json')
    for name in pages:
        page = reader.json(name)
        if not isinstance(page, dict) or page.get('schema_version') != 1: raise db.StudioError('Aのページ構成はschema_version:1が必要です')
        for key in ('layout', 'typeset'):
            if page.get(key): reader.json(page[key])
    for name in reader.list('input/novelai/requests', '*.json'): reader.json(name)
    meta = reader.json('project.json') if 'project.json' in reader.files else {}
    if not isinstance(meta, dict): raise db.StudioError('Aの作品メタデータが不正です')
    if not meta and not source_id: raise db.StudioError('字コンテのみの場合はsource-idを指定してください')
    source_id = source_id or 'a-' + digest({k: meta.get(k, '') for k in ('name', 'createdAt')})[:20]
    db.identifier(source_id)
    hashes = {k: v['sha256'] for k, v in reader.files.items()}
    if any(hashlib.sha256(reader.path(name).read_bytes()).hexdigest() != expected for name, expected in hashes.items()):
        raise db.StudioError('読込み中にAの原資料が変わりました')
    return dict(id=source_id, digest=digest(hashes), files=reader.files, documents=reader.documents)


def documents(source):
    return {name: value['text'] for name, value in source['files'].items() if name.endswith('.md')}


def json_at(source, name):
    if name not in source['files']: raise db.StudioError('棚卸しにない参照です: ' + name)
    return parse(source['files'][name]['text'])


def request_parts(raw):
    if not isinstance(raw, dict) or set(raw) - {'action', 'input', 'model', 'parameters'}:
        raise db.StudioError('Aの要求JSONに未対応のトップレベル項目があります')
    if raw.get('action', 'generate') != 'generate': raise db.StudioError('通常の画像生成要求だけを取り込めます')
    params = raw.get('parameters', {})
    if not isinstance(params, dict): raise db.StudioError('Aのparametersが不正です')
    base, neg = raw.get('input', ''), params.get('negative_prompt', '')
    positive = params.get('v4_prompt', {'caption': {'base_caption': base, 'char_captions': []}})
    negative = params.get('v4_negative_prompt', {'caption': {'base_caption': neg, 'char_captions': []}})
    try:
        chars = positive['caption'].get('char_captions', [])
        exclusions = negative['caption'].get('char_captions', [])
        if positive['caption']['base_caption'] != base or negative['caption']['base_caption'] != neg:
            raise db.StudioError('Aのinput/negativeとbase_captionが一致していません')
        if not isinstance(chars, list) or not isinstance(exclusions, list) or (exclusions and len(exclusions) != len(chars)):
            raise db.StudioError('Aの正負の人物別入力の数が一致していません')
        actors = []
        for i, char in enumerate(chars):
            centers = char.get('centers', [{'x': .5, 'y': .5}])
            excluded = exclusions[i] if exclusions else {}
            if len(centers) != 1 or set(centers[0]) != {'x','y'} or excluded.get('centers', centers) != centers:
                raise db.StudioError('人物ごとの単一座標と、正負で同じ座標が必要です')
            actors.append(dict(id='', prompt=char['char_caption'], prompt_ja='', negative=excluded.get('char_caption', ''), negative_ja='',
                               x=centers[0]['x'], y=centers[0]['y'], expression='custom', outfit_id=''))
        flags = dict(use_coords=positive.get('use_coords', False), use_order=positive.get('use_order', True),
                     negative_use_coords=negative.get('use_coords', False), negative_use_order=negative.get('use_order', False))
    except (KeyError, TypeError, IndexError, AttributeError):
        raise db.StudioError('Aの人物別プロンプト構造が不正です') from None
    return dict(prompt=base, negative=neg, characters=actors, seed=params.get('seed'), conditioning=flags)
