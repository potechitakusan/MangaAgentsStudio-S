"""A-to-S transfer preparation and revision-checked three-way import (no AI/network).

python -m studio.a_import inspect --source A_WORK --out transfer.json
python -m studio.a_import preview --source A_WORK --transfer transfer.json --project ID --out preview.json
python -m studio.a_import apply --source A_WORK --transfer transfer.json --project ID --revision N
"""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path
import re
import sys
from . import project as db, workspace, novelai, a_source, import_merge, prompt_options

TRANSFER_SCHEMA = 'manga-a-transfer-v1'
STATE_SCHEMA = 'manga-a-import-state-v1'
FIELDS = ('output_method', 'image_settings', 'title', 'source', 'render_mode', 'text_mode',
          'style', 'style_ja', 'style_negative', 'style_negative_ja', 'style_preset',
          'characters', 'locations', 'props', 'pages')
PARSE_PAGE = re.compile(r'input/pages/page-(\d+)\.json$')
PARSE_REQUEST = re.compile(r'input/novelai/requests/p(\d+)-(\d+)([a-z]?)\.json$')


def prototype():
    return dict(id='import-validation', revision=0, output_method='novelai',
                image_settings=dict(reference_mode='prompt', anime_finish=False, denoise=.3),
                title='Aからの取込', source='', style='', characters=[], locations=[], props=[], pages=[],
                render_mode='panels', text_mode='overlay', references={})


def projected(project):
    value = workspace.editable(project)
    result = {k: copy.deepcopy(value[k]) for k in FIELDS if k in value}
    result['pages'] = [{k: page[k] for k in ('id', 'template', 'panels')} for page in result['pages']]
    return result


def normalize(value):
    if not isinstance(value, dict) or set(value) - set(FIELDS): raise db.StudioError('移行入力に未対応の共通項目があります')
    if value.get('output_method') != 'novelai' or value.get('render_mode') != 'panels' or value.get('text_mode') != 'overlay':
        raise db.StudioError('Aの取込先はNovelAI・コマ別・文字後付けです')
    for page in value.get('pages', []):
        if not isinstance(page, dict) or set(page) != {'id', 'template', 'panels'}: raise db.StudioError('移行ページはid/template/panelsです')
        for panel in page['panels']:
            if not isinstance(panel, dict) or not panel.get('id'): raise db.StudioError('移行コマには固定のidが必要です')
    for kind in ('characters', 'locations', 'props'):
        allowed = {'id','name','appearance','appearance_ja','voice','voice_ja','outfits','default_outfit_id'} if kind == 'characters' else {'id','name','description','description_ja'}
        for entry in value.get(kind, []):
            if not isinstance(entry, dict) or set(entry) - allowed: raise db.StudioError('移行台帳に未対応の項目があります')
            for outfit in entry.get('outfits', []):
                if not isinstance(outfit, dict) or set(outfit) - {'id','name','prompt','prompt_ja'}: raise db.StudioError('衣装の項目が不正です')
    p = prototype()
    workspace.apply_inputs(p, copy.deepcopy(value))
    if not p['pages']: raise db.StudioError('取り込むページがありません。字コンテからページを整理してください')
    # Draft catalogs may be empty for literal prompts, but the actual per-panel text must be ready.
    for panel in db.panels(p):
        # The transfer is a full input snapshot. Make defaults explicit so save's sparse-edit
        # preservation cannot revive a removed option from the previous imported panel.
        panel.setdefault('prompt_mode', 'catalog')
        panel['conditioning'] = prompt_options.conditioning(panel)
        for actor in panel.get('characters', []):
            actor.setdefault('negative', '')
            actor.setdefault('negative_ja', '')
        db.text_field(panel.get('scene'), '場面')
        db.text_field(panel.get('prompt'), '画像プロンプト', 5000)
        for actor in panel.get('characters', []):
            if panel.get('prompt_mode', 'catalog') == 'literal': db.text_field(actor.get('prompt'), '人物原文', 2000)
        texts = [panel.get('prompt',''),panel.get('negative','')] + [a.get(k,'') for a in panel.get('characters',[]) for k in ('prompt','negative')]
        if any('${' in text for text in texts): raise db.StudioError('未解決のプロンプト変数があります。.envは読み込まず、独立した本文を用意してください')
        if panel.get('needs_edit'): raise db.StudioError('未編集のコマが残っています')
    return projected(p)


def write_output(root, path, value):
    target=Path(path).absolute()
    if target.resolve().is_relative_to(Path(root).resolve()) or target.suffix.lower() != '.json' or target.name == 'project.json':
        raise db.StudioError('出力はA作品の外の専用JSONファイルへ保存してください')
    if len(json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8')) > a_source.MAX_JSON:
        raise db.StudioError('移行JSONが大きすぎます。取込元の資料を必要な範囲へ分けてください')
    db.dump(target, value)


def inspect_source(root, source_id=None, documents=None):
    source = a_source.snapshot(root, source_id, documents)
    meta = a_source.json_at(source, 'project.json') if 'project.json' in source['files'] else {}
    p = prototype(); p['title'] = meta.get('name') or 'Aからの取込'
    p['source'] = '\n\n'.join(a_source.documents(source).values())
    if len(p['source']) > 200000: p['source'] = ''
    inputs = projected(p)
    bindings, issues, groups = [], [], {}
    def issue(code, message): issues.append(dict(code=code, message=message))
    manifests = [(int(m[1]), path) for path in source['files'] if (m := PARSE_PAGE.fullmatch(path))]
    if manifests:
        for number, path in sorted(manifests):
            if number in groups: raise db.StudioError('Aのページ番号が重複しています')
            data = a_source.json_at(source, path)
            layout = a_source.json_at(source, data['layout']) if data.get('layout') else {}
            template = layout.get('template', {}).get('id', '')
            groups[number] = dict(template=template, entries=sorted(data.get('panels', []), key=lambda e: e['panel']), path=path,
                                  typeset=data.get('typeset'), layout=data.get('layout'))
            if data.get('overlays'): issue(f'page:{number}:overlays', '効果音等の追加画像は移行対象外。原資料を保持します')
    else:
        for path in source['files']:
            if not (m := PARSE_REQUEST.fullmatch(path)): continue
            page, panel = int(m[1]), int(m[2])
            group = groups.setdefault(page, dict(template='', entries=[], path=None, typeset=None, layout=None))
            existing = next((e for e in group['entries'] if e['panel'] == panel), None)
            if existing:
                existing.setdefault('alternatives', [existing['request']]).append(Path(path).stem)
            else:
                group['entries'].append(dict(panel=panel, request=Path(path).stem))
        for number in groups: issue(f'page:{number}:layout', 'ページ構成がありません。字コンテから枠と読み順を確定してください')
    for number, group in sorted(groups.items()):
        page = dict(id=f'a-page-{number:03}', template=group['template'], panels=[])
        entries = group['entries']
        seen = set()
        typeset = a_source.json_at(source, group['typeset']) if group['typeset'] else {}
        layout = a_source.json_at(source, group['layout']) if group['layout'] else {}
        layout_ids = {str(c['id']): c['order'] for c in layout.get('template', {}).get('panels', [])}
        for entry in sorted(entries, key=lambda e: e['panel']):
            order = entry['panel']
            if type(order) is not int or order < 1 or order in seen:
                raise db.StudioError('コマ番号が重複しています。別案の採用とページ構成をAの成果物で確定してください')
            seen.add(order)
            pid = f'a-p{number:03}-c{order:03}'
            if entry.get('alternatives'):
                issue(pid + ':alternatives', '別案があります。bindings.requestとコマ入力を選んだ要求に合わせてください: ' + ', '.join(entry['alternatives']))
            path = 'input/novelai/requests/' + entry['request'] + '.json' if entry.get('request') else None
            parts = a_source.request_parts(a_source.json_at(source, path)) if path else dict(prompt='',negative='',characters=[],seed=None)
            panel = dict(id=pid, scene='', prompt_ja='', negative_ja='', dialogue=[], speakers=[], prop_ids=[], location_id='',
                         prompt_mode='literal', crop=[.5,.5], image_transform=dict(x=0,y=0,scale=1), needs_edit=True, **parts)
            balloons = [b for b in typeset.get('balloons', []) if layout_ids.get(str(b.get('panel')), b.get('panel')) in (order, str(order))]
            balloons.sort(key=lambda b: b.get('order', 0))
            if len({b.get('order') for b in balloons}) != len(balloons): raise db.StudioError('文字の読み順が重複しています')
            panel['dialogue'] = [b['text'] for b in balloons]
            panel['speakers'] = [''] * len(balloons)
            if balloons: issue(pid + ':typeset', '台詞本文を保持。話者を照合し、文字方向・位置・吹き出しの違いを確認してください')
            if entry.get('source'): issue(pid + ':image', 'コマ画像の直接指定は移行対象外。必要な要求・台詞を整理してください')
            if entry.get('focus', [.5,.5]) != [.5,.5] or entry.get('zoom', 1) != 1 or entry.get('rotation', 0):
                issue(pid + ':placement', 'Aのfocus/zoom/rotationは画像位置へ自動変換しません')
            if panel['characters']: issue(pid + ':identity', '各人物入力と台帳IDを照合してください。原文方式では台帳の外見は合成されません')
            if not path: issue(pid + ':prompt', '対応する要求がありません。字コンテからプロンプトを用意してください')
            page['panels'].append(panel)
            bindings.append(dict(page_id=page['id'], panel_id=pid, request=path, source_page=group['path'], source_order=order))
        inputs['pages'].append(page)
    if not groups: issue('storyboard-only', '機械可読のページ・要求がありません。原資料からinputsとbindingsを作ってください')
    return dict(schema=TRANSFER_SCHEMA, source=source, inputs=inputs, bindings=bindings, issues=issues, acknowledgements={})


def validate_transfer(root, transfer):
    if not isinstance(transfer, dict) or set(transfer) != {'schema','source','inputs','bindings','issues','acknowledgements'} or transfer['schema'] != TRANSFER_SCHEMA:
        raise db.StudioError('manga-a-transfer-v1の移行ファイルが必要です')
    actual = a_source.snapshot(root, transfer['source']['id'], transfer['source'].get('documents'))
    if actual != transfer['source']: raise db.StudioError('Aの原資料または棚卸し情報が変わりました。inspectをやり直してください')
    incoming = normalize(transfer['inputs'])
    expected = {(page['id'], panel['id']) for page in incoming['pages'] for panel in page['panels']}
    bindings = transfer['bindings']
    if not isinstance(bindings, list) or any(not isinstance(b, dict) or set(b) != {'page_id','panel_id','request','source_page','source_order'} for b in bindings):
        raise db.StudioError('出典対応表の形式が不正です')
    if len(bindings) != len(expected) or {(b['page_id'], b['panel_id']) for b in bindings} != expected:
        raise db.StudioError('すべてのコマに一意の出典対応が必要です')
    for b in bindings:
        if type(b['source_order']) is not int or b['source_order'] < 1: raise db.StudioError('出典コマ番号が不正です')
        for key in ('request', 'source_page'):
            if b[key] is not None and b[key] not in actual['files']: raise db.StudioError('棚卸しにない出典参照です')
        if b['request'] and not b['request'].startswith('input/novelai/requests/'): raise db.StudioError('出典要求の場所が不正です')
        if b['source_page'] and not PARSE_PAGE.fullmatch(b['source_page']): raise db.StudioError('出典ページの場所が不正です')
    fresh = inspect_source(root, actual['id'], actual['documents'])
    # The source-derived warnings cannot be removed by editing the transfer's display list.
    warnings = copy.deepcopy(fresh['issues'])
    p = prototype(); workspace.apply_inputs(p, incoming)
    for b in bindings:
        if not b['request']: continue
        raw = a_source.json_at(actual, b['request']); a_source.request_parts(raw)
        target = novelai.build_request(p, db.panel_by_id(p, b['panel_id']))
        for key in sorted(set(raw.get('parameters', {})) | set(target['parameters'])):
            before, after = raw.get('parameters', {}).get(key), target['parameters'].get(key)
            if key == 'seed' and before is None: continue
            if before != after:
                warnings.append(dict(code=f"{b['panel_id']}:request:{key}", message='送信内容がAと変わります: ' + key, before=before, after=after))
        if raw.get('model') != target['model']: warnings.append(dict(code=b['panel_id']+':request:model', message='モデルをSのV5 Curatedへ変更します', before=raw.get('model'), after=target['model']))
        if raw.get('input') != target['input']: warnings.append(dict(code=b['panel_id']+':request:input', message='Baseの文章がAと変わります', before=raw.get('input'), after=target['input']))
    acknowledgements = transfer['acknowledgements']
    if not isinstance(acknowledgements, dict) or any(not isinstance(k,str) or not isinstance(v,str) or not v.strip() or len(v)>2000 for k,v in acknowledgements.items()):
        raise db.StudioError('差分の確認理由はコードごとの空でない文字列です')
    unresolved = [w for w in warnings if w['code'] not in acknowledgements]
    return incoming, warnings, unresolved


def state_for(project, folder=None):
    key = project.get('import_state')
    if key is None: return None
    if not isinstance(key, str) or not re.fullmatch('[0-9a-f]{64}', key): raise db.StudioError('取込状態の参照が不正です')
    folder = Path(folder) if folder is not None else db.directory(project['id'])
    path = folder / 'imports' / (key + '.json')
    if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()) or not path.is_file():
        raise db.StudioError('取込状態ファイルが不足または不正です')
    value = a_source.read_json(path)
    if a_source.digest(value) != key or value.get('schema') != STATE_SCHEMA: raise db.StudioError('取込状態が変更されています')
    return value


def build_plan(project, incoming, transfer, resolutions=None):
    if project['output_method'] != 'novelai' or project.get('render_mode', 'panels') != 'panels': raise db.StudioError('取込先はNovelAIのコマ別作品です')
    state = state_for(project)
    current = projected(project)
    if state:
        if state['source']['id'] != transfer['source']['id']: raise db.StudioError('別のA作品は新規のS作品へ取り込んでください')
        base = state['base']
    else:
        if project['pages']: raise db.StudioError('初回の取込先は空の新規作品にしてください')
        base = current
    merged, conflicts = import_merge.merge(base, current, incoming, resolutions)
    # Validation is reported after conflicts, since temporary merges can have dangling references.
    problems = []
    try:
        normalize(merged)
        checked=copy.deepcopy(project)
        workspace.apply_inputs(checked,merged)
    except db.StudioError as exc: problems.append(str(exc))
    return dict(project_id=project['id'], revision=project['revision'], inputs=merged,
                conflicts=conflicts, validation_errors=problems,
                changed=current != merged, state=state)


def preview(root, transfer, project_id, resolutions=None):
    incoming, warnings, unresolved = validate_transfer(root, transfer)
    plan = build_plan(db.load(project_id), incoming, transfer, resolutions)
    plan.pop('state')
    return dict(schema='manga-a-import-preview-v1', **plan, warnings=warnings, unresolved=unresolved,
                ready=not unresolved and not plan['validation_errors'] and all(c['choice'] for c in plan['conflicts']))


def apply(root, transfer, project_id, revision, resolutions=None):
    if db.directory(project_id).resolve().is_relative_to(Path(root).resolve()):
        raise db.StudioError('取込先のS作品はA作品の外に置いてください')
    incoming, warnings, unresolved = validate_transfer(root, transfer)
    if unresolved: raise db.StudioError('未確認の変換差分があります。previewのコードごとに確認理由を記録してください')
    with db.LOCK:
        p = db.load(project_id)
        if type(revision) is not int or p['revision'] != revision: raise db.StudioError('編集競合: 最新のrevisionでpreviewをやり直してください')
        plan = build_plan(p, incoming, transfer, resolutions)
        if plan['validation_errors']: raise db.StudioError('結合後の入力が不正です: ' + '; '.join(plan['validation_errors']))
        if any(not c['choice'] for c in plan['conflicts']): raise db.StudioError('AとSの競合を解決してください。previewで対象を確認できます')
        old = plan['state']
        if old and not plan['changed'] and old['source'] == transfer['source'] and old['base'] == incoming and old['bindings'] == transfer['bindings']:
            return dict(project_id=project_id, revision=p['revision'], changed=False)
        record = dict(schema=STATE_SCHEMA, source=transfer['source'], base=incoming, bindings=transfer['bindings'],
                      acknowledgements=transfer['acknowledgements'], resolutions=resolutions or {}, warnings=warnings,
                      previous=p.get('import_state'), imported_at=db.now(), revision=revision+1)
        if len(json.dumps(record, ensure_ascii=False, indent=2).encode('utf-8')) > a_source.MAX_JSON:
            raise db.StudioError('取込記録が大きすぎます。資料を必要な範囲へ分けてください')
        key = a_source.digest(record)
        def commit(current):
            if a_source.snapshot(root, transfer['source']['id'], transfer['source'].get('documents')) != transfer['source']:
                raise db.StudioError('保存直前にAの原資料が変わりました')
            # Existing images/candidates are retained by apply_inputs; removed panels remain in project history.
            workspace.apply_inputs(current, plan['inputs'])
            db.dump(db.directory(project_id) / 'imports' / (key + '.json'), record)
            current['import_state'] = key
        result = db.mutate(project_id, revision, commit)
        (db.directory(project_id)/'source.txt').write_text(result.get('source',''),encoding='utf-8')
        return dict(project_id=project_id, revision=result['revision'], changed=True, import_state=key)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Aの字コンテ・NovelAI要求をSへ取り込む（生成なし）')
    sub = parser.add_subparsers(dest='command', required=True)
    inspect = sub.add_parser('inspect'); inspect.add_argument('--source', required=True); inspect.add_argument('--source-id'); inspect.add_argument('--out', required=True)
    inspect.add_argument('--document', action='append', default=[], help='追加の字コンテ等の相対パス（複数指定可）')
    for name in ('preview', 'apply'):
        command = sub.add_parser(name); command.add_argument('--source', required=True); command.add_argument('--transfer', required=True)
        command.add_argument('--project', required=True); command.add_argument('--resolutions')
        if name == 'preview': command.add_argument('--out', required=True)
        else: command.add_argument('--revision', required=True, type=int)
    args = parser.parse_args(argv)
    try:
        if args.command == 'inspect':
            result = inspect_source(args.source, args.source_id, args.document)
            write_output(args.source, args.out, result)
            print(json.dumps(dict(schema=result['schema'], pages=len(result['inputs']['pages']), issues=len(result['issues']), output=args.out), ensure_ascii=False))
        else:
            transfer = a_source.read_json(args.transfer)
            resolutions = a_source.read_json(args.resolutions) if args.resolutions else None
            if args.command == 'preview':
                result = preview(args.source, transfer, args.project, resolutions)
                write_output(args.source, args.out, result)
                print(json.dumps({k:result[k] for k in ('project_id','revision','ready','changed')},ensure_ascii=False))
            else:
                print(json.dumps(apply(args.source,transfer,args.project,args.revision,resolutions),ensure_ascii=False))
        return 0
    except (db.StudioError, OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError) as exc:
        # Raw JSON and external OS errors can contain paths/data; only our bounded validation messages are shown.
        message = str(exc) if isinstance(exc, db.StudioError) else '入力ファイルの形式または読書きに問題があります'
        print('エラー: ' + message, file=sys.stderr)
        return 1


if __name__ == '__main__': sys.exit(main())
