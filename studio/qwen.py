"""Local ComfyUI QwenImage 2.1 Turbo, with optional Anima/LoRA finishing.

Only fixed, local nodes are submitted. Each stage is sent once and its prompt_id
is durable before polling. Unknown outcomes stop further requests.
"""
from __future__ import annotations
import copy
import hashlib
import io
import json
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from PIL import Image
from . import project as db, profiles, novelai, wholepage, images, references

API = 'http://127.0.0.1:8188'
MODEL = 'qwen_image_2.1_turbo_int8_convrot.safetensors'
CLIP = 'qwen3vl_8b_int8_convrot.safetensors'
VAE = 'qwen_image_2.1_vae_bf16.safetensors'
SIGMAS = '1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0.0'
LORAS = [('Anima-Preview_Turbo_8step.safetensors', .8), ('am_anima_lora.safetensors', .75), ('o1_anima_lora.safetensors', .4)]
MAX_WAIT = 1200

class Rejected(db.StudioError): pass
class ExecutionFailed(db.StudioError): pass

def http(path, payload=None, raw=False, content_type='application/json'):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(API + path, body, {'Content-Type':content_type} if body else {})
    try:
        with urllib.request.build_opener(novelai.NoRedirect()).open(req, timeout=30) as response:
            data=response.read(novelai.MAX_RESPONSE+1)
            if len(data)>novelai.MAX_RESPONSE: raise db.StudioError('ComfyUI応答が大きすぎます')
            return data if raw else json.loads(data)
    except urllib.error.HTTPError as exc:
        if path=='/prompt' and exc.code==400:
            raise Rejected('ComfyUIが要求を拒否しました。ノード・モデル・入力設定を確認してください') from None
        raise db.StudioError(f'ComfyUI HTTP {exc.code}。生成は自動再送しません') from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise db.StudioError('ComfyUIへ接続できません（127.0.0.1:8188）。生成は自動再送しません') from None

def status():
    stats=http('/system_stats')
    return dict(provider='qwen', connected=True, model=MODEL, devices=[dict(name=d['name'], vram_free=d.get('vram_free')) for d in stats.get('devices',[])])

def text(value):
    # Remove only NovelAI weighting delimiters; retain semantic words and actions.
    return str(value).replace('{','').replace('}','').replace('[','').replace(']','').strip(' ,.')

def scenery_style(value):
    # A face-shaped style tag is a strong positive object cue, even after a
    # "no people" instruction. Exclude only anatomy clauses on empty shots.
    return ', '.join(part.strip() for part in value.split(',') if not re.search(r'\b(faces?|eyes?|noses?|mouths?|heads?|proportions)\b',part,re.I))

def scene(project, panel, include_style=True):
    cast={c['id']:c for c in project['characters']}
    places={p['id']:p for p in project.get('locations',[])}
    props={p['id']:p for p in project.get('props',[])}
    parts=[]
    if include_style:
        style=project['style'] if panel.get('characters') else scenery_style(project['style'])
        parts.append('Draw one Japanese manga illustration. Visual style: '+text(style)+'.')
    parts.append('Scene and camera: '+text(profiles.scene_prompt(panel))+'.')
    if panel.get('location_id'): parts.append('Location '+panel['location_id']+': '+text(places[panel['location_id']]['description'])+'.')
    if panel.get('prop_ids'): parts.append('Visible objects: '+'; '.join(text(props[p]['description']) for p in panel['prop_ids'])+'.')
    actors=panel.get('characters',[])
    parts.append(f'There are exactly {len(actors)} visible main characters.' if actors else 'There are no people in this shot.')
    for a in actors:
        c=cast[a['id']]
        x=a.get('x',.5); pos='left' if x<.4 else 'right' if x>.6 else 'center'
        outfit=profiles.outfit_for(c,a)
        parts.append(f"Character {c['id']}, outfit {outfit['id'] if outfit else 'default'}, at the {pos}: {text(profiles.caption(c,a))}.")
    return '\n'.join(parts)

def payload(project, prompt, seed, width, height, negative=''):
    settings=images.for_project(project)
    return dict(provider='qwen', model=MODEL, input=prompt, parameters=dict(seed=seed if seed is not None else secrets.randbelow(4294967296), width=width, height=height, steps=8, cfg=1, sampler='euler', negative_prompt=negative), finish=dict(enabled=settings['anime_finish'],denoise=settings['denoise'],checkpoint='waiANIMA_v10.safetensors',loras=LORAS))

def build_request(project, panel):
    db.validate_board(project,allow_incomplete=project.get('workspace_version')==2)
    # Reuse the proven panel aspect selection, without sending its NAI payload.
    p=novelai.build_request(project,panel)['parameters']
    prompt=scene(project,panel)+'\nNo text, letters, speech balloons, borders, or watermarks.'
    if panel.get('negative'): prompt+='\nAvoid: '+text(panel['negative'])+'.'
    refs=references.collect(project,[panel])
    request=payload(project,prompt,panel.get('seed'),p['width'],p['height'],project.get('style_negative',''))
    request['references']=refs
    if refs: request['input']+='\n'+references.instructions(refs)
    return request

def build_page_request(project, number):
    config=wholepage.settings(project,number)
    draft=wholepage.validate(project,number,config)
    lines=[f"Draw one complete {len(draft['panels'])}-panel Japanese manga page. Read right to left, top to bottom. White gutters. Visual style: {text(project['style'])}."]
    positions=wholepage.POSITIONS.get(draft['template'])
    for i,(panel,rect) in enumerate(zip(draft['panels'],db.TEMPLATES[draft['template']])):
        position=positions[i] if positions else f'normalized rectangle x={rect[0]}, y={rect[1]}, width={rect[2]}, height={rect[3]}'
        lines.append(f'Panel {i+1}, {position}:\n'+scene(project,panel,False))
        if project.get('text_mode','embedded')=='embedded':
            lines.append('Speech bubbles in reading order: '+json.dumps(panel.get('dialogue',[]),ensure_ascii=False))
    if project.get('text_mode','embedded')=='overlay': lines.append('No text, no letters, no speech balloons. Leave space for later lettering.')
    if config['format'] != 'prose':
        plan=dict(instructions=lines[0], panels=[])
        for i, panel in enumerate(draft['panels']):
            row=dict(panel=i+1, position=positions[i] if positions else db.TEMPLATES[draft['template']][i], scene=scene(project,panel,False))
            if project.get('text_mode','embedded')=='embedded':
                row['dialogue']=[dict(speaker=speaker,text=line) for speaker,line in zip(panel.get('speakers',['']*len(panel.get('dialogue',[]))),panel.get('dialogue',[]))]
            plan['panels'].append(row)
        if project.get('text_mode','embedded')=='overlay': plan['lettering']=lines[-1]
        lines=[json.dumps(plan,ensure_ascii=False) if config['format']=='json' else wholepage.yaml_text(plan)]
    refs=references.collect(project,draft['panels'])
    if refs:
        if config['format']!='prose':
            plan['reference_instructions']=references.instructions(refs)
            lines=[json.dumps(plan,ensure_ascii=False) if config['format']=='json' else wholepage.yaml_text(plan)]
        else: lines.append(references.instructions(refs))
    request=payload(project,'\n\n'.join(lines),config['seed'],832,1216,project.get('style_negative',''))
    request['references']=refs
    return request

def preview(project, options):
    prepared=copy.deepcopy(project)
    if isinstance(options,dict) and options.get('kind')=='location': prepared['style']=scenery_style(prepared['style'])
    trial=profiles.preview(prepared,options)
    r=trial['request']; p=r['parameters']
    description='\n'.join(c['char_caption'] for c in p['v4_prompt']['caption']['char_captions'])
    prompt='Draw an anime illustration. '+text(r['input'])+'.\n'+text(description)+'.\nNo text or speech balloons.'
    trial['request']=payload(project,prompt,options.get('seed'),p['width'],p['height'],project.get('style_negative',''))
    trial['reference_profile']=references.profile(project,options['kind'],options['id'],options.get('outfit_id',''))
    return trial

def node(kind, **inputs): return dict(class_type=kind, inputs=inputs)

def graph(request, prefix):
    p=request['parameters']
    # Core LatentUpscale uses an 8-pixel unit; Qwen 2.1 uses 16. Half-sized
    # dimensions yield the exact requested zero-latent shape (64 channels).
    g = {
        '1':node('UNETLoader',unet_name=MODEL,weight_dtype='default'),
        '2':node('CLIPLoader',clip_name=CLIP,type='qwen_image',device='default'),
        '3':node('VAELoader',vae_name=VAE),
        '4':node('QwenImage21Cache',model=['1',0],device='auto',dtype='default'),
        '5':node('TextEncodeQwenImage21',clip=['2',0],vae=['3',0],prompt=request['input'],negative_prompt=p['negative_prompt'],resolution=1024),
        '6':node('KSamplerSelect',sampler_name='euler'),
        '7':node('ManualSigmas',sigmas=SIGMAS),
        '11':node('LatentUpscale',samples=['5',2],upscale_method='nearest-exact',width=p['width']//2,height=p['height']//2,crop='disabled'),
        '8':node('SamplerCustom',model=['4',0],positive=['5',0],negative=['5',1],latent_image=['11',0],sampler=['6',0],sigmas=['7',0],add_noise=True,noise_seed=p['seed'],cfg=1.0),
        '9':node('VAEDecode',samples=['8',0],vae=['3',0]),
        '10':node('SaveImage',images=['9',0],filename_prefix=prefix),
    }
    for i, ref in enumerate(request.get('references',[]), 1):
        key=str(100+i)
        g[key]=node('LoadImage',image='studio-ref-'+ref['sha256']+'.png')
        g['5']['inputs'][f'images.image_{i}']=[key,0]
    return g

def finish_graph(request, image_name, prefix):
    g={
        '1':node('CheckpointLoaderSimple',ckpt_name='waiANIMA_v10.safetensors'),
        '2':node('CLIPLoader',clip_name='qwen_3_06b_base.safetensors',type='lumina2',device='default'),
        '3':node('VAELoader',vae_name='qwen_image_vae.safetensors'),
        '4':node('LoadImage',image=image_name),
        '5':node('VAEEncode',pixels=['4',0],vae=['3',0]),
        '6':node('AnimaLLLiteApply_sdscripts',model=['1',0],image=['4',0],lllite_name='anima-lllite-any-test-like-v2.safetensors',strength=.75,start_percent=0.,end_percent=1.,preserve_wrapper=True),
    }
    model,clip=['6',0],['2',0]
    for i,(name,strength) in enumerate(LORAS,11):
        g[str(i)]=node('LoraLoader',model=model,clip=clip,lora_name=name,strength_model=strength,strength_clip=1.)
        model,clip=[str(i),0],[str(i),1]
    g.update({
        '20':node('CLIPTextEncode',clip=clip,text='anime illustration, clean lineart, cel shading, best quality. '+request['input']),
        '21':node('CLIPTextEncode',clip=clip,text='low quality, blurry, bad anatomy, extra digits, text, watermark'),
        '22':node('KSampler',model=model,positive=['20',0],negative=['21',0],latent_image=['5',0],seed=request['parameters']['seed'],steps=12,cfg=2.5,sampler_name='euler_ancestral',scheduler='simple',denoise=request['finish']['denoise']),
        '23':node('VAEDecode',samples=['22',0],vae=['3',0]),
        '10':node('SaveImage',images=['23',0],filename_prefix=prefix),
    })
    return g

def preflight(g):
    info=http('/object_info')
    for n in g.values():
        kind=n['class_type']
        if kind not in info or info[kind].get('api_node'):
            raise db.StudioError('ComfyUIのローカルノードがありません: '+kind)
        schema=info[kind].get('input',{}).get('required',{})
        for name in ('unet_name','clip_name','vae_name','ckpt_name','lora_name','lllite_name'):
            if name not in n['inputs']: continue
            spec=schema.get(name,[])
            opts=spec[0] if spec and isinstance(spec[0],list) else (spec[1].get('options') if len(spec)>1 and isinstance(spec[1],dict) else None)
            if opts is not None and n['inputs'][name] not in opts:
                raise db.StudioError('ComfyUIにモデルがありません: '+n['inputs'][name])

def upload(png, name, overwrite=False):
    boundary='manga-'+secrets.token_hex(12)
    data=(f'--{boundary}\r\nContent-Disposition: form-data; name="overwrite"\r\n\r\n{str(overwrite).lower()}\r\n--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{name}"\r\nContent-Type: image/png\r\n\r\n').encode()+png+f'\r\n--{boundary}--\r\n'.encode()
    result=http('/upload/image',data,content_type='multipart/form-data; boundary='+boundary)
    return '/'.join(filter(None,[result.get('subfolder',''),result['name']]))

def ledger_path(): return db.ROOT/'.runtime/qwen-ledger.json'
def ledger(): return db.read_json(ledger_path()) if ledger_path().exists() else dict(requests={})
def unresolved(): return any(r['status'] in ('sending','unknown') for r in ledger()['requests'].values())

def run_stage(g, record, stage, persist):
    started=time.monotonic()
    record.setdefault('stages',{})[stage]=dict(status='sending',started_at=db.now())
    persist()
    result=http('/prompt',dict(prompt=g,client_id='MangaAgentsStudio-S'))
    item=record['stages'][stage]
    item.update(prompt_id=result['prompt_id'],status='running'); persist()
    deadline=time.monotonic()+MAX_WAIT
    while time.monotonic()<deadline:
        history=http('/history/'+item['prompt_id']).get(item['prompt_id'])
        if history:
            state=history.get('status',{})
            if state.get('status_str')=='error':
                item.update(status='failed',elapsed_seconds=round(time.monotonic()-started,3));persist()
                raise ExecutionFailed('ComfyUIの生成が失敗しました。保存したprompt_idの履歴を確認してください')
            if state.get('completed'):
                outputs=history.get('outputs',{}).get('10',{}).get('images',[])
                if len(outputs)!=1: raise db.StudioError('ComfyUIの出力画像を一意に確認できません')
                png=http('/view?'+urllib.parse.urlencode(outputs[0]),raw=True)
                events={name:data for name,data in history.get('status',{}).get('messages',[]) if isinstance(data,dict)}
                start=events.get('execution_start',{}).get('timestamp')
                end=events.get('execution_success',{}).get('timestamp')
                item.update(status='complete',elapsed_seconds=round(time.monotonic()-started,3),output=outputs[0])
                if isinstance(start,(int,float)) and isinstance(end,(int,float)): item['server_seconds']=round((end-start)/1000,3)
                persist()
                return png
        time.sleep(1)
    raise db.StudioError('ComfyUIの完了待ちが時間切れです。自動再送しません')

def signature(request):
    clean=copy.deepcopy(request);clean['parameters']['seed']=0
    return hashlib.sha256(json.dumps(clean,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def is_stale(project,panel=None,page_number=None):
    target=project['pages'][page_number-1].get('whole',{}) if page_number is not None else panel
    if not target or not target.get('image'): return True
    path=db.asset_path(project['id'],target['image']).with_suffix('.request.json')
    if not path.exists(): return True
    previous=db.read_json(path)
    if previous.get('provider')!='qwen': return True
    current=build_page_request(project,page_number) if page_number is not None else build_request(project,panel)
    desired=target.get('seed')
    return (desired is not None and desired!=previous['parameters']['seed']) or signature(previous)!=signature(current)

def generate(project_id,panel_id,request_id,expected_revision=None,page_number=None,preview=None):
    db.identifier(request_id)
    if not novelai.GEN_LOCK.acquire(blocking=False): raise db.StudioError('別の画像を生成中です')
    try:
        with db.exclusive(novelai.ledger_path().with_name('novelai.lock')):
            state=ledger()
            if request_id in state['requests']:
                old=state['requests'][request_id]
                if any(old.get(k)!=v for k,v in dict(project_id=project_id,panel_id=panel_id,page_number=page_number,preview=preview).items()):
                    raise db.StudioError('同じ要求IDを別の対象へ使えません')
                return old
            if unresolved() or any(r['status'] in ('sending','unknown') for r in novelai.ledger()['requests'].values()):
                raise db.StudioError('未確認の生成があります。自動再送せず履歴を確認してください')
            p=db.load(project_id)
            if p['output_method']!='qwen': raise db.StudioError('Qwen作品ではありません')
            if expected_revision is not None and p['revision']!=expected_revision: raise db.StudioError('編集競合: 作品を読み直してください')
            if sum([panel_id is not None,page_number is not None,preview is not None])!=1: raise db.StudioError('生成対象を1種類だけ指定してください')
            trial=globals()['preview'](p,preview) if preview is not None else None
            request=trial['request'] if trial else build_page_request(p,page_number) if page_number is not None else build_request(p,db.panel_by_id(p,panel_id))
            folder=db.directory(project_id)/'images'/('previews' if preview is not None else f'page-{page_number:03}' if page_number is not None else panel_id)
            folder.mkdir(parents=True,exist_ok=True)
            file=folder/(request_id+'.png')
            prefix=f'MangaAgentsStudioS/{project_id}/{request_id}'
            g=graph(request,prefix)
            preflight(g)
            if request['finish']['enabled']: preflight(finish_graph(request,'pending.png',prefix+'-anime'))
            for i, ref in enumerate(request.get('references',[]), 1):
                name=upload(references.read_image(p,ref),'studio-ref-'+ref['sha256']+'.png',overwrite=True)
                g[str(100+i)]['inputs']['image']=name
            record=dict(id=request_id,provider='qwen',project_id=project_id,panel_id=panel_id,status='sending',revision=p['revision'],started_at=db.now(),seed=request['parameters']['seed'],image=file.relative_to(db.directory(project_id)).as_posix(),actual_anlas=0)
            if preview is not None:
                record['reference_profile']=trial['reference_profile']
                record.update(preview=preview,name=trial['snapshot']['entry']['name']);db.dump(file.with_suffix('.plan.json'),trial['snapshot'])
            if page_number is not None:
                record.update(page_number=page_number,format=wholepage.settings(p,page_number)['format'],text_mode=p.get('text_mode','embedded'))
                db.dump(file.with_suffix('.plan.json'),wholepage.settings(p,page_number))
            db.dump(file.with_suffix('.request.json'),request);db.dump(file.with_suffix('.workflow.json'),g)
            state['requests'][request_id]=record
            def persist():
                db.dump(ledger_path(),state);db.dump(file.with_suffix('.record.json'),record)
            persist()
            started=time.monotonic()
            try:
                png=run_stage(g,record,'qwen',persist)
                if request['finish']['enabled']:
                    file.with_suffix('.base.png').write_bytes(png)
                    image_name=upload(png,project_id+'-'+request_id+'.png')
                    fg=finish_graph(request,image_name,prefix+'-anime');db.dump(file.with_suffix('.finish-workflow.json'),fg)
                    png=run_stage(fg,record,'anima',persist)
                with Image.open(io.BytesIO(png)) as im:
                    if im.size!=(request['parameters']['width'],request['parameters']['height']): raise db.StudioError('返却画像寸法が要求と違います')
                file.write_bytes(png)
                record.update(status='complete',finished_at=db.now(),elapsed_seconds=round(time.monotonic()-started,3));persist()
            except Exception as exc:
                record.update(status='failed' if isinstance(exc,(Rejected,ExecutionFailed)) else 'unknown',error=str(exc)[:300],finished_at=db.now());persist();raise
            if preview is not None: return record
            with db.LOCK:
                current=db.load(project_id)
                def apply(project):
                    target=wholepage.candidates_target(project,page_number) if page_number is not None else db.panel_by_id(project,panel_id)
                    candidate=dict(id=request_id,image=record['image'],seed=record['seed'],revision=record['revision'],provider='qwen')
                    if page_number is not None: candidate.update(format=record['format'],text_mode=record['text_mode'])
                    target.setdefault('candidates',[]).append(candidate)
                    if current['revision']==p['revision']:
                        target.update(image=record['image'],adopted_request=request_id)
                        if page_number is not None: target['generated_text_mode']=record['text_mode']
                try: db.mutate(project_id,current['revision'],apply)
                except db.StudioError:
                    record['adoption_note']='保存中に作品が変更されました。画像は履歴フォルダに保存済みです'
                    persist()
            return record
    finally: novelai.GEN_LOCK.release()


def finish_existing(project_id, panel_id, request_id, expected_revision, denoise=.3):
    """Explicit postprocessing of an adopted Qwen panel; never submits Qwen.

    Automatic generation settings stay unchanged. .request.json describes the
    source artwork inputs (used for staleness); .finish-request.json and the
    record describe this separate finishing operation and immutable ancestry.
    """
    db.identifier(request_id)
    if type(denoise) not in (int, float) or not .25 <= denoise <= .35:
        raise db.StudioError('denoiseは0.25〜0.35です')
    if not novelai.GEN_LOCK.acquire(blocking=False): raise db.StudioError('別の画像を生成中です')
    try:
        with db.exclusive(novelai.ledger_path().with_name('novelai.lock')):
            state = ledger()
            if request_id in state['requests']:
                old = state['requests'][request_id]
                if any(old.get(k) != v for k,v in dict(project_id=project_id, panel_id=panel_id,
                        operation='finish_existing', denoise=denoise).items()):
                    raise db.StudioError('同じ要求IDを別の対象へ使えません')
                return old
            if unresolved() or any(r['status'] in ('sending','unknown') for r in novelai.ledger()['requests'].values()):
                raise db.StudioError('未確認の生成があります。自動再送せず履歴を確認してください')
            p = db.load(project_id)
            if p['output_method'] != 'qwen': raise db.StudioError('Qwen作品ではありません')
            if p['revision'] != expected_revision: raise db.StudioError('編集競合: 作品を読み直してください')
            panel = db.panel_by_id(p, panel_id)
            if is_stale(p, panel): raise db.StudioError('採用画像の入力が古いため仕上げできません')
            source = db.asset_path(project_id, panel['image'])
            original = db.read_json(source.with_suffix('.record.json'))
            if (original.get('status') != 'complete' or original.get('provider') != 'qwen'
                    or original.get('project_id') != project_id or original.get('panel_id') != panel_id):
                raise db.StudioError('このコマの完了済みQwen画像だけを仕上げられます')
            request = db.read_json(source.with_suffix('.request.json'))
            if original.get('operation') == 'finish_existing' or request['finish']['enabled']:
                raise db.StudioError('仕上げ済み画像への重ね掛けは行いません。元画像を採用してください')
            png = source.read_bytes()
            finish_request = copy.deepcopy(request)
            finish_request['finish'].update(enabled=True, denoise=denoise)
            prefix = f'MangaAgentsStudioS/{project_id}/{request_id}-anime'
            preflight(finish_graph(finish_request,'pending.png',prefix))
            image_name = upload(png,project_id+'-'+request_id+'-source.png')
            g = finish_graph(finish_request,image_name,prefix)
            file = source.parent / (request_id+'.png')
            record = dict(id=request_id, provider='qwen', project_id=project_id, panel_id=panel_id,
                operation='finish_existing', denoise=denoise, status='sending', revision=p['revision'],
                started_at=db.now(), seed=request['parameters']['seed'], actual_anlas=0,
                image=file.relative_to(db.directory(project_id)).as_posix(),
                source_image=panel['image'], source_request=original['id'],
                source_sha256=hashlib.sha256(png).hexdigest())
            db.dump(file.with_suffix('.request.json'), request)
            db.dump(file.with_suffix('.finish-request.json'), finish_request)
            db.dump(file.with_suffix('.finish-workflow.json'), g)
            state['requests'][request_id] = record
            def persist():
                db.dump(ledger_path(),state); db.dump(file.with_suffix('.record.json'),record)
            persist()
            started = time.monotonic()
            try:
                result = run_stage(g,record,'anima',persist)
                with Image.open(io.BytesIO(result)) as im, Image.open(io.BytesIO(png)) as before:
                    if im.size != before.size: raise db.StudioError('仕上げ後の画像寸法が変わりました')
                file.write_bytes(result)
                record.update(status='complete',finished_at=db.now(),elapsed_seconds=round(time.monotonic()-started,3));persist()
            except Exception as exc:
                record.update(status='failed' if isinstance(exc,(Rejected,ExecutionFailed)) else 'unknown',
                              error=str(exc)[:300],finished_at=db.now());persist();raise
            with db.LOCK:
                current=db.load(project_id)
                def apply(project):
                    target=db.panel_by_id(project,panel_id)
                    target.setdefault('candidates',[]).append(dict(id=request_id,image=record['image'],
                        seed=record['seed'],revision=record['revision'],provider='qwen',
                        operation='finish_existing',source_request=original['id']))
                    if current['revision']==p['revision']:
                        target.update(image=record['image'],adopted_request=request_id)
                try: db.mutate(project_id,current['revision'],apply)
                except db.StudioError:
                    record['adoption_note']='保存中に作品が変更されました。仕上げ画像は履歴フォルダに保存済みです';persist()
            return record
    finally: novelai.GEN_LOCK.release()
