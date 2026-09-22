"""WeChat official endpoints, with no automatic retry of uncertain mutations."""
import json
import time
import re
import hashlib
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit,parse_qsl
import httpx
from .content import inside,render,wx_image


class PlatformError(Exception): pass
class UncertainError(Exception): pass


def draft_signature(article):
    class Content(HTMLParser):
        def __init__(self):super().__init__();self.text=[];self.images=[]
        def handle_data(self,data):self.text.append(data)
        def handle_starttag(self,tag,attrs):
            if tag=='img':self.images.append(dict(attrs).get('src',''))
    parsed=Content();parsed.feed(article.get('content',''))
    fields={k:article.get(k,'') for k in ('title','author','digest','content_source_url','thumb_media_id')}
    fields.update(text=re.sub(r'\s+','', ''.join(parsed.text)),images=parsed.images)
    return hashlib.sha256(json.dumps(fields,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


ERRORS={40164:'当前出口 IP 不在微信白名单。请到微信开发者后台添加实际出口 IP，网络切换后可能需要更新。',40125:'AppSecret 无效，请在本机设置中重新填写。',40013:'AppID 无效或与当前业务不匹配。',40001:'凭证无效；请重新测试连接。',48001:'当前账号没有这个接口的权限。请核对后台接口权限表；有 AppID 不代表有草稿或发表权限。',45009:'接口调用达到限制，请稍后再试。',40007:'素材 ID 无效，请在后台核实该素材。',45065:'同一草稿可能已发布，请先核实平台状态。'}


class WeChat:
    def __init__(self,settings,client=None):
        self.settings=settings;self.client=client or httpx.Client(timeout=45,follow_redirects=False)
        self.token_value=None;self.expiry=0
    def request(self,path,payload=None,files=None,mutation=False,token=True):
        path,_,query=path.partition('?')
        params=dict(parse_qsl(query))
        if token:params['access_token']=self.token()
        try:
            kw={'params':params}
            if files:kw['files']=files
            elif payload is not None:
                kw['content']=json.dumps(payload,ensure_ascii=False).encode();kw['headers']={'Content-Type':'application/json; charset=utf-8'}
            resp=self.client.post('https://api.weixin.qq.com/cgi-bin/'+path,**kw)
            resp.raise_for_status();data=resp.json()
        except (httpx.HTTPError,ValueError):
            if mutation:raise UncertainError('请求已发出但未收到可靠结果。请先到公众号后台核实，不要立即重发。')
            raise PlatformError('无法连接微信接口。请检查网络后重试；日志不会记录密钥。')
        if data.get('errcode',0):
            code=data['errcode']
            raise PlatformError(f'微信错误 {code}：'+ERRORS.get(code,'平台拒绝本次操作，请在公众号后台核查内容与权限。'))
        return data
    def token(self):
        if self.token_value and time.time()<self.expiry:return self.token_value
        if not self.settings.get('secret'):raise PlatformError('请在账号设置中填写 AppSecret（不是模型 API Key）。')
        data=self.request('stable_token',{'grant_type':'client_credential','appid':self.settings['appid'],'secret':self.settings['secret'],'force_refresh':False},token=False)
        if not data.get('access_token'):raise PlatformError('微信没有返回有效凭证')
        self.token_value=data['access_token'];self.expiry=time.time()+int(data.get('expires_in',7200))-300
        return self.token_value
    def check(self):
        self.token()
        self.request('draft/batchget',{'offset':0,'count':1,'no_content':1})
        return {'message':'凭证有效，草稿读取接口可用。正式发表权限仍以提交时微信返回为准。','draft_readable':True}
    def run(self,bundle,action,store,job):
        id=job['id'];folder=Path(bundle['folder'])
        if not bundle['cover']:raise PlatformError('公众号需要封面，请在 images 中放一张图片或在文案配置 cover。')
        if len(bundle['title'])>64:raise PlatformError('公众号标题超过本程序的 64 字限制，请修改。')
        if len(bundle['author'])>8:raise PlatformError('作者名超过本程序的 8 字限制，请修改。')
        if len(bundle['digest'])>120:raise PlatformError('摘要超过本程序的 120 字限制，请修改。')
        if bundle['source_url'] and urlsplit(bundle['source_url']).scheme not in ('http','https'):raise PlatformError('阅读原文链接必须是 http 或 https 地址')
        prior=store.prior_draft(bundle,self.settings['appid']) or {}
        media_id=prior.get('media_id');signature=prior.get('draft_signature')
        if not media_id:
            self.token()
            store.update(id,'running','正在上传公众号图片')
            urls={}
            refs=list(dict.fromkeys(bundle['refs'] or bundle['images']))
            for ref in refs:
                data=self.request('media/uploadimg',files={'media':('image.jpg',wx_image(inside(folder,ref)),'image/jpeg')})
                if not data.get('url'):raise PlatformError('正文图片上传未返回链接')
                urls[ref]=data['url']
            thumb=self.request('material/add_material?type=image',files={'media':('cover.jpg',wx_image(inside(folder,bundle['cover'])),'image/jpeg')})
            if not thumb.get('media_id'):raise PlatformError('封面上传未返回素材 ID')
            content=render(bundle,lambda ref:urls[ref])
            if len(content)>=20000 or len(content.encode())>=1_000_000:raise PlatformError('公众号排版后正文过长，请拆分文章')
            article={'article_type':'news','title':bundle['title'],'author':bundle['author'],'digest':bundle['digest'],'content':content,'content_source_url':bundle['source_url'],'thumb_media_id':thumb['media_id'],'need_open_comment':0,'only_fans_can_comment':0}
            # Persist before a non-idempotent call. A crash now is recorded as uncertain.
            store.update(id,'submitting','正在创建公众号草稿')
            draft=self.request('draft/add',{'articles':[article]},mutation=True)
            media_id=draft.get('media_id')
            if not media_id:raise UncertainError('微信草稿响应缺少 media_id，请到后台核实')
            signature=draft_signature(article)
            store.update(id,'running','公众号草稿已创建',media_id=media_id,draft_signature=signature)
        if action=='draft':return store.update(id,'draft_created','已保存到公众号草稿箱，尚未发表',media_id=media_id)
        # A draft may have been edited in WeChat since creation. Never publish
        # an externally changed draft while showing the old local preview.
        remote=self.request('draft/get',{'media_id':media_id}).get('news_item',[])
        if len(remote)!=1 or not signature or draft_signature(remote[0])!=signature:
            raise PlatformError('公众号草稿与本地预览不一致，已停止发表。请在平台核实是否修改了文案或图片。')
        store.update(id,'submitting','正在请求正式发表',media_id=media_id,draft_signature=signature)
        result=self.request('freepublish/submit',{'media_id':media_id},mutation=True)
        publish_id=result.get('publish_id')
        if not publish_id:raise UncertainError('发表请求未返回发布 ID，请到后台核实')
        store.update(id,'submitted','微信已受理，正在处理；这不等于向粉丝群发',publish_id=publish_id)
        return self.refresh(store,id)
    def refresh(self,store,id):
        job=store.get(id);publish_id=job['result'].get('publish_id')
        if not publish_id:raise PlatformError('此记录没有可查询的发布 ID')
        result=self.request('freepublish/get',{'publish_id':publish_id})
        status=result.get('publish_status')
        if status==0:
            items=result.get('article_detail',{}).get('item',[])
            return store.update(id,'published','微信确认已发表（未执行粉丝群发）',article_id=result.get('article_id'),links=[x.get('article_url') for x in items if x.get('article_url')])
        if status==1:return store.update(id,'submitted','微信仍在处理，请稍后查询结果')
        if status in (2,3,4):return store.update(id,'rejected','微信未完成发表，请在后台查看原因',publish_status=status)
        if status in (5,6):return store.update(id,'unavailable','文章发布后已删除或被平台封禁，请在后台核实',publish_status=status)
        return store.update(id,'submitted','收到未知处理状态，请到公众号后台核实')
