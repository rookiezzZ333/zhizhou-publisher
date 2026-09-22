"""Public editor controls and rich clipboard content; no private platform API."""
import re

from .wechat import PlatformError


FIELD_SELECTORS = {
    'title': '#title, input[placeholder*="标题"], textarea[placeholder*="标题"], [contenteditable="true"][placeholder*="标题"], [contenteditable="true"][data-placeholder*="标题"], [contenteditable="true"][aria-label*="标题"]',
    'author': '#author, input[placeholder*="作者"], textarea[placeholder*="作者"], [contenteditable="true"][placeholder*="作者"], [contenteditable="true"][data-placeholder*="作者"], [contenteditable="true"][aria-label*="作者"]',
    'digest': '#js_description, textarea[placeholder*="摘要"], textarea[placeholder*="不填写则默认"], [contenteditable="true"][data-placeholder*="摘要"], [contenteditable="true"][placeholder*="摘要"]',
}
FIELD_NAMES = {'title': '标题', 'author': '作者', 'digest': '摘要'}


def resolve_field(page, name, required=True):
    candidates = page.locator(FIELD_SELECTORS[name])
    visible = [candidates.nth(i) for i in range(candidates.count())
               if candidates.nth(i).is_visible()
               and candidates.nth(i).evaluate('el => el.matches("input,textarea,[contenteditable=true]")')
               and candidates.nth(i).is_editable()]
    if len(visible) == 1:
        return visible[0]
    if required or len(visible) > 1:
        raise PlatformError(f'未找到唯一可编辑的{FIELD_NAMES[name]}栏（找到 {len(visible)} 个）。正文尚未继续填入，请保留页面以便适配。')
    return None


def read_field(field):
    return field.evaluate(r'''el => {
        if ('value' in el) return el.value;
        // innerText includes layout-generated line breaks for ProseMirror's
        // paragraph wrappers and trailing caret BR. They are not title text.
        const copy=el.cloneNode(true);
        copy.querySelectorAll('br.ProseMirror-trailingBreak,img.ProseMirror-separator').forEach(n=>n.remove());
        copy.querySelectorAll('br').forEach(n=>n.replaceWith(document.createTextNode('\n')));
        const blocks=copy.querySelectorAll('p');
        if(blocks.length)return [...blocks].map(n=>n.textContent).join('\n');
        return copy.textContent;
    }''') if field is not None else ''


def write_field(field, value, name):
    if field is None:
        if value:
            raise PlatformError('缺少' + FIELD_NAMES[name] + '输入栏。')
        return
    field.fill(value)
    # Tab is an editor key, not a reliable blur: rich editors may intercept it.
    field.evaluate('el => el.blur()')
    import time
    deadline = time.monotonic() + 3
    while True:
        actual = read_field(field)
        if actual == value:
            return
        if time.monotonic() >= deadline:
            raise PlatformError(f'{FIELD_NAMES[name]}校验未通过（期望 {len(value)} 字，读到 {len(actual)} 字），已停止。')
        field.page.wait_for_timeout(100)


# A paste event lets ProseMirror parse HTML through its clipboard pipeline.
# insertHTML mutates its DOM outside that pipeline and can flatten all paragraphs.
PASTE_HTML = r'''(el, markup) => {
    el.focus();
    const range=document.createRange();range.selectNodeContents(el);
    const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
    const wrapper=document.createElement('div');wrapper.innerHTML=markup;
    const data=new DataTransfer();data.setData('text/html',markup);
    data.setData('text/plain',wrapper.innerText||wrapper.textContent);
    const event=new ClipboardEvent('paste',{bubbles:true,cancelable:true,clipboardData:data});
    el.dispatchEvent(event);
    return event.defaultPrevented;
}'''

DOCUMENT_STATE = r'''el => {
    const copy=el.cloneNode(true);
    copy.querySelectorAll('img.ProseMirror-separator').forEach(n=>n.remove());
    copy.querySelectorAll('img').forEach((n,i)=>n.replaceWith(document.createTextNode('ZZSLOT'+i+'END')));
    const blocks=[...copy.querySelectorAll('p,h1,h2,h3,h4,h5,h6,li,pre,td,th')]
        .filter(n=>!n.querySelector('p,h1,h2,h3,h4,h5,h6,li,pre,td,th'))
        .map(n=>n.textContent).filter(t=>t.trim());
    return {text:copy.textContent,blocks,
        images:[...el.querySelectorAll('img:not(.ProseMirror-separator)')]
            .map(i=>({src:i.src,ok:i.complete&&i.naturalWidth>0}))};
}'''

EXPECTED_BLOCKS = r'''markup => {
    const root=document.createElement('div');root.innerHTML=markup;
    return [...root.querySelectorAll('p,h1,h2,h3,h4,h5,h6,li,pre,td,th')]
        .filter(n=>!n.querySelector('p,h1,h2,h3,h4,h5,h6,li,pre,td,th'))
        .map(n=>n.textContent).filter(t=>t.trim());
}'''


def canonical(text):
    return re.sub(r'\s+|[\u200b\ufeff]', '', text)


def check_document(state, expected, blocks, refs=(), sources=None):
    def clean(text):
        for index, (marker, _) in enumerate(refs):
            text = text.replace(marker, f'ZZSLOT{index}END')
        return canonical(text)
    if clean(state['text']) != canonical(expected):
        raise PlatformError('正文文字或图片位置与原文不一致，已停止保存。')
    def text_blocks(items):
        # The platform may wrap images in figures instead of paragraphs. Check
        # image positions in full text above; compare textual blocks separately.
        result = [re.sub(r'ZZSLOT\d+END', '', clean(t)) for t in items]
        return [t for t in result if t]
    if text_blocks(state['blocks']) != text_blocks(blocks):
        raise PlatformError('正文段落／小标题结构发生变化，已停止，不能把连续文本当成排版成功。')
    if sources is not None:
        if [i['src'] for i in state['images']] != sources or not all(i['ok'] for i in state['images']):
            raise PlatformError('图片数量、顺序或加载状态不一致，已停止保存。')


def wait_for_paste(page, editor, expected, blocks, refs, timeout=20):
    """The platform can validate pasted HTML asynchronously before inserting it."""
    import time
    deadline = time.monotonic() + timeout
    last_error = None
    while True:
        warning = page.get_by_text('内容结构检测', exact=True)
        if any(warning.nth(i).is_visible() for i in range(warning.count())):
            raise PlatformError('微信弹出了「内容结构检测」，正文尚未完成插入；已停止图片上传。请点击弹窗的取消，保留现场诊断以便修复排版，不要将预览当作已填入正文。')
        try:
            check_document(editor.evaluate(DOCUMENT_STATE), expected, blocks, refs=refs)
            return
        except PlatformError as exc:
            last_error = exc
        if time.monotonic() >= deadline:
            raise PlatformError('等待微信完成正文粘贴超时：' + str(last_error))
        page.wait_for_timeout(100)


def one_visible(locator, label, timeout=10):
    import time
    deadline = time.monotonic() + timeout
    while True:
        choices = [locator.nth(i) for i in range(locator.count()) if locator.nth(i).is_visible()]
        if choices or time.monotonic() >= deadline:
            break
        locator.page.wait_for_timeout(100)
    if len(choices) != 1:
        raise PlatformError('未找到唯一的' + label + '，请保留页面以便适配。')
    return choices[0]


def cover_dialog(page):
    # Count only innermost containers when the skin nests both selectors.
    return one_visible(page.locator(
        '[role="dialog"]:visible:not(:has(.weui-desktop-dialog:visible)), '
        '.weui-desktop-dialog:visible:not(:has([role="dialog"]:visible))'), '封面对话框')


def image_identity(url):
    # WeChat thumbnails and cropped images may change the last size component.
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    return parts.hostname, parts.path.rsplit('/', 1)[0]


COVER_STATE = r'''() => {
    const area=document.querySelector('#js_cover_area');
    if(!area)return [];
    const visible=n=>n.getBoundingClientRect().width>0&&n.getBoundingClientRect().height>0;
    const images=[...area.querySelectorAll('img')].filter(i=>visible(i)&&i.complete&&i.naturalWidth>0&&/^https:\/\/[^/]*qpic\.cn\//.test(i.src)).map(i=>i.src);
    const backgrounds=[area,...area.querySelectorAll('*')].filter(visible).map(n=>getComputedStyle(n).backgroundImage).filter(s=>/https:.*qpic\.cn/.test(s));
    return [...new Set([...images,...backgrounds])].sort();
}'''


def check_cover(page, expected):
    if not expected or page.evaluate(COVER_STATE) != expected:
        raise PlatformError('封面丢失或已被更改，请核对封面后再保存。')


def set_cover(page, bundle, sources, refs):
    """Choose this bundle's exact inline image, or upload its explicit cover file."""
    from pathlib import Path
    from .content import inside
    area = one_visible(page.locator('#js_cover_area'), '封面区域')
    trigger = one_visible(area.get_by_text('拖拽或选择封面', exact=True), '选择封面入口')
    cover_sources = [sources[i] for i, (_, ref) in enumerate(refs) if ref == bundle['cover']]
    menu = page.get_by_text(re.compile(r'^\s*从正文(?:中)?选择\s*$') if cover_sources
                            else re.compile(r'^\s*(从本地选择|本地上传|上传封面)\s*$'))
    # Some editor skins reveal the menu on hover. Clicking that same trigger
    # can dismiss it again. Inspect after hovering before trying a click.
    trigger.hover()
    try:
        entry = one_visible(menu, '封面选择菜单', timeout=1)
    except PlatformError:
        trigger.click()
        entry = one_visible(menu, '封面选择菜单')
    if cover_sources:
        entry.click()
        dialog = cover_dialog(page)
        candidates = dialog.locator('img')
        candidates.first.wait_for(state='visible', timeout=15000)
        matches = [candidates.nth(i) for i in range(candidates.count())
                   if candidates.nth(i).is_visible()
                   and image_identity(candidates.nth(i).get_attribute('src') or '') == image_identity(cover_sources[0])]
        if not matches:
            raise PlatformError('封面选择窗口中未找到本篇指定的封面图片，已停止。')
        matches[0].click()
    else:
        with page.expect_file_chooser(timeout=10000) as chooser:
            entry.click()
        chooser.value.set_files(str(inside(Path(bundle['folder']), bundle['cover'])))
    # Selection and cropping mount asynchronously. A gap without a dialog
    # does not mean completion; only the cover thumbnail confirms completion.
    import time
    deadline = time.monotonic() + 45
    previous = None
    label = None
    while time.monotonic() < deadline:
        dialogs = page.locator('[role="dialog"]:visible, .weui-desktop-dialog:visible')
        if dialogs.count() == 0:
            cover = page.evaluate(COVER_STATE)
            if cover:
                return cover
            page.wait_for_timeout(100)
            continue
        if previous is not None and previous.evaluate('''(node,label) => node.isConnected &&
                !!node.getBoundingClientRect().height && node.textContent.trim()===label''', label):
            page.wait_for_timeout(100)
            continue
        dialog = cover_dialog(page)
        button = one_visible(dialog.locator('button, a, [role="button"]').filter(
            has_text=re.compile(r'^\s*(下一步|确定|确认|完成)\s*$')), '封面确认按钮')
        previous = button.element_handle()
        label = button.inner_text().strip()
        button.click()
    raise PlatformError('封面选择或裁剪尚未完成，已停止保存。请保留当前窗口后重试。')
