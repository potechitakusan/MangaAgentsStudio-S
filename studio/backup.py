"""Project backups (ZIP) and soft delete (move to a trash folder). Fixed folders only; nothing is deleted for good."""
from __future__ import annotations
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from datetime import datetime, timezone
from . import project as db

AUTO_KEEP = 20
AUTO_INTERVAL = 30 * 60
IMAGE_SUFFIXES = ('.png', '.psd', '.zip', '.jpg', '.jpeg', '.webp')
NAME = re.compile(r'^(?P<id>[a-zA-Z0-9][a-zA-Z0-9_-]{0,63})-(?P<stamp>\d{8}-\d{6})-r(?P<rev>\d+)-(?P<kind>auto|manual)-(?P<size>light|full)\.zip$')
_auto_lock = threading.Lock()


def backups_root(): return db.WORK / '_backups'
def trash_root(): return db.WORK / '_trash'


def _skip(relative, full):
    parts = relative.parts
    name = relative.name
    if name.endswith(('.tmp', '.lock', '.part')): return True
    if parts[0] in ('exports', 'output'): return True  # Rebuilt from project.json by 再組版 / the export button.
    return not full and relative.suffix.lower() in IMAGE_SUFFIXES


def create(project_id, *, full=False, kind='manual'):
    """Write <work>/_backups/<id>/<id>-<stamp>-r<rev>-<kind>-<light|full>.zip and return its description."""
    if kind not in ('auto', 'manual'): raise db.StudioError('バックアップ種別が不正です')
    folder = db.directory(project_id)
    revision = db.load(project_id)['revision']  # Read first: the manifest states what project.json held when we started.
    target_dir = backups_root() / project_id
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    name = f"{project_id}-{stamp}-r{revision}-{kind}-{'full' if full else 'light'}.zip"
    final = target_dir / name
    if final.exists(): raise db.StudioError('同じ秒に同じバックアップがあります。少し待って再実行してください')
    part = target_dir / (name + '.part')
    count = 0
    try:
        with zipfile.ZipFile(part, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(folder.rglob('*')):
                if not path.is_file(): continue
                relative = path.relative_to(folder)
                if _skip(relative, full): continue
                try:
                    archive.write(path, f'{project_id}/{relative.as_posix()}',
                                  compress_type=zipfile.ZIP_STORED if path.suffix.lower() in IMAGE_SUFFIXES else zipfile.ZIP_DEFLATED)
                    count += 1
                except FileNotFoundError:
                    continue  # A generation finished or was cleaned up while we were copying.
            archive.writestr('backup-manifest.json', json.dumps(dict(
                project_id=project_id, revision=revision, created_at=db.now(), kind=kind, full=full, files=count,
                excluded='exports/ output/' + ('' if full else ' 画像(png/psd/zip等)'),
                restore='このZIPを展開し、中の作品フォルダを work/ に置くと復元できます（同名の作品がある場合は先に退避）'),
                ensure_ascii=False, indent=2))
        os.replace(part, final)
    finally:
        part.unlink(missing_ok=True)
    if kind == 'auto': _prune(project_id)
    return describe(final)


def describe(path):
    match = NAME.match(path.name)
    return dict(name=path.name, bytes=path.stat().st_size, revision=int(match['rev']) if match else None,
                kind=match['kind'] if match else '?', full=bool(match and match['size'] == 'full'),
                created=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec='seconds'))


def list_backups(project_id):
    folder = backups_root() / db.identifier(project_id)
    items = [describe(p) for p in folder.glob('*.zip')] if folder.exists() else []
    return sorted(items, key=lambda x: x['name'], reverse=True)


def _prune(project_id):
    autos = [b for b in list_backups(project_id) if b['kind'] == 'auto']
    for old in autos[AUTO_KEEP:]:
        (backups_root() / project_id / old['name']).unlink(missing_ok=True)


def auto_backup_all():
    """Light backup of every project whose revision changed since its newest auto backup. Returns the ids backed up."""
    done = []
    with _auto_lock:
        for item in db.list_projects():
            latest = next((b for b in list_backups(item['id']) if b['kind'] == 'auto'), None)
            if latest and latest['revision'] == item['revision']: continue
            try:
                create(item['id'], kind='auto')
                done.append(item['id'])
            except (db.StudioError, OSError):
                continue  # One broken project must not stop the others; the next round retries.
    return done


def start_auto(interval=AUTO_INTERVAL):
    def loop():
        while True:
            try: auto_backup_all()
            except Exception:
                pass
            time.sleep(interval)
    thread = threading.Thread(target=loop, name='auto-backup', daemon=True)
    thread.start()
    return thread


def soft_delete(project_id, confirm, *, busy=False):
    """Move the project folder to work/_trash/<id>-<stamp>/. Refuses while anything may still be writing to it."""
    folder = db.directory(project_id)
    if confirm != project_id: raise db.StudioError('確認のため、削除する作品の保存用IDをそのまま入力してください')
    if not (folder / 'project.json').exists(): raise db.StudioError('作品がありません')
    if busy: raise db.StudioError('この作品は処理中です。完了してから削除してください')
    from . import novelai
    state = novelai.ledger()
    if any(r.get('project_id') == project_id and r.get('status') in ('sending', 'unknown') for r in state['requests'].values()):
        raise db.StudioError('結果が未確認の画像生成があります。費用台帳を確認するまで削除できません')
    project = db.load(project_id)
    with db.LOCK, db.exclusive(folder / '.project.lock'):
        trash_root().mkdir(parents=True, exist_ok=True)
        target = trash_root() / f"{project_id}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        if target.exists(): raise db.StudioError('同じ秒のゴミ箱フォルダがあります。少し待って再実行してください')
        (folder / '.project.lock').unlink()  # Held by us; moving a folder with an open file is refused on Windows.
        try:
            os.replace(folder, target)
        except OSError as exc:
            (folder / '.project.lock').write_text(db.now(), encoding='utf-8')
            raise db.StudioError('フォルダを移動できません。他のアプリがこの作品のファイルを開いていないか確認してください') from exc
    db.dump(target / '_deleted.json', dict(project_id=project_id, title=project.get('title'), revision=project['revision'], deleted_at=db.now(),
                                            restore='このフォルダ名から末尾の -日時 を除いて work/ に戻すと復元できます'))
    return dict(trash=target.name, title=project.get('title'))


def folder_for(kind, project_id=None):
    if kind == 'backups': path = backups_root() / (db.identifier(project_id) if project_id else '')
    elif kind == 'trash': path = trash_root()
    elif kind == 'project': path = db.directory(project_id)
    else: raise db.StudioError('開けるフォルダはバックアップ・ゴミ箱・作品フォルダです')
    path.mkdir(parents=True, exist_ok=True)
    return path


def open_folder(kind, project_id=None):
    """Open one of the three fixed folders in the OS file manager. No caller-supplied paths."""
    path = folder_for(kind, project_id)
    if sys.platform == 'win32': os.startfile(path)  # noqa: S606 - fixed, validated local folder
    elif sys.platform == 'darwin': subprocess.Popen(['open', str(path)])
    else: subprocess.Popen(['xdg-open', str(path)])
    return str(path)


def storage(project_id):
    folder = db.directory(project_id)
    if not (folder / 'project.json').exists(): raise db.StudioError('作品がありません')
    size = sum(p.stat().st_size for p in folder.rglob('*') if p.is_file())
    return dict(bytes=size, backups=list_backups(project_id)[:10], backup_folder=str(backups_root() / project_id), trash_folder=str(trash_root()))
