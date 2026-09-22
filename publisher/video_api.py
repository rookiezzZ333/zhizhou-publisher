"""HTTP endpoints for videos, isolated from the article API."""
import json
import mimetypes
import re
import shutil
import uuid
from pathlib import Path
from urllib.parse import quote,unquote,parse_qs
from .content import inside,check_image
from .video import load_video,create_video,read_config,write_config,save_copy,validate_video,snapshot_video,adapter_bundle,MAX_VIDEO
from .wechat import PlatformError

def folder_for(app,id):
    if not re.fullmatch(r'[\w\-\u4e00-\u9fff]{1,120}',id):raise ValueError('视频编号无效')
    root=app.VIDEOS.resolve();folder=(root/id).resolve()
    if not folder.is_relative_to(root) or not folder.is_dir() or (root/id).is_symlink():raise ValueError('视频不存在')
    return folder

def public(bundle):
    b={k:v for k,v in bundle.items() if k!='folder'}
    b['id']=b['name']
    url=lambda ref:'/api/videos/'+quote(b['id'])+'/media?path='+quote(ref,safe='')
    b['video_url']=url(b['video']) if b['video'] else ''
    b['cover_url']=url(b['cover']) if b['cover'] else ''
    b['checks']={}
    for p in ('douyin_video','bilibili'):
        copy=b['copies'][p];errors=[]
        if not b['video']:errors.append('请先选择 MP4 视频')
        if not copy['title'] or len(copy['title'])>(30 if p=='douyin_video' else 80):errors.append('请填写'+('30' if p=='douyin_video' else '80')+'字以内的标题')
        if p=='bilibili' and not b['bilibili_tags']:errors.append('请至少填写一个 B 站标签')
        b['checks'][p]=errors
    return b

def get(handler,app,url):
    path=url.path
    if path=='/api/videos':
        app.VIDEOS.mkdir(exist_ok=True)
        result=[]
        for folder in app.VIDEOS.iterdir():
            if not folder.is_dir() or folder.is_symlink() or folder.name.startswith('.'):continue
            try:result.append(public(load_video(folder)))
            except (ValueError,OSError):result.append({'id':folder.name,'title':folder.name,'error':'视频文件或配置有误','kind':'video'})
        handler.send(result);return
    if path=='/api/videos/bilibili-status':handler.send(app.BILI_LOGIN_STATE);return
    parts=path.strip('/').split('/')
    folder=folder_for(app,unquote(parts[2]))
    bundle=load_video(folder)
    if len(parts)==3:handler.send(public(bundle));return
    if len(parts)!=4 or parts[3]!='media':raise ValueError('视频地址无效')
    ref=parse_qs(url.query).get('path',[''])[0]
    if ref not in bundle['assets']:raise ValueError('文件不属于此视频')
    file=inside(folder,ref)
    if file.suffix.lower()!='.mp4':
        handler.send(file.read_bytes(),kind=mimetypes.guess_type(file.name)[0] or 'image/jpeg');return
    size=file.stat().st_size;start=0;end=size-1;status=200
    requested=handler.headers.get('Range')
    if requested:
        match=re.fullmatch(r'bytes=(\d*)-(\d*)',requested)
        if not match or not any(match.groups()):handler.send('',416,headers={'Content-Range':f'bytes */{size}'});return
        if match[1]:
            start=int(match[1]);end=min(int(match[2]) if match[2] else end,end)
        else:start=max(0,size-int(match[2]))
        if start>end:handler.send('',416,headers={'Content-Range':f'bytes */{size}'});return
        status=206
    handler.send_response(status)
    for k,v in {'Content-Type':'video/mp4','Content-Length':str(end-start+1),'Accept-Ranges':'bytes','Cache-Control':'no-store','X-Content-Type-Options':'nosniff',**({'Content-Range':f'bytes {start}-{end}/{size}'} if status==206 else {})}.items():handler.send_header(k,v)
    handler.end_headers()
    with file.open('rb') as stream:
        stream.seek(start);left=end-start+1
        while left:
            chunk=stream.read(min(left,1024*1024))
            if not chunk:break
            handler.wfile.write(chunk);left-=len(chunk)

def ensure_version(bundle,version):
    if bundle['fingerprint']!=version:raise ValueError('视频内容已变化，请刷新后重试')

def upload(handler,app,length):
    parts=handler.path.split('/')
    folder=folder_for(app,unquote(parts[3]));cover=parts[-1]=='cover'
    if length>(12*1024*1024 if cover else MAX_VIDEO):raise ValueError('文件超过大小限制')
    with app.IMPORT_LOCK:
        bundle=load_video(folder);ensure_version(bundle,handler.headers.get('X-Content-Version'))
        directory=(folder/'media').resolve()
        if not directory.is_relative_to(folder.resolve()):raise ValueError('素材目录无效')
        directory.mkdir(exist_ok=True)
        ext='.png' if handler.headers.get('Content-Type')=='image/png' else '.webp' if handler.headers.get('Content-Type')=='image/webp' else '.jpg' if cover else '.mp4'
        ref='media/'+uuid.uuid4().hex+ext;file=folder/ref
        try:
            with file.open('wb') as stream:
                left=length
                while left:
                    chunk=handler.rfile.read(min(left,1024*1024))
                    if not chunk:raise ValueError('文件未上传完整')
                    stream.write(chunk);left-=len(chunk)
            (check_image if cover else validate_video)(file)
            meta=read_config(folder);meta['cover' if cover else 'file']=ref;write_config(folder,meta)
        except Exception:
            file.unlink(missing_ok=True);raise
        handler.send(public(load_video(folder)))

def post(handler,app,data):
    path=handler.path
    if path=='/api/videos/create':
        with app.IMPORT_LOCK:handler.send(public(create_video(app.VIDEOS,data.get('title'))))
        return
    if path=='/api/videos/bilibili-login':
        app.BILI_POOL.submit(app.bili_login);handler.send({'message':'正在打开 B 站专用窗口'});return
    folder=folder_for(app,str(data.get('video_id','')))
    with app.IMPORT_LOCK:
        bundle=load_video(folder);ensure_version(bundle,data.get('fingerprint'))
        if path=='/api/videos/text':
            handler.send(public(save_copy(folder,data.get('platform'),data.get('title'),data.get('body'),data)));return
        if path=='/api/videos/rename':
            title=str(data.get('title','')).strip()
            if not title or len(title)>120:raise ValueError('请填写 120 字以内的名称')
            meta=read_config(folder);meta['title']=title;write_config(folder,meta)
            handler.send(public(load_video(folder)));return
        if path=='/api/videos/delete':
            if data.get('confirmed') is not True:raise ValueError('请确认删除视频')
            with app.STORE.db() as db:
                if db.execute("SELECT 1 FROM jobs WHERE article=? AND status IN ('running','submitting')",(folder.name,)).fetchone():raise ValueError('视频正在处理中')
            trash=(app.STATE/'trash/videos').resolve();trash.mkdir(parents=True,exist_ok=True)
            target=(trash/(folder.name+'-'+uuid.uuid4().hex)).resolve()
            if not target.is_relative_to(app.STATE.resolve()) or not folder.resolve().is_relative_to(app.VIDEOS.resolve()):raise ValueError('回收目录无效')
            folder.rename(target);handler.send({'ok':True});return
        if path!='/api/videos/prepare':raise ValueError('视频操作不存在')
        platform=data.get('platform')
        if platform not in ('douyin_video','bilibili'):raise ValueError('视频平台无效')
        errors=public(bundle)['checks'][platform]
        if errors:raise ValueError('；'.join(errors))
        job,created=app.STORE.begin(bundle,platform,'prepare','local-bilibili-profile' if platform=='bilibili' else 'local-douyin-profile')
        resumed=False
        if not created:resumed=app.STORE.resume_editor(job['id'])
        if created or resumed:
            try:
                if created:frozen=snapshot_video(bundle,app.STATE,job['id'])
                else:
                    snap=app.STATE/'snapshots'/job['id']
                    if (snap/'video.yaml').exists():frozen=load_video(snap);frozen['name']=bundle['name']
                    else:
                        # Legacy snapshot remains immutable; a new independent snapshot stores the migration.
                        migrated_id=job['id']+'-video'
                        snap=app.STATE/'snapshots'/migrated_id
                        frozen=load_video(snap) if snap.exists() else snapshot_video(bundle,app.STATE,migrated_id)
                        frozen['name']=bundle['name']
                (app.BILI_POOL if platform=='bilibili' else app.DY_POOL).submit(app.run_job,adapter_bundle(frozen,platform),platform,'prepare',job,{})
            except Exception:
                app.STORE.update(job['id'],'failed','无法准备视频快照，请刷新检查素材');raise
        handler.send({'job':job,'created':created,'resumed':resumed},202)
