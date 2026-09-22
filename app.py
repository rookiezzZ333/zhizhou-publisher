import sys
import base64
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs,quote,urlsplit

from publisher.content import inside,load_bundle,render,IMAGE_TYPES,MAX_FILE
from publisher.state import Store
from publisher.douyin import Douyin
from publisher.bilibili import Bilibili
from publisher import video_api
from publisher.wechat import WeChat,PlatformError,UncertainError
from publisher.xiaohongshu import Xiaohongshu,LoginNeeded
from publisher.wechat_browser import WeChatBrowser

ROOT=Path(__file__).resolve().parent
ARTICLES=ROOT/'articles';ARTICLES.mkdir(exist_ok=True)
VIDEOS=ROOT/'videos';VIDEOS.mkdir(exist_ok=True)
STATE=ROOT/'.state';STATE.mkdir(exist_ok=True)
PORT=int(os.getenv('ZHIZHOU_PORT','8766'))
TOKEN=secrets.token_urlsafe(32)
STORE=None
WX_POOL=ThreadPoolExecutor(max_workers=1,thread_name_prefix='wechat')
XHS_POOL=ThreadPoolExecutor(max_workers=1,thread_name_prefix='xiaohongshu')
XHS=Xiaohongshu(STATE)
DY=Douyin(STATE)
BILI=Bilibili(STATE)
BILI_POOL=ThreadPoolExecutor(max_workers=1,thread_name_prefix='bilibili')
BILI_LOGIN_STATE={'status':'idle','message':''}
DY_POOL=ThreadPoolExecutor(max_workers=1,thread_name_prefix='douyin')
DY_LOGIN_STATE={'status':'idle','message':''}
WX_BROWSER=WeChatBrowser(STATE)
WX_LOGIN_STATE={'status':'idle','message':'尚未打开公众号登录窗口'}
IMPORT_LOCK=threading.Lock()
LOGIN_STATE={'status':'idle','message':'尚未打开登录窗口'}


def article_folder(id):
    if not re.fullmatch(r'[\w\-\u4e00-\u9fff]{1,120}',id):raise ValueError('文章目录名称无效')
    path=(ARTICLES/id).resolve()
    if not path.is_relative_to(ARTICLES.resolve()) or not path.is_dir():raise ValueError('文章目录不存在')
    return path


def public_bundle(bundle):
    result={k:v for k,v in bundle.items() if k not in ('folder','refs')}
    id=bundle['name'];result['id']=id
    image_url=lambda ref:'/api/articles/'+quote(id)+'/image?path='+quote(ref,safe='')
    result['html']=render(bundle,image_url)
    result['image_items']=[{'path':p,'url':image_url(p),'cover':p==bundle['cover']} for p in bundle['images']]
    result['platform_image_items']={key:[{'path':ref,'url':image_url(ref)} for ref in bundle[key+'_images']] for key in ('xhs','douyin')}
    result['video_url']=image_url(bundle['douyin_video']) if bundle['douyin_video'] else ''
    result['checks']={'wechat':[],'xiaohongshu':[],'douyin':[],'douyin_video':[]}
    if not bundle['douyin_video']:result['checks']['douyin_video'].append('请先选择一个 MP4 视频')
    if len(bundle['douyin_video_title'])>30:result['checks']['douyin_video'].append('请提供 30 字以内的视频短标题')
    if not bundle['douyin_images']:result['checks']['douyin'].append('至少需要一张抖音图片')
    if len(bundle['douyin_title'])>30:result['checks']['douyin'].append('本工具抖音标题限制 30 字，请提供独立短标题')
    if not bundle['cover']:result['checks']['wechat'].append('缺少封面图片')
    if len(bundle['title'])>64:result['checks']['wechat'].append('标题超过 64 字')
    if len(bundle['author'])>8:result['checks']['wechat'].append('作者超过 8 字')
    if len(bundle['digest'])>120:result['checks']['wechat'].append('摘要超过 120 字')
    if not bundle['xhs_images']:result['checks']['xiaohongshu'].append('至少需要 1 张图片')
    if len(bundle['xhs_images'])>18:result['checks']['xiaohongshu'].append('本版最多发送 18 张图片')
    if len(bundle['xhs_title'])>20:result['checks']['xiaohongshu'].append('请在 xhs_title 中提供 20 字以内的短标题')
    if len(bundle['xhs_body'])>1000:result['checks']['xiaohongshu'].append('请提供 1000 字以内的 xiaohongshu.md 短文；程序不会截断原文')
    return result


def allowed_import(path):
    p=Path(path)
    return (path in ('article.md','xiaohongshu.md','douyin.md','douyin-video.md') or (len(p.parts)>=2 and p.parts[0] in ('image','images','xiaohongshu','douyin','douyin-video') and p.suffix.lower() in IMAGE_TYPES) or (len(p.parts)>=2 and p.parts[0]=='douyin-video' and p.suffix.lower()=='.mp4'))


def import_bundle(payload):
    files=payload.get('files')
    if not isinstance(files,list) or not 1<=len(files)<=80:raise ValueError('每个文件夹最多导入 80 个内容文件')
    name=re.sub(r'[^\w\-\u4e00-\u9fff]','_',str(payload.get('name','article')))[:95] or 'article'
    stage=STATE/'imports'/uuid.uuid4().hex;stage.mkdir(parents=True)
    total=0;seen=set()
    for file in files:
        rel=str(file.get('path','')).replace('\\','/')
        if ':' in rel or rel.startswith('/') or '..' in Path(rel).parts or not allowed_import(rel):raise ValueError('仅导入 article.md、xiaohongshu.md 和 image(s) 内图片')
        if rel.casefold() in seen:raise ValueError('文件名重复：'+rel)
        seen.add(rel.casefold())
        raw=base64.b64decode(file.get('data',''),validate=True);total+=len(raw)
        if len(raw)>(80*1024*1024 if rel.lower().endswith('.mp4') else MAX_FILE) or total>80*1024*1024:raise ValueError('图片超过 12 MB 或文件夹超过 80 MB；大视频请导入文章后单独选择')
        target=(stage/rel).resolve()
        if not target.is_relative_to(stage.resolve()):raise ValueError('文件路径超出文章目录')
        target.parent.mkdir(exist_ok=True,parents=True);target.write_bytes(raw)
    bundle=load_bundle(stage)
    with IMPORT_LOCK:
        target=ARTICLES/name;n=2
        while target.exists():
            try:
                if load_bundle(target)['fingerprint']==bundle['fingerprint']:return public_bundle(load_bundle(target))
            except ValueError:pass
            target=ARTICLES/(name+'_'+str(n));n+=1
        # Source and destination are verified project-owned paths before directory rename.
        if not stage.resolve().is_relative_to(STATE.resolve()) or not target.resolve().is_relative_to(ARTICLES.resolve()):raise ValueError('目录位置无效')
        stage.rename(target)
    return public_bundle(load_bundle(target))


def snapshot(bundle,id):
    target=STATE/'snapshots'/id
    target.mkdir(parents=True)
    source=Path(bundle['folder'])
    for name in ['article.md','xiaohongshu.md','douyin.md','douyin-video.md']+bundle['assets']:
        if not (source/name).exists():continue
        src=inside(source,name);dst=target/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
    copy=load_bundle(target)
    if copy['fingerprint']!=bundle['fingerprint']:raise ValueError('文件在准备过程中发生变化，请重新载入文章')
    copy['name']=bundle['name'];return copy


def run_job(bundle,platform,action,job,settings):
    id=job['id']
    try:
        if platform=='wechat':WeChat(settings).run(bundle,action,STORE,job)
        elif platform=='wechat_browser':WX_BROWSER.run(bundle,action,STORE,job)
        elif platform=='bilibili':BILI.run(bundle,STORE,job,action=action)
        elif platform in ('douyin','douyin_video'):DY.run(bundle,STORE,job,action=action,video=platform=='douyin_video')
        else:XHS.run(bundle,STORE,job,action=action)
    except LoginNeeded as e:STORE.update(id,'needs_login',str(e),screenshot=(WX_BROWSER if platform=='wechat_browser' else BILI if platform=='bilibili' else DY if platform in ('douyin','douyin_video') else XHS).screenshot(id))
    except UncertainError as e:STORE.update(id,'uncertain',str(e),screenshot=(WX_BROWSER if platform=='wechat_browser' else BILI if platform=='bilibili' else DY if platform in ('douyin','douyin_video') else XHS).screenshot(id) if platform!='wechat' else None)
    except (PlatformError,ValueError) as e:
        previous=STORE.get(id)
        if previous['result'].get('publish_id'):
            STORE.update(id,'submitted','平台已受理，但本次查询失败；请点击查询结果，不要重发。')
        else:STORE.update(id,'failed',str(e),screenshot=(WX_BROWSER if platform=='wechat_browser' else BILI if platform=='bilibili' else DY if platform in ('douyin','douyin_video') else XHS).screenshot(id) if platform!='wechat' else None)
    except Exception as exc:
        old=STORE.get(id)
        state='uncertain' if old['status'] in ('submitting','submitted') else 'failed'
        message=WX_BROWSER.interruption_message(exc) if platform=='wechat_browser' else '操作中断。请查看专用浏览器现场，可能是图片上传超时或页面变化；已有内容不会自动重填。'
        STORE.update(id,state,message,screenshot=(WX_BROWSER if platform=='wechat_browser' else BILI if platform=='bilibili' else DY if platform in ('douyin','douyin_video') else XHS).screenshot(id) if platform!='wechat' else None)


def login():
    global LOGIN_STATE
    LOGIN_STATE={'status':'opening','message':'正在打开专用 Edge 浏览器'}
    try:LOGIN_STATE={'status':'ready',**XHS.login()}
    except Exception:LOGIN_STATE={'status':'failed','message':'专用浏览器未能打开，请确认已安装 Edge，或关闭之前的专用窗口后重试。'}


def dy_login():
    global DY_LOGIN_STATE
    DY_LOGIN_STATE={'status':'opening','message':'正在打开抖音'}
    try:DY_LOGIN_STATE={'status':'ready',**DY.login()}
    except Exception:DY_LOGIN_STATE={'status':'failed','message':'抖音窗口打开失败，请检查专用浏览器。'}


def bili_login():
    global BILI_LOGIN_STATE
    BILI_LOGIN_STATE={'status':'opening','message':'正在打开 B 站'}
    try:BILI_LOGIN_STATE={'status':'ready',**BILI.login()}
    except Exception:BILI_LOGIN_STATE={'status':'failed','message':'B 站窗口打开失败，请确认已安装 Edge 并关闭过期专用窗口后重试。'}


def wx_login():
    global WX_LOGIN_STATE
    WX_LOGIN_STATE={'status':'opening','message':'正在打开公众号专用 Edge'}
    try:WX_LOGIN_STATE={'status':'ready',**WX_BROWSER.login()}
    except PlatformError as e:WX_LOGIN_STATE={'status':'failed','message':str(e)}
    except Exception as e:WX_LOGIN_STATE={'status':'failed','message':'公众号页面打开失败（'+type(e).__name__+'）。如果 Edge 已打开，请先在其中登录，再重试。'}


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def send(self,data,status=200,kind='application/json; charset=utf-8',headers=None):
        if isinstance(data,(dict,list)):data=json.dumps(data,ensure_ascii=False).encode()
        if isinstance(data,str):data=data.encode()
        self.send_response(status)
        for k,v in {'Content-Type':kind,'Content-Length':str(len(data)),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','X-Frame-Options':'DENY',**(headers or {})}.items():self.send_header(k,v)
        self.end_headers();self.wfile.write(data)
    def host_ok(self):return self.headers.get('Host') in (f'127.0.0.1:{PORT}',f'localhost:{PORT}')
    def authorized(self):return hmac.compare_digest(self.headers.get('X-Studio-Token',''),TOKEN)
    def image_auth(self):return f'zhizhou={TOKEN}' in self.headers.get('Cookie','').split('; ')
    def do_GET(self):
        if not self.host_ok():return self.send({'error':'无效本机地址'},403)
        url=urlsplit(self.path);path=url.path
        try:
            if path=='/':
                return self.send((ROOT/'web'/'index.html').read_text(encoding='utf-8').replace('__TOKEN__',TOKEN),kind='text/html; charset=utf-8',headers={'Set-Cookie':f'zhizhou={TOKEN}; HttpOnly; SameSite=Strict; Path=/','Content-Security-Policy':"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
            if path in ('/static/app.js','/static/style.css'):
                file=ROOT/'web'/Path(path).name
                return self.send(file.read_bytes(),kind='text/javascript; charset=utf-8' if path.endswith('.js') else 'text/css; charset=utf-8')
            if path=='/api/videos' or path.startswith('/api/videos/'):
                media=bool(re.fullmatch(r'/api/videos/[^/]+/media',path))
                if not (self.image_auth() if media else self.authorized()):return self.send({'error':'请刷新工作台'},403)
                return video_api.get(self,sys.modules[__name__],url)
            if re.fullmatch(r'/api/articles/[^/]+/image',path):
                if not self.image_auth():return self.send({'error':'请先打开工作台'},403)
                from urllib.parse import unquote
                folder=article_folder(unquote(path.split('/')[3]));ref=parse_qs(url.query).get('path',[''])[0]
                bundle=load_bundle(folder)
                if ref not in bundle['assets']:raise ValueError('图片不属于此文章')
                image=inside(folder,ref)
                if image.suffix.lower()=='.mp4':
                    size=image.stat().st_size;start=0;end=size-1;status=200
                    requested=self.headers.get('Range')
                    if requested:
                        match=re.fullmatch(r'bytes=(\d+)-(\d*)',requested)
                        if not match:return self.send('',416,headers={'Content-Range':f'bytes */{size}'})
                        start=int(match[1]);end=min(int(match[2]) if match[2] else end,end);status=206
                        if start>end:return self.send('',416,headers={'Content-Range':f'bytes */{size}'})
                    self.send_response(status)
                    self.send_header('Content-Type','video/mp4');self.send_header('Accept-Ranges','bytes')
                    self.send_header('Content-Length',str(end-start+1));self.send_header('Cache-Control','no-store')
                    if status==206:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
                    self.end_headers()
                    with image.open('rb') as stream:
                        stream.seek(start);remaining=end-start+1
                        while remaining:
                            chunk=stream.read(min(1024*1024,remaining))
                            if not chunk:break
                            self.wfile.write(chunk);remaining-=len(chunk)
                    return
                return self.send(image.read_bytes(),kind=mimetypes.guess_type(image.name)[0] or 'image/png')
            if re.fullmatch(r'/api/screenshots/[a-f0-9]{32}\.png',path):
                if not self.image_auth():return self.send({'error':'请先打开工作台'},403)
                file=STATE/'screenshots'/Path(path).name
                if not file.is_file():return self.send({'error':'暂无截图'},404)
                return self.send(file.read_bytes(),kind='image/png')
            if not self.authorized():return self.send({'error':'请刷新工作台后重试'},403)
            if path=='/api/articles':
                result=[]
                for folder in sorted(ARTICLES.iterdir(),reverse=True):
                    if folder.is_dir() and not folder.is_symlink():
                        try:
                            b=load_bundle(folder)
                            if b.get('library_hidden'):continue
                            result.append({'id':folder.name,'title':b['title'],'images':len(b['images']),'cover_url':('/api/articles/'+quote(folder.name)+'/image?path='+quote(b['cover'],safe='')) if b['cover'] else '','fingerprint':b['fingerprint'],'created_at':getattr(folder.stat(),'st_birthtime',folder.stat().st_ctime),'updated_at':(folder/'article.md').stat().st_mtime})
                        except Exception:result.append({'id':folder.name,'title':folder.name,'error':'内容格式有误，请打开查看'})
                return self.send(result)
            if path.startswith('/api/articles/'):
                from urllib.parse import unquote
                return self.send(public_bundle(load_bundle(article_folder(unquote(path[len('/api/articles/'):])))))
            if path=='/api/settings':return self.send(STORE.settings())
            if path=='/api/jobs':return self.send(STORE.list_jobs())
            if path=='/api/douyin/login-status':return self.send(DY_LOGIN_STATE)
            if path=='/api/login-status':return self.send(LOGIN_STATE)
            if path=='/api/wechat/browser-status':return self.send(WX_LOGIN_STATE)
            return self.send({'error':'不存在'},404)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):return
        except (ValueError,FileNotFoundError) as e:return self.send({'error':str(e)},400)
        except Exception:return self.send({'error':'读取失败，请检查文件格式'},500)
    def do_POST(self):
        if not self.host_ok() or not self.authorized():return self.send({'error':'请刷新页面后重试'},403)
        origin=self.headers.get('Origin')
        if origin and origin not in (f'http://127.0.0.1:{PORT}',f'http://localhost:{PORT}'):return self.send({'error':'来源无效'},403)
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=(500 if self.path.startswith('/api/video-upload/') or re.fullmatch(r'/api/videos/[^/]+/(upload|cover)',self.path) else 112)*1024*1024:raise ValueError('请求过大或为空')
            if re.fullmatch(r'/api/videos/[^/]+/(upload|cover)',self.path):
                return video_api.upload(self,sys.modules[__name__],length)
            if self.path.startswith('/api/video-upload/'):
                from publisher.metadata import update_metadata
                from urllib.parse import unquote
                folder=article_folder(unquote(self.path.split('/')[-1]))
                if length>500*1024*1024:raise ValueError('视频超过本工具 500 MB 限制')
                with IMPORT_LOCK:
                    bundle=load_bundle(folder)
                    if bundle['fingerprint']!=self.headers.get('X-Content-Version'):raise ValueError('文章已更新，请刷新重试')
                    target=(folder/'douyin-video').resolve()
                    if not target.is_relative_to(folder.resolve()):raise ValueError('视频目录无效')
                    target.mkdir(exist_ok=True)
                    ref='douyin-video/'+uuid.uuid4().hex+'.mp4';file=folder/ref
                    remaining=length
                    try:
                        with file.open('wb') as stream:
                            while remaining:
                                chunk=self.rfile.read(min(1024*1024,remaining))
                                if not chunk:raise ValueError('视频上传未完成')
                                stream.write(chunk);remaining-=len(chunk)
                        with file.open('rb') as stream:
                            if stream.read(12)[4:8]!=b'ftyp':raise ValueError('请选择 MP4 视频文件')
                    except Exception:
                        file.unlink(missing_ok=True)
                        raise
                    update_metadata(folder,{'douyin_video':ref})
                    return self.send(public_bundle(load_bundle(folder)))
            data=json.loads(self.rfile.read(length))
            if not isinstance(data,dict):raise ValueError('请求格式无效')
            if self.path.startswith('/api/videos/'):return video_api.post(self,sys.modules[__name__],data)
            if self.path=='/api/import':return self.send(import_bundle(data))
            if self.path=='/api/jobs/delete':
                if data.get('confirmed') is not True:raise ValueError('请确认删除记录')
                return self.send(STORE.hide_job(str(data.get('id',''))))
            if self.path in ('/api/articles/delete','/api/articles/remove-image'):
                if data.get('confirmed') is not True:raise ValueError('请确认操作')
                with IMPORT_LOCK:
                    folder=article_folder(str(data.get('article_id','')))
                    bundle=load_bundle(folder)
                    if bundle['fingerprint']!=data.get('fingerprint'):raise ValueError('文章已更新，请刷新后重试')
                    with STORE.db() as db:
                        if db.execute("SELECT 1 FROM jobs WHERE article=? AND status IN ('running','submitting')",(folder.name,)).fetchone():raise ValueError('文章正在处理中，请稍后再试')
                    if self.path.endswith('/delete'):
                        trash=(STATE/'trash').resolve();trash.mkdir(parents=True,exist_ok=True)
                        target=(trash/(folder.name+'-'+uuid.uuid4().hex)).resolve()
                        if not folder.resolve().is_relative_to(ARTICLES.resolve()) or not target.is_relative_to(STATE.resolve()):raise ValueError('目录位置无效')
                        folder.rename(target)
                        return self.send({'ok':True})
                    ref=str(data.get('path',''))
                    selected=data.get('platform','wechat')
                    image_key={'xiaohongshu':'xhs_images','douyin':'douyin_images'}.get(selected,'images')
                    if ref not in bundle[image_key]:raise ValueError('图片不属于本平台文章')
                    import yaml
                    path=folder/'article.md';raw=path.read_text(encoding='utf-8-sig')
                    match=re.match(r'^---\r?\n(.*?)\r?\n---\r?\n(.*)$',raw,re.S)
                    meta=yaml.safe_load(match[1]) or {} if match else {}
                    body=match[2] if match else raw
                    if image_key!='images':
                        meta[image_key]=[p for p in bundle[image_key] if p!=ref]
                        if image_key=='xhs_images':meta['xhs_card_images' if bundle['xhs_image_mode']=='cards' else 'xhs_original_images']=meta[image_key]
                    else:
                        meta['excluded_images']=list(dict.fromkeys(bundle.get('excluded_images',[])+[ref]))
                        if bundle['cover']==ref:meta['cover']=next((p for p in bundle['images'] if p!=ref),'')
                    changed='---\n'+yaml.safe_dump(meta,allow_unicode=True,sort_keys=False)+'---\n'+body
                    temporary=folder/'article.md.tmp';temporary.write_text(changed,encoding='utf-8');temporary.replace(path)
                    return self.send(public_bundle(load_bundle(folder)))
            if self.path=='/api/douyin/login':
                DY_POOL.submit(dy_login);return self.send({'message':'正在打开抖音专用窗口'})
            if self.path=='/api/articles/video-text':
                with IMPORT_LOCK:
                    folder=article_folder(str(data.get('article_id','')))
                    if load_bundle(folder)['fingerprint']!=data.get('fingerprint'):raise ValueError('文章已更新，请刷新重试')
                    title=str(data.get('title','')).strip();body=str(data.get('body','')).strip()
                    if not title or len(title)>30 or '\n' in title or not body or len(body)>4000:raise ValueError('请填写 30 字内标题及 4000 字内正文')
                    file=folder/'douyin-video.md'
                    if file.is_symlink():raise ValueError('文案路径无效')
                    temp=folder/'douyin-video.md.tmp';temp.write_text('# '+title+'\n\n'+body,encoding='utf-8');temp.replace(file)
                    return self.send(public_bundle(load_bundle(folder)))
            if self.path=='/api/articles/xhs-mode':
                from publisher.metadata import switch_cards
                with IMPORT_LOCK:
                    folder=article_folder(str(data.get('article_id','')))
                    if load_bundle(folder)['fingerprint']!=data.get('fingerprint'):raise ValueError('文章已更新，请刷新重试')
                    return self.send(public_bundle(switch_cards(folder,data.get('mode'))))
            if self.path=='/api/articles/cards':
                from publisher.cards import generate_cards
                with IMPORT_LOCK:
                    folder=article_folder(str(data.get('article_id','')))
                    if load_bundle(folder)['fingerprint']!=data.get('fingerprint'):raise ValueError('文章已改变，请刷新重试')
                    return self.send(public_bundle(generate_cards(folder)))
            if self.path=='/api/settings':return self.send(STORE.save_settings(data))
            if self.path=='/api/wechat/check':return self.send(WeChat(STORE.settings(private=True)).check())
            if self.path=='/api/wechat/browser-login':
                if WX_LOGIN_STATE['status']=='opening':return self.send(WX_LOGIN_STATE)
                WX_POOL.submit(wx_login);return self.send({'message':'正在打开公众号专用 Edge，请扫码登录并核对账号。'})
            if self.path=='/api/xiaohongshu/login':
                if LOGIN_STATE['status']=='opening':return self.send(LOGIN_STATE)
                XHS_POOL.submit(login);return self.send({'message':'正在打开小红书登录窗口，请自行扫码。'})
            if self.path=='/api/publish':
                platform=data.get('platform');action=data.get('action')
                allowed={'wechat':('draft','publish'),'wechat_browser':('draft','prepare'),'xiaohongshu':('publish','prepare'),'douyin':('prepare',),'douyin_video':('prepare',)}
                if platform not in allowed or action not in allowed[platform]:raise ValueError('发布目标无效')
                bundle=load_bundle(article_folder(str(data.get('article_id',''))))
                if bundle['fingerprint']!=data.get('fingerprint'):raise ValueError('文案或图片已更新，请重新载入预览再发送')
                errors=public_bundle(bundle)['checks']['wechat' if platform=='wechat_browser' else platform]
                if errors:raise ValueError('；'.join(errors))
                settings=STORE.settings(private=True) if platform=='wechat' else {}
                if platform=='wechat' and not settings.get('secret'):raise ValueError('请先在账号设置中填写 AppSecret 并测试连接')
                account=settings['appid'] if platform=='wechat' else 'local-wechat-profile' if platform=='wechat_browser' else 'local-douyin-profile' if platform in ('douyin','douyin_video') else 'local-xhs-profile'
                job,created=STORE.begin(bundle,platform,action,account)
                resumed=False
                if not created and platform in ('douyin','douyin_video','xiaohongshu','wechat_browser') and action=='prepare':
                    resumed=STORE.resume_editor(job['id'])
                    if resumed:
                        try:frozen=load_bundle(STATE/'snapshots'/job['id'])
                        except Exception:
                            STORE.update(job['id'],'failed','原任务素材快照不可用，请重新载入文章后重试')
                            raise
                        frozen['name']=bundle['name']
                        (DY_POOL if platform.startswith('douyin') else WX_POOL if platform=='wechat_browser' else XHS_POOL).submit(run_job,frozen,platform,action,job,settings)
                if created:
                    try:frozen=snapshot(bundle,job['id'])
                    except Exception:
                        STORE.update(job['id'],'failed','文件快照失败，请重新载入文章');raise
                    (WX_POOL if platform in ('wechat','wechat_browser') else DY_POOL if platform in ('douyin','douyin_video') else XHS_POOL).submit(run_job,frozen,platform,action,job,settings)
                return self.send({'job':job,'created':created,'resumed':resumed},202)
            if self.path=='/api/jobs/refresh':
                job=STORE.get(str(data.get('id','')))
                settings=STORE.settings(private=True)
                if job['platform']!='wechat' or job['account']!=settings['appid']:raise ValueError('只能查询当前公众号账号下的发表记录')
                return self.send(WeChat(settings).refresh(STORE,job['id']))
            if self.path=='/api/jobs/resolve':
                job=STORE.get(str(data.get('id','')))
                if job['status']!='uncertain' or data.get('confirmed_not_sent') is not True:raise ValueError('只有核实未发送的待核实记录可以解除重复保护')
                return self.send(STORE.update(job['id'],'resolved_not_sent','用户在平台核实未发送，已允许重新提交'))
            if self.path=='/api/jobs/confirm-published':
                if data.get('confirmed') is not True:raise ValueError('请先确认已在平台完成发布')
                return self.send(STORE.confirm_published(str(data.get('id',''))))
            return self.send({'error':'不存在'},404)
        except (ValueError,KeyError,TypeError,PlatformError,FileNotFoundError) as e:return self.send({'error':str(e)},400)
        except Exception:return self.send({'error':'操作未完成，请检查文件和本机配置；未输出敏感错误详情'},500)


if __name__=='__main__':
    server=ThreadingHTTPServer(('127.0.0.1',PORT),Handler)
    STORE=Store(STATE)
    from publisher.video import migrate_legacy
    migrate_legacy(ARTICLES,VIDEOS,STATE,STORE)
    print(f'Zhizhou Publisher: http://127.0.0.1:{PORT}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
