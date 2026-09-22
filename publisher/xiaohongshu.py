"""Visible, dedicated Edge profile; only creator-page controls, no reverse API signing.

Selector references: xpzouying/xiaohongshu-mcp, see REFERENCES.md.
Site selectors are an integration assumption until verified against the user's login.
"""
import re
from pathlib import Path
from .content import inside
from .wechat import PlatformError,UncertainError

URL='https://creator.xiaohongshu.com/publish/publish'


class LoginNeeded(PlatformError):pass


class Xiaohongshu:
    def __init__(self,root):
        self.root=Path(root);self.context=None;self.page=None;self.runtime=None
        self.step='打开小红书编辑器'
    def browser(self):
        from playwright.sync_api import sync_playwright
        from .session import live_page
        page=live_page(self)
        if page is not None:return page
        if self.runtime is None:self.runtime=sync_playwright().start()
        try:
            self.context=self.runtime.chromium.launch_persistent_context(str(self.root/getattr(self,'profile_name','xhs-profile')),channel='msedge',headless=False,no_viewport=True,locale='zh-CN')
        except Exception:
            raise PlatformError('无法启动专用 Edge 浏览器。请安装或更新 Microsoft Edge，并关闭上次的纸舟专用浏览器后重试。')
        self.page=self.context.pages[0] if self.context.pages else self.context.new_page()
        self.page.set_default_timeout(20000)
        return self.page
    def login(self):
        page=self.browser()
        page.goto(URL,wait_until='domcontentloaded',timeout=45000)
        page.bring_to_front()
        return {'message':'已打开小红书专用浏览器。请自行扫码登录，并确认右上角账号正确；登录会话保留在本机。'}
    def screenshot(self,jobid):
        try:
            import json
            details=self.page.evaluate('''() => ({
                controls:[...document.querySelectorAll('input,textarea,[contenteditable=true]')].map(el=>({
                    tag:el.tagName,type:el.getAttribute('type'),accept:el.getAttribute('accept'),
                    placeholder:el.getAttribute('placeholder'),classes:el.className,
                    visible:!!(el.getBoundingClientRect().width&&el.getBoundingClientRect().height)})),
                tabs:[...document.querySelectorAll('span,button,[role=tab]')].filter(el=>/^(上传图文|上传视频)$/.test(el.textContent.trim()))
                    .map(el=>({text:el.textContent.trim(),classes:el.className,
                        parentClasses:el.parentElement?.className,
                        parentText:el.parentElement?.textContent.trim().slice(0,180),
                        visible:!!el.getBoundingClientRect().height,rect:el.getBoundingClientRect().toJSON()}))})''')
            path=self.root/'diagnostics';path.mkdir(parents=True,exist_ok=True)
            (path/(jobid+'-xhs.json')).write_text(json.dumps({'step':getattr(self,'step',''),**details},ensure_ascii=False,indent=2),encoding='utf-8')
        except Exception:pass
        try:
            path=self.root/'screenshots';path.mkdir(exist_ok=True)
            self.page.screenshot(path=str(path/(jobid+'.png')),full_page=False)
            return '/api/screenshots/'+jobid+'.png'
        except Exception:return None
    def first_visible(self,page,selectors):
        for selector in selectors:
            candidates=page.locator(selector)
            for i in range(candidates.count()):
                if candidates.nth(i).is_visible():return candidates.nth(i)
        return None
    def open_image_mode(self,page):
        # Hidden duplicate tabs can precede the current tab in DOM order.
        # Wait for the visible tab and then the image input mounted by it.
        import time
        self.step='切换到上传图文'
        tabs=page.get_by_text('上传图文',exact=True).filter(visible=True)
        tabs.first.wait_for(state='visible',timeout=15000)
        candidates=[tabs.nth(i) for i in range(tabs.count())]
        # The creator page also exposes same-name sidebar/menu controls.
        # Identify the compact mode switch shown in the actual page instead
        # of treating every visible occurrence as the same action.
        grouped=[tab for tab in candidates if tab.evaluate('''el => {
            for(let p=el.parentElement;p&&p!==document.body;p=p.parentElement){
                const text=p.textContent.replace(/\\s/g,'');
                if(text.length>180)break;
                if(text.includes('上传视频')&&text.includes('上传图文')&&
                   (text.includes('写长文')||text.includes('发播客')))return true;
            }return false;
        }''')]
        if grouped:candidates=grouped
        # CSS-visible off-screen copies must not compete with the actual tab.
        if len(candidates)>1:
            candidates=[tab for tab in candidates if tab.evaluate('''el => {
                const r=el.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2;
                if(x<0||y<0||x>=innerWidth||y>=innerHeight)return false;
                const hit=document.elementFromPoint(x,y);
                return !!hit&&(el===hit||el.contains(hit));
            }''')]
        if len(candidates)!=1:
            raise PlatformError('顶部图文标签尚未唯一定位，已停止，请保留页面。')
        candidates[0].click()
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            targets=self.image_inputs(page)
            if len(targets)==1:return targets[0]
            if len(targets)>1:raise PlatformError('图文上传入口不唯一，未向不确定的位置上传。')
            page.wait_for_timeout(100)
        raise PlatformError('点击上传图文后，图片上传区未就绪；当前尚未上传图片。')

    def image_inputs(self,page):
        inputs=page.locator('input[type="file"]')
        return [inputs.nth(i) for i in range(inputs.count())
                if inputs.nth(i).evaluate('''el => !el.disabled &&
                    /image|\\.png|\\.jpe?g|\\.webp/i.test(el.accept) &&
                    !/video/i.test(el.accept)''')]
    def fill_topics(self,page,editor,tags):
        for tag in tags:
            self.step='选择小红书话题：'+tag
            editor.press('Control+End')
            editor.press('Enter')
            editor.press_sequentially('#'+tag,delay=60)
            menu=page.locator('#creator-editor-topic-container').filter(visible=True)
            menu.wait_for(state='visible',timeout=10000)
            choices=menu.locator('.item').filter(has=page.get_by_text(re.compile(r'^#?'+re.escape(tag)+r'$')))
            choices.first.wait_for(state='visible',timeout=10000)
            if choices.count()!=1:
                raise PlatformError('未找到唯一匹配的话题「'+tag+'」，请在小红书页面选择；尚未发布。')
            choices.click()
            editor.press('End')
            editor.press('Space')
    def set_original(self,page):
        self.step='设置原创声明'
        card=page.locator('.custom-switch-card').filter(has_text='原创声明')
        switch=card.locator('.d-switch')
        if switch.count()!=1:raise PlatformError('未找到唯一原创声明开关，尚未发布。')
        checked=lambda: switch.locator('input[type="checkbox"]').is_checked()
        if checked():return
        switch.click()
        footer=page.locator('.footer').filter(has_text='原创声明须知').filter(visible=True)
        footer.wait_for(state='visible',timeout=10000)
        consent=footer.locator('.d-checkbox')
        if not consent.locator('input[type="checkbox"]').is_checked():consent.click()
        button=page.get_by_role('button',name='声明原创',exact=True).filter(visible=True)
        if button.count()!=1:raise PlatformError('原创声明确认按钮不唯一，尚未发布。')
        button.click()
        from playwright.sync_api import expect
        expect(switch.locator('input[type="checkbox"]')).to_be_checked(timeout=10000)
    def run(self,bundle,store,job,action='publish'):
        from playwright.sync_api import TimeoutError as BrowserTimeout
        bundle=dict(bundle,images=bundle.get('xhs_images',bundle['images']))
        id=job['id'];clicked=False
        if action not in ('prepare','publish'):raise PlatformError('不支持的小红书操作')
        if not bundle['images']:raise PlatformError('小红书图文需要至少一张图片')
        if len(bundle['images'])>18:raise PlatformError('本版最多上传 18 张图片，请拆分文章；实际数量限制以平台为准')
        if len(bundle['xhs_title'])>20:raise PlatformError('本版小红书标题最多 20 个字符。请在 xhs_title 或 xiaohongshu.md 设置短标题。')
        if len(bundle['xhs_body'])>1000:raise PlatformError('本版小红书图文正文最多 1000 个字符。请增加 xiaohongshu.md 短文；不会擅自截断长文。')
        try:
            self.step='打开小红书创作页面'
            page=self.browser()
            prepared=getattr(self,'prepared',None)
            if action=='prepare' and prepared and prepared['page']==page and prepared['fingerprint']==bundle.get('fingerprint') and prepared['url']==page.url:
                page.bring_to_front()
                return store.update(id,'editor_ready','已切回小红书编辑器，保留当前编辑内容；请核对后手动发布。',screenshot=self.screenshot(id))
            page.goto(URL,wait_until='domcontentloaded',timeout=45000)
            if 'login' in page.url:raise LoginNeeded('请在已打开的小红书窗口扫码登录，再回本程序点击发送')
            target=self.open_image_mode(page)
            store.update(id,'running','正在按编号上传小红书图片')
            paths=[str(inside(Path(bundle['folder']),ref)) for ref in bundle['images']]
            if target.get_attribute('multiple') is not None:
                self.step=f'上传全部 {len(paths)} 张小红书图片'
                target.set_input_files(paths)
                page.locator('.img-preview-area .pr').nth(len(paths)-1).wait_for(state='visible',timeout=120000)
            else:
                for i,path in enumerate(paths):
                    self.step=f'上传小红书图片 {i+1}/{len(paths)}'
                    if i:
                        targets=self.image_inputs(page)
                        if len(targets)>1:
                            targets=[t for t in targets if t.get_attribute('multiple') is not None]
                        if len(targets)!=1:raise PlatformError('未能区分追加图片与替换图片入口，已停止，未发布。')
                        target=targets[0]
                    target.set_input_files(path)
                    page.locator('.img-preview-area .pr').nth(i).wait_for(state='visible',timeout=60000)
            self.step='填写标题与正文'
            title=self.first_visible(page,['input[placeholder*="标题"]','div.d-input input','input.d-text'])
            editor=self.first_visible(page,['div.tiptap.ProseMirror','div.ProseMirror[contenteditable="true"]','[role="textbox"][contenteditable="true"]'])
            if title is None or editor is None:raise PlatformError('图片已上传，但没有找到唯一可用的标题或正文编辑器，已停止，未点击发布')
            title.fill(bundle['xhs_title']);editor.fill(bundle.get('xhs_text',bundle['xhs_body']))
            self.fill_topics(page,editor,bundle.get('tags',[]))
            title.click()
            if title.input_value()!=bundle['xhs_title']:raise PlatformError('平台标题与预览不一致，未发布')
            expected=re.sub(r'\s+','',bundle['xhs_body'])
            if re.sub(r'\s+','',editor.inner_text())!=expected:raise PlatformError('平台正文与预览不一致，未发布')
            if bundle.get('xhs_original') is True:self.set_original(page)
            if page.get_by_text(re.compile('请完成验证|拖动滑块|安全验证')).first.is_visible():raise PlatformError('平台要求本人完成验证，请在浏览器处理；本程序不会绕过验证')
            if action=='prepare':
                self.prepared={'page':page,'fingerprint':bundle.get('fingerprint'),'url':page.url}
                return store.update(id,'editor_ready','小红书标题、正文和图片已填入，尚未发布。请在专用浏览器核对后点击发布。',screenshot=self.screenshot(id))
            store.update(id,'running','标题、正文和图片已填入，正在检查发布按钮')
            button=page.get_by_role('button',name='发布',exact=True)
            if button.count()!=1 or not button.is_visible():
                button=page.locator('xhs-publish-btn').filter(visible=True)
                if button.count()!=1 or button.get_attribute('is-publish')=='false' or button.get_attribute('submit-disabled')=='true':
                    raise PlatformError('没有找到唯一可用的发布按钮，已保留浏览器内容，未发布')
            if not button.is_enabled():raise PlatformError('平台发布按钮不可用，已停止，未发布')
            store.update(id,'submitting','正在提交小红书笔记')
            clicked=True
            button.click()
            # Navigation alone is not success: login/challenge pages can also redirect.
            success=page.get_by_text(re.compile(r'^(发布成功|发布完成|笔记发布成功)$')).first
            try:success.wait_for(state='visible',timeout=30000)
            except BrowserTimeout:raise UncertainError('已点击发布，但未看到明确成功提示。请到小红书创作中心核实，不要直接重发。')
            shot=self.screenshot(id)
            return store.update(id,'platform_confirmed','小红书页面显示发布成功；公开可见状态仍可能受审核影响',screenshot=shot,evidence=success.inner_text())
        except (LoginNeeded,PlatformError,UncertainError):raise
        except Exception as exc:
            if clicked:raise UncertainError('提交后浏览器连接中断，结果待核实。请先查看小红书创作中心。')
            raise PlatformError(f'小红书在「{self.step}」中断（{type(exc).__name__}），尚未点击发布。请保留专用浏览器页面。') from None
