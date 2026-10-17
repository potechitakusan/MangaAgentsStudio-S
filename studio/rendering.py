"""Backend page layers shared by preview, PNG and PSD exports."""
from __future__ import annotations
from PIL import Image, ImageDraw, ImageOps
from . import project as db, layouts
from .compose import WIDTH, HEIGHT, MARGIN, font, wrap, crop_box, rectangle

def geometry(page):
    return [[(MARGIN+round(x*(WIDTH-2*MARGIN)),MARGIN+round(y*(HEIGHT-2*MARGIN))) for x,y in poly]
            for poly in layouts.polygons(page['template'],db.TEMPLATES[page['template']])]

def text_layers(panel, rect):
    x,y,w,h=rect; lines=panel.get('dialogue',[]); result=[]
    if not lines: return result
    gap,pad=10,14
    bw=max(40,(w-32-gap*(len(lines)-1))//len(lines))
    measure=ImageDraw.Draw(Image.new('RGB',(1,1)))
    for n,line in enumerate(lines):
        size=29
        while True:
            face=font(size); wrapped=wrap(measure,line,face,max(16,bw-2*pad)); bh=len(wrapped)*(size+6)+2*pad
            if bh<=h*.48 or size<=16: break
            size-=1
        if bh>h-24: raise db.StudioError(f'{panel.get("id", "page")}: 台詞が長すぎます')
        bx=x+w-16-(n+1)*bw-n*gap; by=y+h-bh-16
        layer=Image.new('RGBA',(bw,bh)); draw=ImageDraw.Draw(layer)
        draw.rounded_rectangle((0,0,bw-1,bh-1),radius=14,fill='white',outline='#161a20',width=2)
        draw.multiline_text((pad,pad),'\n'.join(wrapped),fill='#141820',font=face,spacing=6)
        meta=dict(text=line,lines=wrapped,rect=[bx,by,bw,bh],font_size=size,font='Meiryo',orientation='horizontal',speaker=(panel.get('speakers') or ['']*len(lines))[n])
        result.append((layer,(bx,by),meta))
    return result

def page_layers(project,number,include_text=True,require_images=False):
    page=project['pages'][number-1]
    layers=[('用紙',Image.new('RGBA',(WIDTH,HEIGHT),'white'),(0,0))]
    whole_mode=project.get('render_mode')=='whole'
    record=dict(page=number,template=page['template'],file=f'output/{"whole-" if whole_mode else ""}page-{number:03}.png',panels=[])
    if whole_mode:
        whole=page.get('whole',{}); source=whole.get('image')
        embedded=whole.get('generated_text_mode','embedded')=='embedded'
        record.update(mode='whole',image=source,embedded_text=embedded,actual_panel_bounds_known=False,planned_template=(whole.get('draft') or page)['template'],unintended_generated_text_may_remain=True)
        if source and not include_text and embedded: raise db.StudioError('この一括画像には台詞が焼き込まれています。文字を「後付け」にして再生成すると台詞なし出力ができます')
        if source:
            with Image.open(db.asset_path(project['id'],source)) as im:
                fitted=ImageOps.contain(im.convert('RGBA'),(WIDTH,HEIGHT),Image.Resampling.LANCZOS)
                layers.append(('ページ一括画像',fitted,((WIDTH-fitted.width)//2,(HEIGHT-fitted.height)//2)))
                record['source_size']=list(im.size)
        else:
            if require_images: raise db.StudioError(f'{number}ページの画像が未生成です')
            placeholder=Image.new('RGBA',(WIDTH,HEIGHT)); ImageDraw.Draw(placeholder).text((70,100),f'PAGE {number:02}\nページ一括画像は未生成です',font=font(30),fill='#536174'); layers.append(('未生成',placeholder,(0,0)))
        if include_text and not embedded and source:
            draft=whole.get('draft') or page
            record['planned_text_layers']=[]
            for panel,box in zip(draft['panels'],db.TEMPLATES[draft['template']]):
                for n,(layer,offset,meta) in enumerate(text_layers(panel,rectangle(box)),1):
                    layers.append((f'{panel.get("id","page")} 台詞 {n}',layer,offset)); record['planned_text_layers'].append(meta)
        return layers,record
    for panel,polygon in zip(page['panels'],geometry(page)):
        xs,ys=zip(*polygon); x,y=min(xs),min(ys); w,h=max(xs)-x,max(ys)-y
        mask=Image.new('L',(w,h)); ImageDraw.Draw(mask).polygon([(px-x,py-y) for px,py in polygon],fill=255)
        entry=dict(id=panel['id'],rect=[x,y,w,h],polygon=polygon,image=panel.get('image'),location_id=panel.get('location_id'),prop_ids=panel.get('prop_ids',[]),text_layers=[])
        if panel.get('image'):
            with Image.open(db.asset_path(project['id'],panel['image'])) as im:
                box=crop_box(im.size,(w,h),panel.get('crop',[.5,.5]))
                art=im.convert('RGB').resize((w,h),Image.Resampling.LANCZOS,box=box).convert('RGBA')
                entry.update(source_size=list(im.size),source_crop=box)
        else:
            if require_images: raise db.StudioError(f'{panel["id"]}の画像が未生成です')
            art=Image.new('RGBA',(w,h),'#e9edf2'); draw=ImageDraw.Draw(art)
            draw.multiline_text((20,20),'\n'.join(wrap(draw,panel['id']+'\n'+panel['scene'],font(26),max(30,w-40))),font=font(26),fill='#536174',spacing=8)
        art.putalpha(mask); layers.append((panel['id']+' コマ画像',art,(x,y)))
        if include_text:
            # Keep text inset from slanted edges; ordinary rectangles preserve legacy placement.
            rect=[x,y,w,h]
            if len({px for px,py in polygon})>2 or len({py for px,py in polygon})>2:
                rect=[x+round(w*.12),y+round(h*.08),round(w*.76),round(h*.84)]
            for n,(layer,offset,meta) in enumerate(text_layers(panel,rect),1):
                layers.append((f'{panel["id"]} 台詞 {n}',layer,offset)); entry['text_layers'].append(meta)
        border=Image.new('RGBA',(w+1,h+1)); ImageDraw.Draw(border).line([(px-x,py-y) for px,py in polygon]+[(polygon[0][0]-x,polygon[0][1]-y)],fill='#15191e',width=4,joint='curve')
        layers.append((panel['id']+' コマ枠',border,(x,y))); record['panels'].append(entry)
    return layers,record

def flatten(layers):
    canvas=Image.new('RGBA',(WIDTH,HEIGHT))
    for _,image,offset in layers: canvas.alpha_composite(image,offset)
    return canvas.convert('RGB')

def compose(project_id):
    with db.LOCK,db.exclusive(db.directory(project_id)/'.compose.lock'):
        project=db.load(project_id)
        if not project['pages']: raise db.StudioError('先に字コンテを作成してください')
        db.validate_board(project,allow_incomplete=project.get('workspace_version')==2)
        out=db.directory(project_id)/'output'; out.mkdir(exist_ok=True)
        manifest=dict(project_id=project_id,revision=project['revision'],reading_order='right-to-left',canvas=[WIDTH,HEIGHT],generated_at=db.now(),pages=[])
        for number in range(1,len(project['pages'])+1):
            layers,record=page_layers(project,number)
            target=db.directory(project_id)/record['file']; flatten(layers).save(target.with_suffix('.tmp'),format='PNG'); target.with_suffix('.tmp').replace(target)
            manifest['pages'].append(record)
        columns=min(3,len(project['pages'])); overview=Image.new('RGB',(columns*320,((len(project['pages'])+columns-1)//columns)*480),'#e9edf2')
        for i,record in enumerate(manifest['pages']):
            x,y=(i%columns)*320+10,(i//columns)*480+24
            with Image.open(db.directory(project_id)/record['file']) as im: overview.paste(im.resize((300,450),Image.Resampling.LANCZOS),(x,y))
            ImageDraw.Draw(overview).text((x,y-23),f'PAGE {i+1:02}',fill='#15191e',font=font(15))
        name='whole-overview' if project.get('render_mode')=='whole' else 'overview'
        overview.save(out/(name+'.png')); manifest['overview']=f'output/{name}.png'; db.dump(out/'manifest.json',manifest)
        return manifest
