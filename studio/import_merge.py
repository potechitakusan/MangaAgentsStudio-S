"""Three-way input merge. Entity IDs are stable; removals require a decision."""
import copy
from . import project as db

MISSING = object()
COLLECTIONS = {'pages', 'panels', 'characters', 'locations', 'props', 'outfits'}


def equal(a, b):
    return a is b if a is MISSING or b is MISSING else a == b


def clone(value):
    return value if value is MISSING else copy.deepcopy(value)


def pointer(path, key):
    return path + '/' + str(key).replace('~', '~0').replace('/', '~1')


def merge(base, local, incoming, decisions=None):
    decisions = decisions or {}
    if not isinstance(decisions, dict) or any(v not in ('local', 'incoming') for v in decisions.values()):
        raise db.StudioError('競合の選択はJSONパスごとにlocalまたはincomingです')
    conflicts, used = [], set()

    def conflict(b, l, r, path, reason):
        choice = decisions.get(path)
        if choice: used.add(path)
        def display(v): return {'present': v is not MISSING, **({'value': v} if v is not MISSING else {})}
        conflicts.append(dict(path=path, reason=reason, base=display(b), local=display(l), incoming=display(r), choice=choice))
        return clone(r if choice == 'incoming' else l)

    def visit(b, l, r, path, entity=False):
        if equal(l, r): return clone(l)
        if entity and b is not MISSING and r is MISSING and l is not MISSING:
            return conflict(b, l, r, path, 'A側で削除された項目')
        if equal(r, b): return clone(l)
        structured = (all(isinstance(v, dict) for v in (b,l,r)) or
                      (path.rsplit('/',1)[-1] in COLLECTIONS and all(isinstance(v,list) for v in (b,l,r))))
        if equal(l, b) and not structured: return clone(r)
        if MISSING is b or MISSING is l or MISSING is r:
            return conflict(b, l, r, path, '追加・削除と編集が重なっています')
        if all(isinstance(v, dict) for v in (b, l, r)):
            result = {}
            for key in sorted(set(b) | set(l) | set(r)):
                value = visit(b.get(key, MISSING), l.get(key, MISSING), r.get(key, MISSING), pointer(path, key))
                if value is not MISSING: result[key] = value
            return result
        if path.rsplit('/', 1)[-1] in COLLECTIONS and all(isinstance(v, list) for v in (b, l, r)):
            if not all(all(isinstance(e, dict) and isinstance(e.get('id'), str) for e in v) for v in (b, l, r)):
                return conflict(b, l, r, path, '配列の形式が変わっています')
            for value in (b, l, r):
                if len({e['id'] for e in value}) != len(value): raise db.StudioError('取込比較のIDが重複しています')
            bm, lm, rm = ({e['id']: e for e in value} for value in (b, l, r))
            merged = {}
            for key in sorted(set(bm) | set(lm) | set(rm)):
                value = visit(bm.get(key, MISSING), lm.get(key, MISSING), rm.get(key, MISSING), pointer(path, key), True)
                if value is not MISSING: merged[key] = value
            order = visit(list(bm), list(lm), list(rm), pointer(path, '@order'))
            for prior in (list(lm), list(rm)):
                for i, key in enumerate(prior):
                    if key not in merged or key in order: continue
                    after = next((k for k in prior[i+1:] if k in order), None)
                    order.insert(order.index(after) if after is not None else len(order), key)
            return [merged[key] for key in dict.fromkeys(order + list(merged)) if key in merged]
        return conflict(b, l, r, path, 'AとSの両方で変更されています')

    result = visit(base, local, incoming, '')
    if set(decisions) - used: raise db.StudioError('現在の競合に対応しない選択があります。最新のpreviewを確認してください')
    return result, conflicts
