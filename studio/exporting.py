"""All downloads are assembled on the backend from one saved snapshot."""
from __future__ import annotations
import copy
import json
import uuid
import zipfile
from PIL import Image, ImageChops
from . import project as db, workspace, rendering

def dialogue_markdown(p):
    names={c['id']:c['name'] for c in p['characters']}
    lines=['# '+p['title']+' — 台詞一覧','',f'保存時 revision: {p["revision"]}','']
    for number,page in enumerate(p['pages'],1):
        lines += [f'## {number}ページ','']
        source=page.get('whole',{}).get('draft',page) if p.get('render_mode')=='whole' else page
        for i,panel in enumerate(source['panels'],1):
            lines += [f'### コマ {i} ({panel.get("id", "page")})','']
            speakers=panel.get('speakers',['']*len(panel.get('dialogue',[])))
            for speaker,text in zip(speakers,panel.get('dialogue',[])):
                lines.append(f'- {names.get(speaker,"話者未指定／ナレーション")}: '+text.replace('\n',' / '))
            if not panel.get('dialogue'): lines.append('（台詞なし）')
            lines.append('')
    return '\n'.join(lines)

def write_psd(path,layers,project,number):
    try:
        from psd_tools import PSDImage
        from psd_tools.api.layers import PixelLayer,Group
    except ImportError: raise db.StudioError('PSD出力にはrequirements.txtのpsd-toolsをインストールしてください') from None
    psd=PSDImage.new('RGB',(rendering.WIDTH,rendering.HEIGHT))
    originals=Group.new(psd,'元画像・未加工'); originals.visible=False
    page=project['pages'][number-1]
    sources=[c for c in page['panels'] if c.get('image')] if project.get('render_mode')!='whole' else ([page['whole']] if page.get('whole',{}).get('image') else [])
    for i,source in enumerate(sources,1):
        with Image.open(db.asset_path(project['id'],source['image'])) as im: PixelLayer.frompil(im.convert('RGBA'),originals,f'原画 {i}')
    for name,image,(x,y) in layers: PixelLayer.frompil(image,psd,name,top=y,left=x)
    for layer in psd.descendants(): layer.name=layer.name
    psd.save(path)
    reopened=PSDImage.open(path)
    expected=rendering.flatten(layers); actual=reopened.composite(force=True,apply_icc=False).convert('RGB')
    extremes=ImageChops.difference(actual,expected).getextrema()
    maximum=max(high for low,high in extremes)
    if maximum>1: raise db.StudioError(f'PSD読戻しの合成差が大きすぎます: {maximum}')
    return dict(layers=len(list(reopened.descendants())),composite_max_difference=maximum,text_layers='raster',native_text=False,application_readback=False)

def bundle(project_id,kind='png',include_text=True,progress=None):
    if kind not in ('png','psd'): raise db.StudioError('書出し形式はpng/psdです')
    if type(include_text) is not bool: raise db.StudioError('台詞表示は真偽値です')
    p=copy.deepcopy(db.load(project_id))
    if not p['pages']: raise db.StudioError('書き出すページがありません')
    with db.exclusive(db.directory(project_id)/'.export.lock'):
        token=uuid.uuid4().hex[:12]; folder=db.directory(project_id)/'exports'/token; folder.mkdir(parents=True)
        records=[]
        for number in range(1,len(p['pages'])+1):
            if progress: progress(f'{kind.upper()}書出し {number}/{len(p["pages"])}')
            layers,record=rendering.page_layers(p,number,include_text,require_images=True)
            name=f'page-{number:03}'
            rendering.flatten(layers).save(folder/(name+'.png'))
            if kind=='psd': record['psd_verification']=write_psd(folder/(name+'.psd'),layers,p,number)
            records.append(record)
        (folder/'dialogue.md').write_text(dialogue_markdown(p),encoding='utf-8')
        db.dump(folder/'settings.json',workspace.config_export(project_id,'all'))
        db.dump(folder/'manifest.json',dict(revision=p['revision'],kind=kind,include_text=include_text,pages=records))
        archive=folder/'download.zip'
        with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
            for file in sorted(folder.iterdir()):
                if file!=archive: z.write(file,file.name)
        return dict(path=archive.relative_to(db.directory(project_id)).as_posix(),pages=len(records),kind=kind,include_text=include_text,revision=p['revision'])

def config_download(project_id,scope):
    result=workspace.config_export(project_id,scope)
    path=db.directory(project_id)/'exports'/('settings-'+uuid.uuid4().hex[:12]+'.json')
    db.dump(path,result)
    return dict(path=path.relative_to(db.directory(project_id)).as_posix())
