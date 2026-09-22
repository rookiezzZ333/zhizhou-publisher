"""Independent video library; no article.md required."""
import hashlib
import json
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
import yaml
from .content import inside,check_image,load_bundle

MAX_VIDEO=500*1024*1024
PLATFORMS=('douyin_video','bilibili')
COPY_FILES={'douyin_video':'douyin.md','bilibili':'bilibili.md'}

def read_config(folder):
    path=inside(folder,'video.yaml')
    if path.is_symlink() or path.stat().st_size>100_000:raise ValueError('视频配置无效')
    data=yaml.safe_load(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data,dict):raise ValueError('video.yaml 必须是配置对象')
    return data

def write_config(folder,data):
    path=folder/'video.yaml'
    if path.is_symlink():raise ValueError('视频配置路径无效')
    temp=folder/'video.yaml.tmp'
    temp.write_text(yaml.safe_dump(data,allow_unicode=True,sort_keys=False),encoding='utf-8')
    temp.replace(path)

def validate_video(file):
    if file.suffix.lower()!='.mp4' or not 12<=file.stat().st_size<=MAX_VIDEO:raise ValueError('请选择 500 MB 以内的 MP4 视频')
    with file.open('rb') as stream:
        if stream.read(12)[4:8]!=b'ftyp':raise ValueError('文件不是 MP4 容器')

def read_copy(folder,platform,title):
    path=folder/COPY_FILES[platform]
    if not path.exists():return {'title':title,'body':''}
    path=inside(folder,COPY_FILES[platform])
    if path.stat().st_size>100_000:raise ValueError('视频文案过大')
    raw=path.read_text(encoding='utf-8-sig').strip()
    match=re.match(r'^# ([^\n]+)\n?',raw)
    return {'title':match[1].strip() if match else title,'body':raw[match.end():].strip() if match else raw}

def load_video(folder):
    folder=Path(folder).resolve();meta=read_config(folder)
    title=str(meta.get('title') or folder.name)
    video=str(meta.get('file') or '');cover=str(meta.get('cover') or '')
    if video:validate_video(inside(folder,video))
    if cover:check_image(inside(folder,cover))
    tags=meta.get('bilibili_tags',[])
    if not isinstance(tags,list) or not all(isinstance(x,str) for x in tags):raise ValueError('B 站标签应为文字列表')
    if len(tags)>10 or any(not x.strip() or len(x)>20 for x in tags):raise ValueError('B 站标签请填写 1–20 字，最多 10 个')
    result=dict(kind='video',name=folder.name,folder=str(folder),title=title,video=video,cover=cover,
        created_at=meta.get('created_at',getattr(folder.stat(),'st_birthtime',folder.stat().st_ctime)),
        copies={p:read_copy(folder,p,title) for p in PLATFORMS},
        bilibili_tags=tags,bilibili_category=str(meta.get('bilibili_category') or ''),
        bilibili_copyright=str(meta.get('bilibili_copyright') or 'unset'),
        bilibili_source=str(meta.get('bilibili_source') or ''),
        origin_article=meta.get('origin_article',''),assets=[p for p in (video,cover) if p])
    if result['bilibili_copyright'] not in ('unset','original','repost'):raise ValueError('创作类型无效')
    digest=hashlib.sha256()
    digest.update(json.dumps({k:v for k,v in result.items() if k not in ('folder','name','created_at','origin_article')},ensure_ascii=False,sort_keys=True).encode())
    for ref in result['assets']:
        with inside(folder,ref).open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
    result['fingerprint']=digest.hexdigest()
    result['size']=inside(folder,video).stat().st_size if video else 0
    result['updated_at']=max(p.stat().st_mtime for p in [folder/'video.yaml']+[folder/n for n in COPY_FILES.values() if (folder/n).exists()])
    return result

def adapter_bundle(bundle,platform):
    result=dict(bundle)
    copy=bundle['copies'][platform]
    result.update(douyin_video=bundle['video'],douyin_video_title=copy['title'],douyin_video_body=copy['body'])
    return result

def save_copy(folder,platform,title,body,extra=None):
    if platform not in PLATFORMS:raise ValueError('视频平台无效')
    title=str(title).strip();body=str(body).strip()
    if not title or '\n' in title or len(title)>(30 if platform=='douyin_video' else 80):raise ValueError('标题为空或超过本平台长度限制')
    if len(body)>(4000 if platform=='douyin_video' else 2000):raise ValueError('视频简介超过本工具长度限制')
    file=folder/COPY_FILES[platform]
    if file.is_symlink():raise ValueError('文案路径无效')
    if platform=='bilibili':
        data=read_config(folder);extra=extra or {}
        tags=extra.get('tags',[])
        if not isinstance(tags,list) or len(tags)>10 or any(not isinstance(t,str) or not t.strip() or len(t)>20 for t in tags):raise ValueError('请填写最多 10 个、每个 1–20 字的标签')
        copyright=extra.get('copyright','unset')
        if copyright not in ('unset','original','repost'):raise ValueError('创作类型无效')
        source=str(extra.get('source','')).strip()
        if copyright=='repost' and not source:raise ValueError('转载视频请填写来源')
        data.update(bilibili_tags=list(dict.fromkeys(t.strip() for t in tags)),bilibili_category=str(extra.get('category',''))[:100],bilibili_copyright=copyright,bilibili_source=source[:1000])
        write_config(folder,data)
    temp=file.with_suffix('.md.tmp');temp.write_text('# '+title+'\n\n'+body+'\n',encoding='utf-8');temp.replace(file)
    return load_video(folder)

def create_video(root,title):
    root.mkdir(exist_ok=True,parents=True)
    folder=root/('video_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]);folder.mkdir()
    write_config(folder,{'title':str(title or '未命名视频')[:120],'file':'','cover':'','created_at':datetime.now().timestamp()})
    return load_video(folder)

def snapshot_video(bundle,state,jobid):
    target=state/'snapshots'/jobid;target.mkdir(parents=True)
    source=Path(bundle['folder'])
    for name in ['video.yaml']+list(COPY_FILES.values())+bundle['assets']:
        if not (source/name).exists():continue
        src=inside(source,name);dst=target/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
    copy=load_video(target)
    if copy['fingerprint']!=bundle['fingerprint']:raise ValueError('视频在准备过程中发生变化，请刷新后重试')
    copy['name']=bundle['name'];return copy

def migrate_legacy(articles,videos,state,store=None,video_only=()):
    """Copy and verify first; preserve original sources and historical job evidence."""
    videos.mkdir(parents=True,exist_ok=True)
    report=[]
    for folder in articles.iterdir():
        if not folder.is_dir() or folder.is_symlink():continue
        try:old=load_bundle(folder)
        except (ValueError,OSError):continue
        if not old['douyin_video']:continue
        import yaml
        raw=(folder/'article.md').read_text(encoding='utf-8-sig')
        front=re.match(r'^---\r?\n(.*?)\r?\n---',raw,re.S)
        legacy_meta=(yaml.safe_load(front[1]) or {}) if front else {}
        # Deleting a migrated video must not resurrect it on the next start.
        if legacy_meta.get('migrated_video'):continue
        name='video_'+folder.name.removeprefix('article_')
        target=videos/name
        if target.exists():
            try:
                if read_config(target).get('origin_article')!=folder.name:raise ValueError('迁移目标名称冲突')
            except Exception:raise ValueError('视频迁移目标已存在，请检查 '+name)
            from .metadata import update_metadata
            update_metadata(folder,{'migrated_video':name,**({'library_hidden':True} if folder.name in video_only else {})})
            continue
        stage=videos/('.migrate-'+uuid.uuid4().hex);stage.mkdir()
        (stage/'media').mkdir()
        src=inside(folder,old['douyin_video']);shutil.copy2(src,stage/'media/video.mp4')
        cover=''
        if old['cover']:
            cover='cover'+Path(old['cover']).suffix.lower();shutil.copy2(inside(folder,old['cover']),stage/cover)
        write_config(stage,{'title':old['title'],'file':'media/video.mp4','cover':cover,
            'created_at':getattr(folder.stat(),'st_birthtime',folder.stat().st_ctime),
            'origin_article':folder.name,'legacy_fingerprint':old['fingerprint'],'bilibili_tags':old['tags'][:10]})
        for platform in PLATFORMS:
            (stage/COPY_FILES[platform]).write_text('# '+old['douyin_video_title']+'\n\n'+old['douyin_video_body'],encoding='utf-8')
        # Verify copied bytes without loading a large video into RAM.
        def hash_file(path):
            h=hashlib.sha256()
            with path.open('rb') as f:
                for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
            return h.hexdigest()
        if hash_file(src)!=hash_file(stage/'media/video.mp4'):raise ValueError('迁移视频校验失败，原文件未修改')
        new=load_video(stage)
        stage.rename(target)
        if store:
            with store.lock,store.db() as db:
                rows=db.execute("SELECT id,result,fingerprint FROM jobs WHERE article=? AND platform='douyin_video'",(folder.name,)).fetchall()
                for row in rows:
                    result=json.loads(row['result'] or '{}');result['migration']={'origin_article':folder.name,'video_id':name,'original_fingerprint':row['fingerprint']}
                    fingerprint=new['fingerprint'] if row['fingerprint']==old['fingerprint'] else row['fingerprint']
                    db.execute('UPDATE jobs SET article=?,fingerprint=?,result=? WHERE id=?',(name,fingerprint,json.dumps(result,ensure_ascii=False),row['id']))
        # Keep article content intact; hide explicitly classified video-only legacy entries.
        if folder.name in video_only:
            from .metadata import update_metadata
            backup=state/'migration-backups'/folder.name;backup.mkdir(parents=True,exist_ok=True)
            shutil.copy2(folder/'article.md',backup/'article.md')
            update_metadata(folder,{'library_hidden':True,'migrated_video':name})
        if folder.name not in video_only:
            from .metadata import update_metadata
            update_metadata(folder,{'migrated_video':name})
        report.append({'article':folder.name,'video':name})
    return report
