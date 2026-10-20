"""Reusable Qwen references selected from this work's completed catalog trials."""
from __future__ import annotations
import copy
import hashlib
import io
import json
from PIL import Image
from . import project as db, profiles, images


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def profile(project, kind, entry_id, outfit_id=''):
    """Only visual inputs count; voice, translations and expression do not."""
    from .qwen import scenery_style
    entries = project.get('characters' if kind == 'character' else 'locations', [])
    entry = next((e for e in entries if e['id'] == entry_id), None)
    if not entry or kind not in ('character', 'location'): raise db.StudioError('参照対象がありません')
    style = project['style'] if kind == 'character' else scenery_style(project['style'])
    visual = dict(style=style, negative=project.get('style_negative',''), finish=project['image_settings']['anime_finish'])
    if visual['finish']: visual['denoise'] = project['image_settings']['denoise']
    if kind == 'character':
        outfit = profiles.outfit_for(entry, dict(outfit_id=outfit_id))
        outfit_id = outfit['id'] if outfit else ''
        visual.update(appearance=entry['appearance'], outfit=outfit.get('prompt','') if outfit else '')
    else:
        outfit_id = ''
        visual['description'] = entry['description']
    return dict(key=f'{kind}/{entry_id}/{outfit_id}', kind=kind, id=entry_id, outfit_id=outfit_id,
                name=entry['name'], fingerprint=digest(visual))


def read_image(project, ref):
    relative = ref.get('image','')
    if not relative.startswith(('images/previews/', 'images/references/')) or not relative.endswith('.png'):
        raise db.StudioError('参照画像はこの作品の試し描きから登録してください')
    path = db.asset_path(project['id'], relative)
    if not path.is_file(): raise db.StudioError('参照画像ファイルがありません')
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != ref.get('sha256'):
        raise db.StudioError('参照画像ファイルが変更されています。登録し直してください')
    return data


def import_png(project_id, revision, kind, entry_id, png, *, outfit_id='', source_name=''):
    """Import explicitly supplied artwork, preserving bytes and honest provenance.

    This local helper accepts bytes, never a server-side arbitrary file path.
    Imported art is not recorded as an AI trial or a generated candidate.
    """
    if not isinstance(png, bytes) or len(png) > 32 * 1024 * 1024:
        raise db.StudioError('参照PNGは32MB以下です')
    try:
        with Image.open(io.BytesIO(png)) as im:
            if im.format != 'PNG' or im.width * im.height > 25_000_000:
                raise ValueError()
            im.verify()
    except Exception:
        raise db.StudioError('参照画像は有効なPNGで指定してください') from None
    db.text_field(source_name, '参照元名', 1000, True)
    sha = hashlib.sha256(png).hexdigest()
    def apply(p):
        if p['output_method'] != 'qwen': raise db.StudioError('参照画像はQwen作品専用です')
        target = profile(p, kind, entry_id, outfit_id)
        relative = f'images/references/{sha}.png'
        path = db.asset_path(project_id, relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_bytes() != png:
            raise db.StudioError('同名の参照ファイルが変更されています')
        path.write_bytes(png)
        p.setdefault('references', {})[target['key']] = dict(
            target, image=relative, sha256=sha, candidate_id='import-'+sha[:16],
            origin='imported', source_name=source_name)
    return db.mutate(project_id, revision, apply)


def selected(project, target):
    ref = project.get('references',{}).get(target['key'])
    if not ref: raise db.StudioError(f"{target['name']}（{target['outfit_id'] or '背景／既定'}）の参照画像が未登録です。作品専用タブで試し描きを登録してください")
    if ref.get('fingerprint') != target['fingerprint']:
        raise db.StudioError(f"{target['name']}の参照画像は外見・衣装・画風の変更前のものです。試し描きを作り直して登録してください")
    read_image(project, ref)
    return copy.deepcopy(ref)


def collect(project, panels):
    if images.for_project(project)['reference_mode'] != 'references': return []
    targets = {}
    for panel in panels:
        for actor in panel.get('characters',[]):
            p = profile(project, 'character', actor['id'], actor.get('outfit_id',''))
            targets[p['key']] = p
        if panel.get('location_id'):
            p = profile(project, 'location', panel['location_id'])
            targets[p['key']] = p
    if len(targets) > 16: raise db.StudioError('1回の参照画像は最大16枚です。コマ別生成またはプロンプトのみを選んでください')
    result = []
    for i, target in enumerate(targets.values(), 1):
        ref = selected(project, target)
        ref['slot'] = f'image{i}'
        result.append(ref)
    return result


def instructions(refs):
    lines = ['Use the supplied reference images to draw a NEW composition. Preserve identity, hair, outfit and location design; follow the requested camera, pose and expression. Do not copy reference poses or add reference-sheet panels.']
    for ref in refs:
        subject = f"character {ref['id']} wearing outfit {ref['outfit_id'] or 'default'}" if ref['kind']=='character' else f"location {ref['id']} (background design only)"
        lines.append(f"{ref['slot']} is the reference for {subject}.")
    return '\n'.join(lines) if refs else ''


def adopt(project_id, revision, candidate_id):
    db.identifier(candidate_id)
    def apply(p):
        if p['output_method'] != 'qwen': raise db.StudioError('参照画像はQwen作品専用です')
        path = db.directory(project_id)/'images'/'previews'/(candidate_id+'.record.json')
        if not path.is_file(): raise db.StudioError('試し描き履歴がありません')
        record = db.read_json(path)
        if record.get('status') != 'complete' or record.get('provider') != 'qwen' or record.get('project_id') != project_id:
            raise db.StudioError('この作品の完了したQwen試し描きだけ登録できます')
        options = record['preview']
        if options['kind']=='character' and options.get('framing','full')!='full':
            raise db.StudioError('人物参照には衣装が分かる全身の試し描きを使ってください')
        ref = record.get('reference_profile')
        if not ref: raise db.StudioError('参照登録用の情報がない古い試し描きです')
        current = profile(p, ref['kind'], ref['id'], ref['outfit_id'])
        if current['fingerprint'] != ref['fingerprint']:
            raise db.StudioError('試し描き後に外見・衣装・画風が変更されています。作り直してください')
        relative = f'images/previews/{candidate_id}.png'
        data = db.asset_path(project_id, relative).read_bytes()
        saved = dict(current, image=relative, sha256=hashlib.sha256(data).hexdigest(), candidate_id=candidate_id)
        p.setdefault('references',{})[current['key']] = saved
    return db.mutate(project_id, revision, apply)


def catalog(project):
    if project['output_method'] != 'qwen': return []
    targets = []
    for c in project['characters']:
        for outfit in c.get('outfits') or [dict(id='',name='既定')]:
            targets.append(dict(profile(project,'character',c['id'],outfit['id']), outfit_name=outfit['name']))
    targets.extend(profile(project,'location',loc['id']) for loc in project.get('locations',[]))
    for target in targets:
        try:
            ref = selected(project, target)
            target.update(status='ready', image=ref['image'], candidate_id=ref['candidate_id'])
        except db.StudioError as exc:
            target.update(status='missing' if target['key'] not in project.get('references',{}) else 'stale', message=str(exc))
    return targets
