"""Deterministic page render and an editable-layer handoff manifest."""
from __future__ import annotations
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps
from . import project as db

WIDTH, HEIGHT, MARGIN = 1200, 1800, 48

def font(size):
    for path in ('C:/Windows/Fonts/meiryo.ttc', 'C:/Windows/Fonts/YuGothM.ttc', 'C:/Windows/Fonts/msgothic.ttc'):
        if Path(path).exists(): return ImageFont.truetype(path, size)
    raise db.StudioError('日本語フォントがありません。compose.pyに日本語フォントを設定してください')

def wrap(draw, text, face, width):
    lines, line = [], ''
    for ch in text:
        if ch == '\n' or draw.textlength(line + ch, font=face) > width:
            lines.append(line)
            line = '' if ch == '\n' else ch
        else: line += ch
    if line: lines.append(line)
    return lines

def rectangle(box):
    x, y, w, h = box
    return [MARGIN + round(x * (WIDTH - 2 * MARGIN)), MARGIN + round(y * (HEIGHT - 2 * MARGIN)),
            round(w * (WIDTH - 2 * MARGIN)), round(h * (HEIGHT - 2 * MARGIN))]

def crop_box(image_size, target_size, center):
    iw, ih = image_size
    tw, th = target_size
    scale = max(tw / iw, th / ih)
    cw, ch = tw / scale, th / scale
    left, top = (iw - cw) * center[0], (ih - ch) * center[1]
    return [left, top, left + cw, top + ch]

def compose(project_id):
    from .rendering import compose as render
    return render(project_id)
