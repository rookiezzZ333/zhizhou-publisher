"""Bilibili creator-page adapter. Prepare only; never submit a publication."""
import re
from pathlib import Path
from .xiaohongshu import Xiaohongshu,LoginNeeded
from .content import inside
from .wechat import PlatformError

URL='https://member.bilibili.com/platform/upload/video/frame'

class Bilibili(Xiaohongshu):
    profile_name='bilibili-profile'
    def login(self):
        page=self.browser();page.goto(URL,wait_until='domcontentloaded',timeout=45000);page.bring_to_front()
        return {'message':'已打开 B 站专用窗口，请登录并确认投稿账号。'}
    def video_inputs(self,scope):
        return [item for item in scope.locator('input[type=file]').all()
                if not item.is_disabled() and any(value in (item.get_attribute('accept') or '').lower()
                for value in ('video','.mp4'))]
    def upload_target(self,page):
        # Bilibili mounts a hidden legacy picker beside the active visible picker.
        # Inspect all frames and prefer the unique visible video input. Never pick
        # the first duplicate or the unrelated .txt subtitle input.
        scopes=[page]+[f for f in page.frames if f!=page.main_frame]
        candidates=[(scope,item) for scope in scopes for item in self.video_inputs(scope)]
        visible=[entry for entry in candidates if entry[1].is_visible()]
        if len(visible)==1:return visible[0]
        if not visible and len(candidates)==1:return candidates[0]
        return None
    def scope(self,page):
        target=self.upload_target(page)
        return target[0] if target else page
    def unique(self,scope,selector,label,timeout=20000):
        items=scope.locator(selector).filter(visible=True)
        items.first.wait_for(state='visible',timeout=timeout)
        if items.count()!=1:raise PlatformError('B 站'+label+'入口不唯一，请保留页面检查')
        return items
    def fill_form(self,scope,bundle):
        copy=bundle['copies']['bilibili']
        title=self.unique(scope,'input[placeholder*="标题"],textarea[placeholder*="标题"]','标题',120000)
        body=self.unique(scope,'textarea[placeholder*="简介"],textarea[placeholder*="介绍"],[contenteditable=true][data-placeholder*="简介"],.ql-editor[contenteditable=true],.editor[contenteditable=true]','简介')
        if title.input_value().strip() not in ('',copy['title']):
            # Bilibili commonly uses the filename as the initial title.
            stem=Path(bundle['video']).stem
            if title.input_value().strip()!=stem:raise PlatformError('B 站标题已有其他内容，已停止以保留编辑')
        title.fill(copy['title']);body.fill(copy['body']);title.click()
        actual=body.input_value() if body.evaluate('el=>el.tagName') in ('INPUT','TEXTAREA') else body.inner_text()
        if title.input_value()!=copy['title'] or re.sub(r'\s','',actual)!=re.sub(r'\s','',copy['body']):raise PlatformError('B 站文案回读不一致，未投稿')
        tags=bundle.get('bilibili_tags',[])
        if tags:
            field=self.unique(scope,'input[placeholder*="标签"],input[placeholder*="按回车"]','标签')
            for tag in tags:
                # Avoid toggling an existing selected tag.
                selected=scope.locator('.tag-item,.tag-pre-item,[data-tag]').filter(has_text=re.compile('^'+re.escape(tag)+r'\s*[×x✕]?$'))
                if selected.count():continue
                field.fill(tag);field.press('Enter')
                selected.first.wait_for(state='visible',timeout=5000)
                if field.input_value().strip():raise PlatformError('B 站标签尚未确认添加，请在平台检查')
    def run(self,bundle,store,job,action='prepare'):
        if action!='prepare':raise PlatformError('B 站仅支持准备投稿，请在平台手动发布')
        if not bundle['video']:raise PlatformError('请先选择视频')
        self.step='打开 B 站投稿页面'
        page=self.browser();self.page=page;page.bring_to_front()
        prepared=getattr(self,'prepared',None)
        if prepared and prepared['page']==page and prepared['fingerprint']==bundle['fingerprint'] and page.url==prepared['url']:
            return store.update(job['id'],'editor_ready','已切回 B 站编辑器，保留你的编辑，请核对后手动投稿。')
        try:
            page.goto(URL,wait_until='domcontentloaded',timeout=45000)
            if 'passport.bilibili.com' in page.url or 'login' in page.url:raise LoginNeeded('请先在 B 站专用窗口登录，再点击自动填入')
            self.step='等待视频上传入口'
            import time
            deadline=time.monotonic()+20
            while True:
                target=self.upload_target(page)
                if target:
                    scope,picker=target
                    break
                if time.monotonic()>deadline:raise PlatformError('未找到唯一的 B 站视频入口，请检查登录状态与投稿页面')
                page.wait_for_timeout(250)
            store.update(job['id'],'running','正在向 B 站提交视频文件，请等待上传')
            self.step='上传视频'
            file=str(inside(Path(bundle['folder']),bundle['video']))
            buttons=scope.get_by_text('上传视频',exact=True).filter(visible=True)
            if buttons.count()==1:
                # Use the control's actual file chooser: a visible file input can
                # be an inert duplicate while the button owns a hidden uploader.
                with page.expect_file_chooser(timeout=15000) as opened:
                    buttons.click(force=True)
                chooser=opened.value
                accept=(chooser.element.get_attribute('accept') or '').lower()
                if not any(v in accept for v in ('video','.mp4')):raise PlatformError('B 站按钮未打开视频选择器，已停止')
                chooser.set_files(file)
            elif buttons.count()>1:
                raise PlatformError('B 站上传视频按钮不唯一，已停止')
            else:
                picker.set_input_files(file)
            store.update(job['id'],'running','视频已选择，正在等待 B 站上传表单出现')
            self.step='填写标题、简介与标签'
            self.fill_form(scope,bundle)
            self.prepared={'page':page,'fingerprint':bundle['fingerprint'],'url':page.url}
            return store.update(job['id'],'editor_ready','视频已交给 B 站上传控件，标题、简介和标签已填入；请等待上传转码，并手动核对封面、分区、创作类型、转载来源和声明后投稿。',screenshot=self.screenshot(job['id']))
        except PlatformError:raise
        except Exception as exc:
            raise PlatformError('B 站在「'+self.step+'」中断（'+type(exc).__name__+'）；未投稿，请保留页面检查。') from None
