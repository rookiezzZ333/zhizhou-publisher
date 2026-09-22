"""Experimental public-page adapter. No AppSecret, private HTTP API or cookie export.

Editor entry points were cross-checked against JimLiu/baoyu-skills' public
wechat-article.ts. Implementation is independent. Real-account QA is pending.
"""
import html
import json
import re
import time
import uuid
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from .content import render, inside
from .wechat import PlatformError, UncertainError
from .xiaohongshu import Xiaohongshu, LoginNeeded
from .wechat_editor import (resolve_field, read_field, write_field, PASTE_HTML,
                            DOCUMENT_STATE, EXPECTED_BLOCKS, check_document, set_cover, check_cover, wait_for_paste)

EDITOR = '.rich_media_content .ProseMirror[contenteditable="true"]'

# Inspect a copy so checking emptiness never changes the live editor.
BODY_STATE = r'''el => {
    const copy=el.cloneNode(true);
    copy.querySelectorAll('img.ProseMirror-separator').forEach(n=>n.remove());
    const hints=new Set(['从这里开始写正文','请输入正文','从这里开始输入正文']);
    copy.querySelectorAll('*').forEach(n=>{
        const decoration=/placeholder/i.test(n.className||'')||n.getAttribute('contenteditable')==='false';
        if(decoration&&hints.has((n.textContent||'').trim())&&!n.querySelector('img,video,audio,iframe,table'))n.remove();
    });
    const text=(copy.textContent||'').replace(/[\u200b\ufeff]/g,'').trim();
    const media=copy.querySelectorAll('img,video,audio,iframe,table,hr,svg,canvas,embed,object').length;
    return {empty:media===0&&!text};
}'''


def normalize(text):
    return re.sub(r'\s+', '', text).replace('\u200b', '')


class TextOnly(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []
    def handle_data(self, data):
        self.parts.append(data)


def prepare_body(bundle):
    """One distinct marker per occurrence, including repeat uses of the same file."""
    prefix = 'ZZIMAGE' + uuid.uuid4().hex
    refs = []
    def marker(ref):
        key = prefix + 'N' + str(len(refs)) + 'END'
        refs.append((key, ref))
        return key
    markup = render(bundle, marker)
    markup = re.sub(r'<img\b[^>]*\bsrc="([^"]+)"[^>]*>', lambda m: html.escape(m[1]), markup)
    parser = TextOnly(); parser.feed(markup)
    expected = ''.join(parser.parts)
    for index, (key, _) in enumerate(refs):
        expected = expected.replace(key, f'ZZSLOT{index}END')
    return markup, refs, expected


def launch_error(exc):
    # Classify without printing raw browser arguments, profile data or login URLs.
    text = str(exc).lower()
    if 'executable' in text and ('exist' in text or 'found' in text):
        return '未找到 Microsoft Edge，请先安装 Edge。无需下载 Playwright 浏览器。'
    if any(x in text for x in ('singleton', 'processsingleton', 'profile in use')):
        return '专用浏览器资料目录被占用。请关闭纸舟打开的公众号浏览器后重试。'
    if any(x in text for x in ('access is denied', 'winerror 5', 'eperm', 'permission denied')):
        return '系统拒绝启动浏览器进程。请从项目内的启动.cmd运行程序，并检查系统拦截提示。'
    return 'Edge 启动失败（' + type(exc).__name__ + '）。请关闭此前纸舟专用窗口后重试；普通浏览器无需关闭。'


class WeChatBrowser(Xiaohongshu):
    def __init__(self, root):
        super().__init__(root)
        self.active = None
        self.step = '准备编辑器'

    def interruption_message(self, exc):
        if self.active and self.active.get('body_ready'):
            return f'操作在「{self.step}」中断（{type(exc).__name__}）。已保留正文恢复记录；重新打开同一篇图文后重试，核对通过后继续封面步骤。'
        return f'操作在「{self.step}」中断（{type(exc).__name__}）。请查看专用浏览器现场；若已有部分内容，请保存后新建空白图文再试。'

    def screenshot(self, jobid):
        # Save control structure, never input values, login URLs or cookies.
        try:
            details = self.page.evaluate('''() => {
                const visible=el=>!!(el.getBoundingClientRect().width&&el.getBoundingClientRect().height);
                return {controls:[...document.querySelectorAll('input,textarea,[contenteditable=true]')].map(el=>({
                    tag:el.tagName,id:el.id,classes:el.className,type:el.getAttribute('type'),
                    placeholder:el.getAttribute('placeholder')||el.getAttribute('data-placeholder'),
                    visible:visible(el),accept:el.getAttribute('accept')})),
                    buttons:[...document.querySelectorAll('button,[role=button]')].filter(visible).map(el=>(el.textContent||'').trim().slice(0,80)),
                    coverControls:[...document.querySelectorAll('#js_cover_area, #js_cover_area *, a, [role=menuitem]')]
                        .filter(el=>el.closest('#js_cover_area')||/^(从正文(?:中)?选择|从图片库选择|下一步|确定|确认|完成)$/.test((el.textContent||'').trim()))
                        .map(el=>({tag:el.tagName,id:el.id,classes:el.className,visible:visible(el),text:(el.textContent||'').trim().slice(0,80)}))};
            }''')
            path = self.root / 'diagnostics'; path.mkdir(exist_ok=True, parents=True)
            (path / (jobid + '.json')).write_text(json.dumps({'step': self.step, **details}, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception:
            pass
        return super().screenshot(jobid)

    def browser(self):
        from playwright.sync_api import sync_playwright
        from .session import live_page
        page=live_page(self)
        if page is not None:return page
        if self.runtime is None:
            self.runtime = sync_playwright().start()
        try:
            self.context = self.runtime.chromium.launch_persistent_context(
                str(self.root / 'wechat-profile'), channel='msedge', headless=False,
                no_viewport=True, locale='zh-CN')
        except Exception as exc:
            raise PlatformError(launch_error(exc)) from None
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.page.set_default_timeout(15000)
        return self.page

    def login(self):
        page = self.browser()
        if urlsplit(page.url).hostname != 'mp.weixin.qq.com':
            page.goto('https://mp.weixin.qq.com/', wait_until='domcontentloaded', timeout=45000)
        page.bring_to_front()
        return {'message': '已打开公众号专用 Edge。请扫码登录并核对账号，再点击自动填入。无需 AppSecret。'}

    def editor(self):
        page = self.browser()
        if page.locator(EDITOR).count() == 1:
            return page
        for tab in self.context.pages:
            if urlsplit(tab.url).hostname == 'mp.weixin.qq.com' and tab.locator(EDITOR).count() == 1:
                self.page = tab
                return tab
        self.login()
        if '/cgi-bin/home' not in page.url:
            raise LoginNeeded('请在专用 Edge 扫码登录公众号，再回工作台点击自动填入。')
        entry = page.locator('.new-creation__menu-item').filter(has_text=re.compile(r'^\s*(图文|文章)\s*$'))
        if entry.count() != 1:
            raise LoginNeeded('已打开后台。请在专用 Edge 点击「新的创作 → 文章／图文」，保持空白编辑器打开后重试。')
        entry.click()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            for tab in self.context.pages:
                if urlsplit(tab.url).hostname == 'mp.weixin.qq.com' and tab.locator(EDITOR).count() == 1:
                    self.page = tab
                    return tab
            page.wait_for_timeout(250)
        raise LoginNeeded('请打开空白图文编辑器后重试；未识别当前页面。')

    def check_body(self, page, bundle, expected, sources, blocks):
        for name in ('title', 'author', 'digest'):
            if read_field(resolve_field(page, name, bool(bundle[name]))) != bundle[name]:
                raise PlatformError('标题、作者或摘要被改变，已停止保存。')
        check_document(page.locator(EDITOR).evaluate(DOCUMENT_STATE), expected, blocks, sources=sources)

    def fill(self, page, bundle, store, job):
        self.step = '检查标题、作者和摘要输入栏'
        fields = {name: resolve_field(page, name, bool(bundle[name])) for name in ('title', 'author', 'digest')}
        self.step = '检查编辑器是否为空'
        editor = page.locator(EDITOR)
        if not editor.evaluate(BODY_STATE)['empty'] or any(
                read_field(field) not in ('', bundle[name]) for name, field in fields.items()):
            raise PlatformError('当前编辑器已有内容，为避免覆盖，请先保存原稿并新建空白图文。')
        markup, refs, expected = prepare_body(bundle)
        # Paste the actual blocks, not an unsupported outer section wrapper.
        markup = re.sub(r'^<section[^>]*>|</section>$', '', markup)
        blocks = page.evaluate(EXPECTED_BLOCKS, markup)
        for index, (marker, _) in enumerate(refs):
            blocks = [text.replace(marker, f'ZZSLOT{index}END') for text in blocks]
        # Record ownership before changing the page: a partial insertion is not retryable.
        self.active = {'page': page, 'fingerprint': bundle['fingerprint'], 'ready': False}
        self.step = '填写标题、作者和摘要'
        for name, field in fields.items():
            write_field(field, bundle[name], name)
        self.step = '粘贴富文本正文'
        store.update(job['id'], 'running', '正在填入排版正文；请勿同时编辑这个窗口')
        inserted = editor.evaluate(PASTE_HTML, markup)
        if not inserted:
            raise PlatformError('微信编辑器拒绝插入排版内容，已停止，需要适配当前页面。')
        self.step = '等待微信完成正文粘贴和结构检测'
        store.update(job['id'], 'running', '正在等待微信完成正文粘贴和结构检测')
        editor.evaluate('el => el.blur()')
        wait_for_paste(page, editor, expected, blocks, refs)
        sources = []
        for index, (marker, ref) in enumerate(refs):
            self.step = f'上传正文图片 {index+1}/{len(refs)}'
            store.update(job['id'], 'running', f'正在上传正文图片 {index+1}/{len(refs)}')
            # Exclude cover/dialog uploads, which otherwise make the global input
            # count ambiguous. Never choose the first file input arbitrarily.
            targets = page.locator('input[type="file"][accept*="image"]')
            usable = [targets.nth(i) for i in range(targets.count())
                      if targets.nth(i).evaluate('el => !el.disabled && !el.closest("#js_cover_area, [role=dialog], .weui-desktop-dialog")')]
            if len(usable) != 1:
                raise PlatformError(f'正文图片上传入口不唯一（{len(usable)} 个），未上传到不确定的位置。请保留页面以便适配。')
            target = usable[0]
            selected = editor.evaluate('''(el, marker) => {
                const walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT);let node;const hits=[];
                while(node=walker.nextNode()){let i=node.data.indexOf(marker);if(i>=0)hits.push([node,i]);}
                if(hits.length!==1)return false; el.focus();const [n,i]=hits[0];
                const r=document.createRange();r.setStart(n,i);r.setEnd(n,i+marker.length);
                const s=window.getSelection();s.removeAllRanges();s.addRange(r);return true;
            }''', marker)
            if not selected:
                raise PlatformError('图片位置标记丢失，未继续上传。')
            page.keyboard.press('Backspace')
            target.set_input_files(str(inside(Path(bundle['folder']), ref)))
            page.wait_for_function('''({selector,count}) => {
                const images=[...document.querySelector(selector).querySelectorAll('img:not(.ProseMirror-separator)')];
                return images.length===count&&images.every(i=>i.complete&&i.naturalWidth>0&&/^https:\\/\\/[^/]*qpic\\.cn\\//.test(i.src));
            }''', arg={'selector': EDITOR, 'count': index + 1}, timeout=60000)
            sources = editor.locator('img:not(.ProseMirror-separator)').evaluate_all('(xs)=>xs.map(x=>x.src)')
        self.step = '核对正文和图片'
        self.check_body(page, bundle, expected, sources, blocks)
        self.active.update(expected=expected, sources=sources, blocks=blocks, refs=refs, body_ready=True)
        self.save_checkpoint()
        self.finish_cover(page, bundle, store, job)

    def save_checkpoint(self):
        # Keep only verified content and uploaded image identities. Never save
        # the editor URL (which contains an account token) or browser handles.
        state = {k: self.active[k] for k in
                 ('fingerprint', 'expected', 'sources', 'blocks', 'refs')}
        if self.active.get('ready'):
            state['cover'] = self.active['cover']
        path = self.root / 'wechat-body-checkpoint.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
        temporary.replace(path)

    def restore_checkpoint(self, page, bundle):
        path = self.root / 'wechat-body-checkpoint.json'
        if not path.exists():
            return False
        try:
            state = json.loads(path.read_text(encoding='utf-8'))
            if state['fingerprint'] != bundle['fingerprint']:
                return False
            self.check_body(page, bundle, state['expected'], state['sources'], state['blocks'])
        except (ValueError, KeyError, PlatformError):
            return False
        self.active = dict(state, page=page, ready=False, body_ready=True)
        if state.get('cover'):
            check_cover(page, state['cover'])
            self.active['ready'] = True
        return True

    def finish_cover(self, page, bundle, store, job):
        state = self.active
        self.check_body(page, bundle, state['expected'], state['sources'], state['blocks'])
        self.step = '设置封面并确认裁剪'
        store.update(job['id'], 'running', '正文图片已上传，正在设置封面')
        cover = set_cover(page, bundle, state['sources'], state['refs'])
        self.check_body(page, bundle, state['expected'], state['sources'], state['blocks'])
        self.active.update(ready=True, cover=cover)
        self.save_checkpoint()

    def save(self, page, store, job):
        self.step = '保存微信草稿'
        button = page.get_by_role('button', name='保存为草稿', exact=True)
        if button.count() != 1:
            button = page.locator('#js_submit button')
        if button.count() != 1 or not button.is_visible() or not button.is_enabled():
            raise PlatformError('未找到可用的保存草稿按钮，请在专用浏览器检查封面或必填项。')
        store.update(job['id'], 'submitting', '正在请求微信保存草稿')
        button.click()
        try:
            page.wait_for_function('''() => {
                const id=new URL(location.href).searchParams.get('appmsgid');
                const ok=[...document.querySelectorAll('.weui-desktop-toast,.js_tips')].some(e=>e.getBoundingClientRect().height&&/保存成功|已保存/.test(e.textContent));
                return !!id&&ok;
            }''', timeout=20000)
        except Exception:
            raise UncertainError('已点击保存，但未同时取得草稿编号和成功提示。请在微信后台核实；缺少封面时请先补充封面。') from None
        draft_id = parse_qs(urlsplit(page.url).query).get('appmsgid', [''])[0]
        return store.update(job['id'], 'draft_created', '微信页面确认草稿已保存；尚未正式发布', appmsgid=draft_id, screenshot=self.screenshot(job['id']))

    def run(self, bundle, action, store, job):
        self.step = '打开公众号编辑器'
        if action not in ('prepare', 'draft'):
            raise PlatformError('网页模式目前试验自动填入和保存草稿；正式发表请在微信页面核对后完成。')
        page = self.editor(); page.bring_to_front()
        if not (self.active and self.active['page'] == page and self.active['fingerprint'] == bundle['fingerprint']):
            self.restore_checkpoint(page, bundle)
        if self.active and self.active['page'] == page and self.active['fingerprint'] == bundle['fingerprint'] and self.active.get('body_ready'):
            self.step = '核对已填入的正文和图片'
            self.check_body(page, bundle, self.active['expected'], self.active['sources'], self.active['blocks'])
            if self.active['ready']:
                check_cover(page, self.active['cover'])
            else:
                self.finish_cover(page, bundle, store, job)
        else:
            self.fill(page, bundle, store, job)
        if action == 'draft':
            return self.save(page, store, job)
        return store.update(job['id'], 'editor_ready', '标题、作者、摘要、正文图片和封面已填入，文字、段落及图片位置检查通过。请在专用 Edge 核对实际样式和封面裁剪后保存或发表；当前未发布。', screenshot=self.screenshot(job['id']))
