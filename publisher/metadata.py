import re
import yaml

def update_metadata(folder, values):
    path=folder/'article.md'
    raw=path.read_text(encoding='utf-8-sig')
    match=re.match(r'^---\r?\n(.*?)\r?\n---\r?\n(.*)$',raw,re.S)
    meta=(yaml.safe_load(match[1]) or {}) if match else {}
    meta.update(values)
    temp=folder/'article.md.tmp'
    temp.write_text('---\n'+yaml.safe_dump(meta,allow_unicode=True,sort_keys=False)+'---\n'+(match[2] if match else raw),encoding='utf-8')
    temp.replace(path)

def switch_cards(folder, mode):
    from .content import load_bundle
    bundle=load_bundle(folder)
    if mode not in ('original','cards'):raise ValueError('配图模式无效')
    refs=bundle['xhs_card_images' if mode=='cards' else 'xhs_original_images']
    if not refs:raise ValueError('请先生成文字卡片或添加原始配图')
    update_metadata(folder,{'xhs_images':refs,'xhs_image_mode':mode,
        'xhs_card_images':bundle['xhs_card_images'],'xhs_original_images':bundle['xhs_original_images']})
    return load_bundle(folder)
