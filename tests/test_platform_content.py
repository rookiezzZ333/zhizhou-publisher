import tempfile
import unittest
from pathlib import Path
from PIL import Image
from playwright.sync_api import sync_playwright
from unittest.mock import MagicMock
from publisher.content import load_bundle,render
from publisher.cards import generate_cards
from publisher.douyin import Douyin

class PlatformContentTests(unittest.TestCase):
    def test_cards_keep_wechat_assets_and_snapshot(self):
        import app
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);article=root/'article';article.mkdir();(article/'images').mkdir()
            Image.new('RGB',(20,20),'red').save(article/'images/01.png')
            (article/'article.md').write_text('# 原始长文\n\n正文\n\n![图](images/01.png)',encoding='utf-8')
            (article/'xiaohongshu.md').write_text('# 操作教程\n\n第一步：准备素材。\n\n第二步：核对内容。',encoding='utf-8')
            (article/'douyin.md').write_text('# 抖音独立标题\n\n抖音独立文案',encoding='utf-8')
            before=load_bundle(article);after=generate_cards(article)
            self.assertEqual(before['images'],after['images'])
            self.assertEqual(render(before,lambda r:r),render(after,lambda r:r))
            self.assertNotEqual(after['xhs_images'],after['images'])
            from publisher.metadata import switch_cards
            restored=switch_cards(article,'original')
            self.assertEqual(restored['xhs_images'],before['xhs_images'])
            self.assertEqual(switch_cards(article,'cards')['xhs_images'],after['xhs_images'])
            self.assertEqual(after['douyin_title'],'抖音独立标题')
            self.assertEqual(after['douyin_body'],'抖音独立文案')
            with Image.open(article/after['xhs_images'][0]) as image:self.assertEqual(image.size,(1080,1440))
            old=app.STATE;app.STATE=root/'state'
            try:self.assertEqual(app.snapshot(after,'test')['fingerprint'],after['fingerprint'])
            finally:app.STATE=old

    def test_douyin_fills_without_publishing(self):
        with tempfile.TemporaryDirectory() as name,sync_playwright() as p:
            root=Path(name);Image.new('RGB',(10,10)).save(root/'one.png')
            browser=p.chromium.launch(channel='msedge',headless=True);page=browser.new_page()
            page.route('https://creator.douyin.com/**',lambda route:route.fulfill(content_type='text/html; charset=utf-8',body='''<input type=file accept="image/*" multiple><input placeholder="填写标题"><div contenteditable=true style="min-height:40px"></div><button onclick="window.published=true">发布</button>'''))
            adapter=Douyin(root);adapter.browser=lambda:page;adapter.screenshot=lambda id:None
            store=MagicMock()
            adapter.run({'folder':str(root),'douyin_images':['one.png'],'douyin_title':'测试','douyin_body':'正文'},store,{'id':'test'})
            self.assertEqual(page.get_by_placeholder('填写标题').input_value(),'测试')
            self.assertEqual(page.locator('[contenteditable]').inner_text(),'正文')
            self.assertFalse(page.evaluate('Boolean(window.published)'))
            self.assertEqual(store.update.call_args.args[1],'editor_ready')
            # A second prepare reopens without navigating or replacing edits.
            page.get_by_placeholder('填写标题').fill('用户修改的标题')
            adapter.run({'folder':str(root),'douyin_images':['one.png'],'douyin_title':'测试','douyin_body':'正文'},store,{'id':'test'})
            self.assertEqual(page.get_by_placeholder('填写标题').input_value(),'用户修改的标题')
            browser.close()

    def test_closed_context_relaunches_on_first_call(self):
        from playwright.sync_api import Error
        from publisher.wechat_browser import WeChatBrowser
        for cls in (Douyin,WeChatBrowser):
            adapter=cls(Path('.state'))
            adapter.page=MagicMock();adapter.page.is_closed.return_value=False
            adapter.page.title.side_effect=Error('Target closed')
            stale=MagicMock();stale.close.side_effect=Error('Target closed');adapter.context=stale
            runtime=MagicMock();adapter.runtime=runtime
            page=MagicMock();runtime.chromium.launch_persistent_context.return_value.pages=[page]
            self.assertIs(adapter.browser(),page)
            runtime.chromium.launch_persistent_context.assert_called_once()

    def test_video_upload_uses_video_input_and_independent_copy(self):
        with tempfile.TemporaryDirectory() as name,sync_playwright() as p:
            root=Path(name);(root/'sample.mp4').write_bytes(b'\x00\x00\x00\x18ftypmp42')
            browser=p.chromium.launch(channel='msedge',headless=True);page=browser.new_page()
            page.route('https://creator.douyin.com/**',lambda route:route.fulfill(content_type='text/html; charset=utf-8',body='<input type=file accept="image/*"><input type=file accept="video/*"><input placeholder="填写标题"><div contenteditable=true style="min-height:40px"></div><button onclick="window.published=true">发布</button>'))
            adapter=Douyin(root);adapter.browser=lambda:page;adapter.screenshot=lambda id:None
            adapter.run({'folder':str(root),'douyin_video':'sample.mp4','douyin_video_title':'视频标题','douyin_video_body':'视频说明 #教程'},MagicMock(),{'id':'video'},video=True)
            self.assertEqual(page.locator('input[accept="video/*"]').evaluate('(el)=>el.files.length'),1)
            self.assertEqual(page.locator('input[accept="image/*"]').evaluate('(el)=>el.files.length'),0)
            self.assertEqual(page.get_by_placeholder('填写标题').input_value(),'视频标题')
            self.assertFalse(page.evaluate('Boolean(window.published)'))
            browser.close()
