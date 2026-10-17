"""Page plan: the source split into pages before any storyboard is drawn.

Why a separate step: measured on Strata (2026-10-17, docs/records/STRATA_UNITS.md), one call that both paces a chapter
and writes every panel either thins the detail (4-8 pages per call) or loses the pacing (1 page per call crams the
chapter into the first pages and then repeats a scene). A short plan call (about 100 tokens per page) fixes the pacing;
the storyboard calls then only draw the pages they were given, so any call size keeps the order."""
from __future__ import annotations
import json
from . import project as db, agent, workspace, llm

def clean(plan, project):
    """Validates a stored or edited plan. Page numbers are continuous; chapters never go backwards."""
    if plan in (None, {}): return None
    if not isinstance(plan, dict) or not isinstance(plan.get('pages'), list): raise db.StudioError('ページ割りはpages配列です')
    rows = plan['pages']
    if not 1 <= len(rows) <= db.MAX_PAGES: raise db.StudioError('ページ割りは1〜24ページです')
    sections = db.source_sections(project['id'])
    first = rows[0].get('page') if isinstance(rows[0], dict) else None
    result = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict): raise db.StudioError('ページ割りの各ページはオブジェクトです')
        number, section, panels = row.get('page'), row.get('section'), row.get('panels', 4)
        if type(number) is not int or type(first) is not int or number != first + i or not 1 <= number <= db.MAX_PAGES:
            raise db.StudioError('ページ割りのページ番号は連番で24以下です')
        if type(section) is not int or not 0 <= section < len(sections): raise db.StudioError(f'PAGE {number}: 原作の章番号が不正です')
        if result and section < result[-1]['section']: raise db.StudioError(f'PAGE {number}: 章の順序が戻っています')
        if type(panels) is not int or not 1 <= panels <= 7: raise db.StudioError(f'PAGE {number}: コマ数は1〜7です')
        db.text_field(row.get('summary'), f'PAGE {number}の内容', 1000)
        lines = row.get('lines', [])
        if not isinstance(lines, list) or len(lines) > 12 or any(not isinstance(x, str) for x in lines):
            raise db.StudioError(f'PAGE {number}: 台詞は12行以内の文字列です')
        for x in lines: db.text_field(x, '台詞', 200, True)
        result.append(dict(page=number, section=section, panels=panels, summary=row['summary'].strip(), lines=[x.strip() for x in lines if x.strip()]))
    return dict(pages=result, created_at=plan.get('created_at') or db.now(), **({'provider': plan['provider']} if plan.get('provider') else {}))

def rows_for(project, first, count):
    """Plan rows for pages first..first+count-1, or [] when the plan does not cover all of them."""
    rows = {r['page']: r for r in (project.get('page_plan') or {}).get('pages', [])}
    picked = [rows.get(n) for n in range(first, first + count)]
    return picked if all(picked) else []

def remaining(project):
    done = len(project['pages'])
    return [r for r in (project.get('page_plan') or {}).get('pages', []) if r['page'] > done]

def next_chunk(project, size):
    """The next pages to draw from the plan: up to `size` rows in a row that share one chapter."""
    rows = remaining(project)
    if not rows or rows[0]['page'] != len(project['pages']) + 1: return []
    chunk = [rows[0]]
    for row in rows[1:size]:
        if row['section'] != chunk[0]['section'] or row['page'] != chunk[-1]['page'] + 1: break
        chunk.append(row)
    return chunk

def instruction(rows):
    """What the storyboard call is told to draw: only these pages; the next page's events wait."""
    lines = ['今回のページの割り当て（ページ割り）。各ページはこの出来事だけを描き、次のページの出来事を先取りしない。台詞は原作の文言を使い、割り当てにない台詞を足さない:']
    for r in rows:
        lines.append(f"PAGE {r['page']}（{r['panels']}コマ前後）: {r['summary']} 台詞: {' / '.join(r['lines']) if r['lines'] else 'なし'}")
    return '\n'.join(lines)

PLAN_SYSTEM = ('漫画のページ割りを作る。原作を読み、各ページに描く出来事を原作の順番どおりに割り当てる。'
    '1ページは3〜5コマ分の出来事にする。出来事の飛ばし・重複・原作にない展開をしない。'
    'summaryは日本語2文以内で、そのページの始まりと終わりの出来事が分かるように書く。'
    'linesにはそのページに入る原作の台詞（「」の中身）を原作どおりの文言・順序で入れる。台詞が無ければ空配列。同じ台詞を2ページに入れない。'
    'sectionはそのページの元になる章のindex。1ページに2つの章を混ぜない。'
    'pagesは指定のページ数ちょうど。章の分量に応じて配分し、見せ場や感情の山場には多めのページを使う。'
    'common_settings（人物・場所の説明）はページに割り当てる出来事ではない。previousがあれば、その続きから始める。'
    'submit_resultを1回呼んで終了する。')

def split_preface(sections):
    """The shared text before the first chapter heading is repeated at the top of every section; return it once."""
    if len(sections) < 2: return '', sections
    first = sections[0]['text']
    head = first.find('## ')
    preface = first[:head] if head > 0 else ''
    return preface.strip(), [dict(index=s['index'], title=s['title'], text=s['text'][len(preface):].strip() if preface and s['text'].startswith(preface) else s['text']) for s in sections]

def make(project_id, revision, first_section, total_pages, request='', transport=None):
    """One Strata/Grok call: split the source from first_section to the end into total_pages pages after the existing ones."""
    from .strata_tasks import structured
    p = db.load(project_id)
    if p['revision'] != revision: raise db.StudioError('編集競合: 最新の内容を読み直してください')
    done = len(p['pages'])
    if type(total_pages) is not int or not 1 <= total_pages <= db.MAX_PAGES - done:
        raise db.StudioError(f'ページ割りは1〜{db.MAX_PAGES - done}ページです（同じ作品で最大24ページ）')
    sections = db.source_sections(project_id)
    if type(first_section) is not int or not 0 <= first_section < len(sections): raise db.StudioError('開始する章が不正です')
    db.text_field(request, '構成の希望', 2000, True)
    preface, body = split_preface(sections)
    body = body[first_section:]
    previous = (p.get('batches') or [])[-1:]
    content = dict(start_page=done + 1, target_pages=total_pages, common_settings=preface, sections=body,
                   previous=dict(end_page=previous[0]['end_page'], handoff=previous[0]['handoff']) if previous else None,
                   request=request.strip())
    if len(json.dumps(content, ensure_ascii=False)) > 90000:
        raise db.StudioError('原作が長すぎてページ割りを1回で作れません。開始する章を後ろにするか、原作を短く区切ってください')
    row = agent.object_schema(dict(page={'type': 'integer'}, section={'type': 'integer'}, panels={'type': 'integer', 'minimum': 1, 'maximum': 6},
                                   summary={'type': 'string'}, lines={'type': 'array', 'items': {'type': 'string'}}), ('page', 'section', 'panels', 'summary', 'lines'))
    schema = agent.object_schema(dict(pages={'type': 'array', 'items': row, 'minItems': total_pages, 'maxItems': total_pages}), ('pages',))
    allowed = {s['index'] for s in body}
    def check(value):
        rows = value.get('pages') if isinstance(value, dict) else None
        if not isinstance(rows, list) or len(rows) != total_pages:
            raise db.StudioError(f'pagesが{len(rows) if isinstance(rows, list) else 0}件です。ちょうど{total_pages}件にしてください')
        for i, r in enumerate(rows):
            if isinstance(r, dict): r['page'] = done + 1 + i     # numbering is the host's job
        plan = clean(dict(pages=rows), p)
        if any(r['section'] not in allowed for r in plan['pages']): raise db.StudioError(f'sectionは{sorted(allowed)}のいずれかです')
        return plan
    system = PLAN_SYSTEM + '\nユーザー共通制作指示:\n' + workspace.effective_system(p).get('storyboard', '')
    plan = structured(project_id, 'page-plan', system, content, schema, check, transport)
    def apply(q):
        kept = [r for r in (q.get('page_plan') or {}).get('pages', []) if r['page'] <= done]
        q['page_plan'] = dict(pages=kept + plan['pages'], created_at=db.now(), provider=llm.current())
    return db.mutate(project_id, revision, apply)

def missing_ja(project, pages=None):
    """(panel id, actor id or None, english field, japanese field, english text) for every empty Japanese check field."""
    out = []
    for n, page in enumerate(project['pages'], 1):
        if pages and n not in pages: continue
        for c in page['panels']:
            for en, ja in (('prompt', 'prompt_ja'), ('negative', 'negative_ja')):
                if (c.get(en) or '').strip() and not (c.get(ja) or '').strip(): out.append((c['id'], None, en, ja, c[en]))
            for a in c.get('characters', []):
                if (a.get('prompt') or '').strip() and not (a.get('prompt_ja') or '').strip(): out.append((c['id'], a['id'], 'prompt', 'prompt_ja', a['prompt']))
    return out

def fill_ja(project_id, pages=None, progress=None, transport=None):
    """English to Japanese for the empty Japanese check fields of the given pages. Fills empty fields only; a field whose
    English changed meanwhile is skipped. Returns how many fields were filled."""
    from .strata_tasks import translate
    todo = missing_ja(db.load(project_id), pages)
    filled = 0
    for start in range(0, len(todo), 12):
        batch = todo[start:start + 12]
        if progress: progress(f'コマの日本語訳を補っています {start + len(batch)}/{len(todo)}')
        out = {x['id']: x['text'] for x in translate(project_id, [dict(id=f'f{i}', text=t[4]) for i, t in enumerate(batch)], 'ja', transport=transport)}
        def apply(q):
            nonlocal filled
            panels = {c['id']: c for page in q['pages'] for c in page['panels']}
            for i, (panel_id, actor_id, en, ja, text) in enumerate(batch):
                c = panels.get(panel_id)
                target = c if c is not None and actor_id is None else next((a for a in (c or {}).get('characters', []) if a.get('id') == actor_id), None)
                if target is not None and target.get(en) == text and not (target.get(ja) or '').strip() and (out.get(f'f{i}') or '').strip():
                    target[ja] = out[f'f{i}'].strip(); filled += 1
            # A linked page-batch design is built from the panels, so it takes the new Japanese too (as edit_panel does).
            from . import wholepage
            touched = {panel_id for panel_id, *_ in batch}
            for n, page in enumerate(q['pages'], 1):
                if any(c['id'] in touched for c in page['panels']) and page.get('whole', {}).get('linked', q.get('workspace_version') == 2):
                    page.setdefault('whole', {}).update(linked=True, draft=wholepage.from_panels(q, n))
        db.mutate(project_id, db.load(project_id)['revision'], apply)
    return filled
