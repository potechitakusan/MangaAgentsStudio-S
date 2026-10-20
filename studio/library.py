"""Library visibility and explicit copies of existing work folders. Never delete originals."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid
from . import project as db, images


def state_path():
    return db.WORK / '_library.json'


def excluded():
    value = db.read_json(state_path()) if state_path().exists() else {}
    return set(value.get('excluded', []))


def catalog():
    hidden = excluded()
    return [dict(item, excluded=item['id'] in hidden) for item in db.list_projects()]


def visible():
    hidden = excluded()
    return [item for item in db.list_projects() if item['id'] not in hidden]


def set_visibility(project_id, hide, confirmed=False):
    db.identifier(project_id)
    if type(hide) is not bool: raise db.StudioError('除外の指定が不正です')
    if hide and confirmed is not True: raise db.StudioError('再読込み方法を確認し、チェックを入れてください')
    with db.LOCK, db.exclusive(db.WORK / '.library.lock'):
        if not any(item['id'] == project_id for item in db.list_projects()): raise db.StudioError('作品がありません')
        ids = excluded()
        if hide: ids.add(project_id)
        else: ids.discard(project_id)
        db.dump(state_path(), dict(excluded=sorted(ids)))
    return dict(project_id=project_id, excluded=hide)


def choose_folder():
    """An explicitly requested OS picker, in its own process (Tk requires its main thread)."""
    code = ('import tkinter as tk; from tkinter import filedialog; '
            'root=tk.Tk(); root.withdraw(); root.attributes("-topmost", True); '
            'print(filedialog.askdirectory(parent=root, title="作品フォルダを選択（project.jsonがあるフォルダ）")); root.destroy()')
    try:
        result = subprocess.run([sys.executable, '-X', 'utf8', '-c', code], capture_output=True,
            encoding='utf-8', timeout=180, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise db.StudioError('フォルダを選択できませんでした。パスを直接入力してください') from exc
    if result.returncode: raise db.StudioError('フォルダ選択を開けません。パスを直接入力してください')
    return dict(path=result.stdout.strip())


def _files(source):
    # Only known work data, never hidden files, credentials, executables or runtime locks.
    roots = {'project.json', 'source.txt', 'AGENTS.md', 'SKILL.md', 'board.example.json', 'review.json'}
    trees = {'images': {'.png', '.json'}, 'history': {'.json'}, 'agent-runs': {'.json'},
             'output': {'.png', '.json'}, 'exports': {'.png', '.psd', '.json', '.md', '.zip'}}
    for base, dirs, names in os.walk(source, followlinks=False):
        folder = Path(base)
        relative = folder.relative_to(source)
        allowed = []
        for name in dirs:
            child = folder / name
            if name.startswith('.') or (folder == source and name not in trees): continue
            if child.is_symlink() or (hasattr(child, 'is_junction') and child.is_junction()):
                raise db.StudioError('リンクされたフォルダは読み込めません')
            allowed.append(name)
        dirs[:] = allowed
        for name in names:
            path = folder / name
            if name.startswith('.') or path.suffix in ('.tmp', '.lock', '.part'): continue
            if folder == source:
                if name not in roots and not re.fullmatch(r'review-page-[0-9-]+\.json', name): continue
            elif path.suffix.lower() not in trees[relative.parts[0]]: continue
            if path.is_symlink() or not path.resolve().is_relative_to(source):
                raise db.StudioError('リンクされたファイルは読み込めません')
            yield path, path.relative_to(source)


def _validate(p, folder):
    if not isinstance(p, dict): raise db.StudioError('project.jsonの形式が不正です')
    db.identifier(p.get('id'))
    images.for_project(p)
    if type(p.get('revision')) is not int or p['revision'] < 0: raise db.StudioError('作品のrevisionが不正です')
    db.text_field(p.get('title'), '作品名', 120)
    if not isinstance(p.get('pages'), list): raise db.StudioError('作品のpagesが不正です')
    if p['pages']: db.validate_board(p, allow_incomplete=p.get('workspace_version') == 2)
    else:
        check = dict(p, pages=[dict(template='splash', panels=[dict(id='validation', scene='', prompt='', characters=[], dialogue=[],
            location_id=p.get('locations', [{}])[0].get('id', '') if p.get('locations') else '')])])
        db.validate_board(check, allow_incomplete=True)
    if p.get('render_mode', 'panels') not in ('panels', 'whole'): raise db.StudioError('生成方式が不正です')
    if p.get('text_mode', 'overlay') not in ('overlay', 'embedded'): raise db.StudioError('文字指定が不正です')
    if 'source' in p: db.text_field(p['source'], '原作', 200000, True)
    if not (folder / 'source.txt').is_file() and not isinstance(p.get('source'), str):
        raise db.StudioError('原作（source.txtまたはsource）がありません')
    def check(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == 'image' and item:
                    if not isinstance(item, str) or not item.startswith('images/') or '\\' in item or ':' in item:
                        raise db.StudioError('作品画像のパスが不正です')
                    target = (folder / item).resolve()
                    if not target.is_relative_to(folder) or target.suffix.lower() != '.png':
                        raise db.StudioError('作品フォルダ外の画像は読み込めません')
                    if not target.is_file(): raise db.StudioError('作品の画像が不足しています: ' + item)
                else: check(item)
        elif isinstance(value, list):
            for item in value: check(item)
    check(p)


def import_folder(value):
    if not isinstance(value, str) or not value.strip(): raise db.StudioError('作品フォルダを指定してください')
    source = Path(value.strip().strip('"')).expanduser().resolve()
    if not source.is_dir() or not (source / 'project.json').is_file():
        raise db.StudioError('project.jsonがある作品フォルダを指定してください')
    # Reject live / interrupted writes; the user can choose an intact backup instead.
    if any(source.glob('.*.lock')): raise db.StudioError('処理中または要確認のロックがある作品は読み込めません')
    original = (source / 'project.json').read_bytes()
    try:
        p = json.loads(original.decode('utf-8-sig'))
        _validate(p, source)
    except (ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
        raise db.StudioError('project.jsonの形式が不正です') from exc
    target = db.directory(p['id']).resolve()
    if source == target:
        set_visibility(p['id'], False)
        return dict(id=p['id'], title=p['title'], copied=False)
    if target.exists(): raise db.StudioError('同じIDの作品があります。上書きせず、管理画面から既存作品を再表示してください')
    if target.is_relative_to(source): raise db.StudioError('読込み元の中へコピーできません')
    db.WORK.mkdir(exist_ok=True)
    stage = (db.WORK / ('_import-' + uuid.uuid4().hex)).resolve()
    if stage.parent != db.WORK.resolve(): raise db.StudioError('保存先が不正です')
    with db.LOCK, db.exclusive(db.WORK / '.library.lock'):
        try:
            stage.mkdir()
            for path, rel in _files(source):
                dest = stage / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dest)
            if original != (source / 'project.json').read_bytes() or any(source.glob('.*.lock')):
                raise db.StudioError('読込み中に作品が更新されました。完了後に読み込んでください')
            _validate(db.read_json(stage / 'project.json'), stage)
            if not (stage / 'source.txt').exists(): (stage / 'source.txt').write_text(p['source'], encoding='utf-8')
            for name, origin in [('AGENTS.md', db.ROOT / 'templates/AGENTS.md'),
                                 ('SKILL.md', db.ROOT / 'skills/manga-production/SKILL.md')]:
                if not (stage / name).exists(): shutil.copy2(origin, stage / name)
            if target.exists(): raise db.StudioError('同じIDの作品があります。上書きできません')
            # rename never replaces an existing nonempty project directory.
            stage.rename(target)
            ids = excluded(); ids.discard(p['id'])
            db.dump(state_path(), dict(excluded=sorted(ids)))
        finally:
            if stage.exists(): shutil.rmtree(stage)
    return dict(id=p['id'], title=p['title'], copied=True)
