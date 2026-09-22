"""Douyin public creator UI; prepare only, never clicks publish."""
from pathlib import Path
import re
from .xiaohongshu import Xiaohongshu, LoginNeeded
from .content import inside
from .wechat import PlatformError

URL='https://creator.douyin.com/creator-micro/content/upload?default-tab=3'

class Douyin(Xiaohongshu):
    profile_name='douyin-profile'
    def login(self):
        page=self.browser();page.goto(URL,wait_until='domcontentloaded',timeout=45000);page.bring_to_front()
        return {'message':'已打开抖音专用窗口，请扫码登录并确认账号，再回工作台自动填入。'}
    def run(self,bundle,store,job,action='prepare',video=False):
        if action!='prepare':raise PlatformError('抖音当前只支持自动填入，请在网页手动发布')
        images=[bundle['douyin_video']] if video else bundle['douyin_images']
        prefix='douyin_video' if video else 'douyin'
        if not images or not all(images):raise PlatformError('请先配置抖音素材')
        page=self.browser();self.page=page
        page.bring_to_front()
        prepared=getattr(self,'prepared',None)
        if prepared and prepared['page']==page and prepared['fingerprint']==bundle.get('fingerprint') and page.url==prepared['url'] and prepared.get('video',False)==video:
            # Reopening must not navigate away from the user's current edits.
            return store.update(job['id'],'editor_ready','已切回此前填入的抖音编辑器，保留当前编辑内容；请在页面核对后手动发布。',screenshot=self.screenshot(job['id']))
        self.step='打开抖音视频编辑器' if video else '打开抖音图文编辑器'
        page.goto(URL.split('?')[0] if video else URL,wait_until='domcontentloaded',timeout=45000)
        page.bring_to_front()
        if 'login' in page.url:raise LoginNeeded('请在抖音专用窗口完成扫码登录')
        try:
            self.step='等待抖音素材上传入口'
            page.locator('input[type=file]').first.wait_for(state='attached',timeout=20000)
            choices=[item for item in page.locator('input[type=file]').all() if any(x in (item.get_attribute('accept') or '').lower() for x in ('video','.mp4'))] if video else self.image_inputs(page)
            if len(choices)!=1:raise PlatformError('未找到唯一的抖音上传入口，请在专用窗口检查发布类型')
            target=choices[0]
            if len(images)>1 and target.get_attribute('multiple') is None:raise PlatformError('当前抖音入口不支持多图选择，已停止，请检查图文发布模式')
            store.update(job['id'],'running','正在向抖音编辑器上传素材')
            target.set_input_files([str(inside(Path(bundle['folder']),ref)) for ref in images])
            self.step='填写抖音标题和正文'
            titles=page.locator('input[placeholder*="标题"],textarea[placeholder*="标题"]').filter(visible=True)
            titles.first.wait_for(state='visible',timeout=120000)
            editors=page.locator('[contenteditable=true]').filter(visible=True)
            editors.first.wait_for(state='visible',timeout=20000)
            if titles.count()!=1 or editors.count()!=1:raise PlatformError('抖音标题或正文输入栏不唯一，已停止，未发布')
            titles.fill(bundle[prefix+'_title']);editors.fill(bundle[prefix+'_body']);titles.click()
            if titles.input_value()!=bundle[prefix+'_title'] or re.sub(r'\s','',editors.inner_text())!=re.sub(r'\s','',bundle[prefix+'_body']):raise PlatformError('抖音文案核对不一致，未发布')
            self.prepared={'page':page,'fingerprint':bundle.get('fingerprint'),'url':page.url,'video':video}
            return store.update(job['id'],'editor_ready','已向抖音提交素材并填入文案。请等待转码完成，核对封面、话题、可见范围及声明后手动发布；当前未发布。',screenshot=self.screenshot(job['id']))
        except PlatformError:raise
        except Exception as exc:raise PlatformError(f'抖音在「{self.step}」中断（{type(exc).__name__}），未发布；请保留页面。') from None
