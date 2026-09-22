"""Deterministic article bundles. No model service or remote images required."""
import hashlib
import html
import io
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml
from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML
from PIL import Image

IMAGE_TYPES = {'.jpg', '.jpeg', '.png', '.webp'}
MAX_FILE = 12 * 1024 * 1024
MD = MarkdownIt('commonmark', {'html': False, 'breaks': True}).enable('table')


def natural(value):
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r'(\d+)', str(value))]


def inside(root, relative):
    relative = unquote(str(relative)).replace('\\', '/')
    if ':' in relative or relative.startswith('/') or '\x00' in relative:
        raise ValueError('图片必须使用文章文件夹内的相对路径')
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('图片不存在或超出文章目录：' + relative)
    return path


def check_image(path):
    if path.suffix.lower() not in IMAGE_TYPES or path.stat().st_size > MAX_FILE:
        raise ValueError('图片格式不支持或超过 12 MB：' + path.name)
    with Image.open(path) as im:
        if im.width * im.height > 30_000_000:
            raise ValueError('图片超过 3000 万像素：' + path.name)
        im.verify()


def plain(markdown):
    result = []
    for t in MD.parse(markdown):
        if t.type == 'inline':
            result.append(''.join(c.content if c.type in ('text', 'code_inline') else '\n' if c.type in ('softbreak', 'hardbreak') else '' for c in t.children or []))
        elif t.type in ('fence', 'code_block'):
            result.append(t.content)
    return '\n\n'.join(result).strip()


def load_bundle(folder):
    folder = Path(folder).resolve()
    path = folder / 'article.md'
    if not path.is_file() or path.is_symlink():
        raise ValueError('文件夹内需要 article.md')
    if path.stat().st_size > 500_000:
        raise ValueError('article.md 超过 500 KB，请分篇处理')
    raw = path.read_text(encoding='utf-8-sig')
    meta = {}
    if raw.startswith('---\n') or raw.startswith('---\r\n'):
        match = re.match(r'^---\r?\n(.*?)\r?\n---\r?\n(.*)$', raw, re.S)
        if not match:
            raise ValueError('文案开头的配置区缺少结束的 ---')
        meta = yaml.safe_load(match[1]) or {}
        if not isinstance(meta, dict): raise ValueError('文章配置必须是键值形式')
        raw = match[2]
    heading = re.match(r'^\s*# ([^\n]+)\n?', raw)
    title = str(meta.get('title') or (heading[1] if heading else folder.name)).strip()
    body = raw[heading.end():].strip() if heading else raw.strip()
    if not title or not body: raise ValueError('标题和正文不能为空')
    excluded = meta.get('excluded_images') or []
    if not isinstance(excluded,list) or not all(isinstance(x,str) for x in excluded):raise ValueError('excluded_images 必须是图片路径列表')
    images = []
    for dirname in ('images', 'image'):
        directory = folder / dirname
        if directory.is_dir():
            for img in sorted(directory.rglob('*'), key=lambda p: natural(p.relative_to(folder))):
                if img.is_file() and img.suffix.lower() in IMAGE_TYPES:
                    if img.relative_to(folder).as_posix() in excluded:continue
                    safe = inside(folder, img.relative_to(folder).as_posix())
                    check_image(safe)
                    images.append(safe.relative_to(folder).as_posix())
    refs = []
    # Markdown deliberately renders unsafe URL schemes as text. Reject image-like
    # file/remote references explicitly, so the author sees a useful import error.
    for match in re.finditer(r'!\[[^\]]*\]\(([^)\s]+)',body):
        candidate=match[1].strip('<>')
        if urlsplit(candidate).scheme or candidate.startswith('/'):
            raise ValueError('正文图片必须位于文章文件夹内，不支持远程链接或绝对路径')
    for token in MD.parse(body):
        for child in token.children or []:
            if child.type == 'image':
                ref = unquote(child.attrGet('src') or '').replace('\\', '/')
                if ref in excluded:continue
                p = inside(folder, ref)
                check_image(p)
                if ref not in images: images.append(ref)
                refs.append(ref)
    cover = str(meta.get('cover') or (images[0] if images else ''))
    if cover in excluded:cover=images[0] if images else ''
    if cover:
        p = inside(folder, cover); check_image(p)
        if cover not in images: images.insert(0, cover)
    xhsfile = folder / 'xiaohongshu.md'
    xhs_title = str(meta.get('xhs_title') or title)
    xhs_body = str(meta.get('xhs_content') or plain(body))
    if xhsfile.is_file():
        if xhsfile.is_symlink() or xhsfile.stat().st_size > 100_000: raise ValueError('小红书文案文件无效')
        xraw = xhsfile.read_text(encoding='utf-8-sig').strip()
        xhead = re.match(r'^# ([^\n]+)\n?', xraw)
        if xhead: xhs_title, xraw = xhead[1], xraw[xhead.end():]
        xhs_body = plain(xraw)
    tags = meta.get('tags') or []
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags): raise ValueError('tags 必须是文字列表')
    tags = [t.lstrip('#').strip() for t in tags if t.strip()]
    xhs_original = meta.get('xhs_original', False)
    if not isinstance(xhs_original, bool): raise ValueError('xhs_original 必须为 true 或 false')
    xhs_text = xhs_body
    xhs_body += ('\n\n' + ' '.join('#'+t for t in tags)) if tags else ''
    warnings=[]
    if not refs and images: warnings.append('正文没有图片标记：公众号会按编号把图片追加到文末。要指定位置，请写 ![说明](images/01.png)。')
    unused=[p for p in images if p not in refs and p != cover]
    if refs and unused: warnings.append('部分图片没有插入公众号正文；小红书仍会按列表上传全部图片。')
    result=dict(folder=str(folder), name=folder.name, title=title, body=body, author=str(meta.get('author') or ''), digest=str(meta.get('digest') or plain(body)[:110]), cover=cover, images=images, refs=refs, xhs_title=xhs_title, xhs_body=xhs_body, xhs_text=xhs_text, tags=tags, xhs_original=xhs_original, source_url=str(meta.get('source_url') or ''), warnings=warnings)
    for platform, fallback in [('xhs',images),('douyin',images)]:
        chosen=meta.get(platform+'_images',fallback)
        if not isinstance(chosen,list) or not all(isinstance(x,str) for x in chosen):raise ValueError(platform+'_images 必须是图片路径列表')
        for ref in chosen:check_image(inside(folder,ref))
        result[platform+'_images']=chosen
    dyfile=folder/'douyin.md'
    dytext=xhs_text
    dytitle=str(meta.get('douyin_title') or xhs_title)
    if dyfile.exists():
        if dyfile.is_symlink() or dyfile.stat().st_size>100_000:raise ValueError('抖音文案文件无效')
        dyraw=dyfile.read_text(encoding='utf-8-sig').strip()
        head=re.match(r'^# ([^\n]+)\n?',dyraw)
        if head:dytitle,dyraw=head[1],dyraw[head.end():]
        dytext=plain(dyraw)
    result.update(douyin_title=dytitle,douyin_body=dytext,douyin_custom=dyfile.exists())
    is_cards=bool(result['xhs_images']) and all(p.startswith('xiaohongshu/cards-') for p in result['xhs_images'])
    result['xhs_image_mode']=meta.get('xhs_image_mode','cards' if is_cards else 'original')
    result['xhs_original_images']=meta.get('xhs_original_images',images if is_cards else result['xhs_images'])
    result['xhs_card_images']=meta.get('xhs_card_images',result['xhs_images'] if is_cards else [])
    for key in ('xhs_original_images','xhs_card_images'):
        if not isinstance(result[key],list):raise ValueError('小红书配图配置无效')
        for ref in result[key]:check_image(inside(folder,ref))
    video=str(meta.get('douyin_video') or '')
    if video:
        file=inside(folder,video)
        if file.suffix.lower()!='.mp4' or not 12<=file.stat().st_size<=500*1024*1024:raise ValueError('视频需要为 500 MB 以内的 MP4')
        with file.open('rb') as stream:
            if stream.read(12)[4:8]!=b'ftyp':raise ValueError('视频不是有效的 MP4 容器')
    vfile=folder/'douyin-video.md'
    vtitle=str(meta.get('douyin_video_title') or dytitle);vbody=dytext
    if vfile.exists():
        if vfile.is_symlink() or vfile.stat().st_size>100_000:raise ValueError('视频文案文件无效')
        vraw=vfile.read_text(encoding='utf-8-sig').strip();head=re.match(r'^# ([^\n]+)\n?',vraw)
        if head:vtitle,vraw=head[1],vraw[head.end():]
        vbody=plain(vraw)
    result.update(douyin_video=video,douyin_video_title=vtitle,douyin_video_body=vbody,douyin_video_custom=vfile.exists())
    result['assets']=list(dict.fromkeys(images+result['xhs_images']+result['douyin_images']))
    result['assets']=list(dict.fromkeys(result['assets']+result['xhs_original_images']+result['xhs_card_images']+([video] if video else [])))
    digest=hashlib.sha256()
    result['excluded_images']=excluded
    # Includes every platform field plus image content: identical titles are not enough.
    import json
    digest.update(json.dumps({k:v for k,v in result.items() if k not in ('folder','name','warnings')},ensure_ascii=False,sort_keys=True).encode())
    for ref in result['assets']:
        digest.update(ref.encode())
        with inside(folder,ref).open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
    result['fingerprint']=digest.hexdigest()
    return result


STYLES = {
    'p':'margin:18px 0;font-size:16px;line-height:32px;text-align:left;color:#303b4d;',
    'h1':'font-size:25px;line-height:40px;margin:26px 0;text-align:left;color:#19283d;',
    'h2':'font-size:21px;line-height:36px;margin:30px 0 14px;text-align:left;color:#3559af;',
    'h3':'font-size:18px;line-height:32px;margin:24px 0 12px;text-align:left;color:#3559af;',
    'blockquote':'margin:20px 0;padding:10px 16px;font-size:16px;line-height:32px;border-left:4px solid #809cdc;background:#f3f6fc;',
    'img':'max-width:100%;height:auto;display:block;margin:20px auto;',
    'li':'margin:8px 0;line-height:32px;font-size:16px;text-align:left;',
    'pre':'padding:14px;background:#f3f5f8;white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;line-height:26px;',
    'code':'background:#f3f5f8;',
    'table':'border-collapse:collapse;width:100%;table-layout:fixed;font-size:14px;line-height:28px;',
    'th':'padding:8px;word-break:break-word;border:1px solid #dce3ed;background:#eef3fb;',
    'td':'padding:8px;word-break:break-word;border:1px solid #dce3ed;',
    'a':'color:#3559af;text-decoration:underline;overflow-wrap:anywhere;',
}


def render(bundle, image_url):
    tokens=MD.parse(bundle['body'])
    def decorate(t):
        if t.tag in STYLES: t.attrSet('style',STYLES[t.tag])
        if t.type=='image':
            ref=unquote(t.attrGet('src') or '').replace('\\','/')
            if ref in bundle.get('excluded_images',[]):
                t.type='html_inline';t.tag='';t.content='';t.children=None
                return
            t.attrSet('src',image_url(ref))
        if t.type=='link_open':
            href=t.attrGet('href') or ''
            if urlsplit(href).scheme not in ('http','https','mailto'):t.attrSet('href','#')
        for child in t.children or []:decorate(child)
    for t in tokens:decorate(t)
    # Explicit inline text leaves match WeChat's documented span[leaf] model.
    # Keep mixed bold/link text inside leaves instead of mixing raw text and
    # inline element boxes directly under a paragraph. Never wrap block nodes.
    renderer = RendererHTML()
    renderer.rules['text'] = lambda ts, i, options, env: '<span leaf="">' + html.escape(ts[i].content, quote=False) + '</span>'
    out=renderer.render(tokens,MD.options,{})
    if not bundle['refs']:
        out+=''.join(f'<p><img src="{html.escape(image_url(ref),quote=True)}" alt="文章配图" style="{STYLES["img"]}"></p>' for ref in bundle['images'])
    return '<section style="font-size:16px;line-height:32px;text-align:left;overflow-wrap:anywhere;">'+out+'</section>'


def wx_image(path):
    """Normalize user-selected images for the platform, without changing originals."""
    from PIL import ImageOps
    with Image.open(path) as original:
        image=ImageOps.exif_transpose(original).convert('RGB')
        image.thumbnail((1920,1920))
        for quality in (92,85,75,65):
            data=io.BytesIO();image.save(data,format='JPEG',quality=quality)
            if data.tell()<1_000_000:return data.getvalue()
    raise ValueError('图片压缩后仍过大，请缩小后重试')
