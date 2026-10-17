"""Local copy of A's polygon templates; no reference-folder runtime dependency."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = json.loads((ROOT/'templates/panel-templates/catalog.json').read_text(encoding='utf-8'))
IMPORTED = {t['id']:t for t in CATALOG['templates']}

def polygons(template, rectangles):
    if template in IMPORTED:
        return [[(x/1000,y/1500) for x,y in panel['polygon']] for panel in IMPORTED[template]['panels']]
    return [[(x,y),(x+w,y),(x+w,y+h),(x,y+h)] for x,y,w,h in rectangles]

def bounds(points):
    xs,ys=zip(*points)
    return (min(xs),min(ys),max(xs)-min(xs),max(ys)-min(ys))

RECTANGLES = {key:[bounds(p) for p in polygons(key,[])] for key in IMPORTED}

def catalog(rectangles):
    return [dict(id=key,name=IMPORTED[key]['name'] if key in IMPORTED else key,
                 count=len(boxes),polygons=polygons(key,boxes),
                 summary=IMPORTED[key]['summary'] if key in IMPORTED else 'S初期版のコマ割り') for key,boxes in rectangles.items()]
