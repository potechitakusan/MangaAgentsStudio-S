from __future__ import annotations
import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from . import project as db

def main():
    parser = argparse.ArgumentParser(description='MangaAgentsStudio-S')
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('create')
    init.add_argument('id'); init.add_argument('--source', required=True); init.add_argument('--title', default='新しい漫画')
    a = sub.add_parser('agent')
    a.add_argument('id'); a.add_argument('--instruction', default='原作を漫画にしてください。字コンテとNovelAIプロンプトを作成して保存してください。')
    a.add_argument('--pages', type=int, default=1); a.add_argument('--panel')
    a.add_argument('--provider', choices=('strata','grok'), default='strata')
    a.add_argument('--append', action='store_true'); a.add_argument('--section', type=int, help='原作の章番号（1から、コマ修正は自動選択）')
    c = sub.add_parser('compose'); c.add_argument('id')
    i = sub.add_parser('import'); i.add_argument('id'); i.add_argument('file')
    g = sub.add_parser('generate'); g.add_argument('id'); g.add_argument('--panel'); g.add_argument('--missing', action='store_true'); g.add_argument('--request-id')
    g.add_argument('--from-page', type=int); g.add_argument('--to-page', type=int)
    pg = sub.add_parser('generate-page'); pg.add_argument('id'); pg.add_argument('--page', type=int, required=True)
    pg.add_argument('--format', choices=('prose','json','yaml')); pg.add_argument('--seed',type=int); pg.add_argument('--plan',help='ページ設計JSONファイル'); pg.add_argument('--request-id')
    pm = sub.add_parser('mode'); pm.add_argument('id'); pm.add_argument('mode',choices=('panels','whole'))
    pp = sub.add_parser('page-plan'); pp.add_argument('id'); pp.add_argument('--page',type=int,required=True)
    sub.add_parser('status'); sub.add_parser('ledger')
    sub.add_parser('serve').add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    try:
        if args.command == 'create':
            result = db.create(args.id, args.title, Path(args.source).read_text(encoding='utf-8-sig'))
        elif args.command == 'agent':
            from .agent import run
            from . import llm
            os.chdir(db.directory(args.id))
            with llm.using(args.provider):
                result = run(args.id, args.instruction, args.pages, args.panel, progress=lambda s: print(s, flush=True), append=args.append, source_section=args.section-1 if args.section is not None else None)
            from .compose import compose
            compose(args.id)
        elif args.command == 'compose':
            from .compose import compose
            result = compose(args.id)
        elif args.command == 'import':
            result = db.import_board(args.id, db.load(args.id)['revision'], db.read_json(args.file))
        elif args.command == 'generate':
            from .novelai import generate
            from .compose import compose
            p = db.load(args.id)
            if not args.panel and not args.missing: raise db.StudioError('--panelまたは--missingを指定してください')
            targets = db.generation_targets(p, args.panel, args.from_page, args.to_page)
            result = []
            for index, pid in enumerate(targets):
                if index: time.sleep(3)
                request_id = (args.request_id if len(targets)==1 else args.request_id+f'-{index}') if args.request_id else uuid.uuid4().hex
                r = generate(args.id, pid, request_id)
                print(json.dumps(r, ensure_ascii=False), flush=True)
                result.append(r)
                compose(args.id)
                if r['status'] != 'complete': break
        elif args.command in ('generate-page','mode','page-plan'):
            from . import wholepage
            from .compose import compose
            p = db.load(args.id)
            if args.command == 'page-plan': result = wholepage.settings(p,args.page)
            elif args.command == 'mode':
                result = wholepage.set_mode(args.id,p['revision'],args.mode)
                compose(args.id)
            else:
                from .novelai import generate
                config = wholepage.settings(p,args.page)
                if args.format: config['format'] = args.format
                if args.seed is not None: config['seed'] = args.seed
                if args.plan: config['draft'] = db.read_json(args.plan)
                p = wholepage.save(args.id,p['revision'],args.page,config)
                p = wholepage.set_mode(args.id,p['revision'],'whole')
                result = generate(args.id,None,args.request_id or uuid.uuid4().hex,p['revision'],page_number=args.page)
                compose(args.id)
        elif args.command in ('status', 'ledger'):
            from . import novelai
            result = getattr(novelai, args.command)()
        else:
            from .server import serve
            serve(args.port)
            return 0
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (db.StudioError, OSError, ValueError) as exc:
        print('エラー: ' + str(exc), file=sys.stderr)
        return 1

if __name__ == '__main__': sys.exit(main())
