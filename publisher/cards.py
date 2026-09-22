"""Text-only editorial cards. No model call or invented instructional content."""
from pathlib import Path
import re
import uuid
import yaml
from PIL import Image, ImageDraw, ImageFont
from .content import load_bundle


def generate_cards(folder):
    folder=Path(folder);bundle=load_bundle(folder)
    font_path=Path('C:/Windows/Fonts/msyh.ttc')
    if not font_path.exists():raise ValueError('未找到微软雅黑字体，无法生成中文卡片')
    body_font=ImageFont.truetype(str(font_path),40)
    heading=ImageFont.truetype(str(font_path),64)
    label=ImageFont.truetype(str(font_path),25)
    probe=ImageDraw.Draw(Image.new('RGB',(1,1)))
    def wrap(text,font,width):
        lines=[]
        for paragraph in text.splitlines():
            line=''
            for char in paragraph:
                if probe.textlength(line+char,font=font)>width and line:
                    lines.append(line);line=''
                line+=char
            lines.append(line)
        return lines
    # Reserve fixed top/footer regions; split at measured lines without dropping text.
    text=re.sub(r'[\U0001f000-\U0001faff\u2600-\u27bf]+', '•',bundle['xhs_text']).replace('\ufe0f','')
    pages=[];page=[]
    for paragraph in re.split(r'\n\s*\n',text):
        block=wrap(paragraph,body_font,860)
        if page and len(page)+1+len(block)>17:pages.append(page);page=[]
        if page:page.append('')
        for line in block:
            if len(page)==17:pages.append(page);page=[]
            page.append(line)
    if page:pages.append(page)
    pages=pages or [[]]
    if len(pages)>17:raise ValueError('卡片超过 18 张，请先精简小红书短文')
    relative='xiaohongshu/cards-'+uuid.uuid4().hex[:10]
    target=(folder/relative).resolve()
    if not target.is_relative_to(folder.resolve()):raise ValueError('卡片目录超出文章范围')
    target.mkdir(parents=True)
    refs=[]
    total=len(pages)+1
    for index,content in enumerate([None]+pages):
        image=Image.new('RGB',(1080,1440),'#f6f4ec');draw=ImageDraw.Draw(image)
        draw.rectangle((0,0,1080,20),fill='#277969')
        draw.text((90,85),'ZHIZHOU / 阅读笔记',font=label,fill='#527c6e')
        if content is None:
            title_lines=wrap(bundle['xhs_title'],heading,880)
            if len(title_lines)>10:raise ValueError('卡片标题过长')
            y=350
            for line in title_lines:draw.text((90,y),line,font=heading,fill='#1a3530');y+=100
            draw.text((90,1160),'向左滑动，逐页阅读 →',font=body_font,fill='#277969')
        else:
            draw.text((90,175),f'阅读笔记 / {index:02d}',font=heading,fill='#1a3530')
            for n,line in enumerate(content):draw.text((90,310+n*54),line,font=body_font,fill='#263d35')
        draw.line((90,1300,990,1300),fill='#c9d5cb',width=2)
        draw.text((90,1340),'依据本篇短文排版 · 请核对内容后发布',font=label,fill='#527268')
        draw.text((890,1340),f'{index+1:02d}/{total:02d}',font=label,fill='#527268')
        ref=f'{relative}/{index+1:02d}.png';image.save(folder/ref);refs.append(ref)
    article=folder/'article.md';raw=article.read_text(encoding='utf-8-sig')
    match=re.match(r'^---\r?\n(.*?)\r?\n---\r?\n(.*)$',raw,re.S)
    meta=(yaml.safe_load(match[1]) or {}) if match else {}
    meta['xhs_original_images']=bundle['xhs_original_images']
    meta['xhs_card_images']=refs
    meta['xhs_image_mode']='cards'
    meta['xhs_images']=refs
    updated='---\n'+yaml.safe_dump(meta,allow_unicode=True,sort_keys=False)+'---\n'+(match[2] if match else raw)
    temp=article.with_suffix('.tmp');temp.write_text(updated,encoding='utf-8');temp.replace(article)
    return load_bundle(folder)
