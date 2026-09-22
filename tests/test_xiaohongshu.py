import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock
from PIL import Image
from playwright.sync_api import TimeoutError
from publisher.content import load_bundle
from publisher.state import Store
from publisher.xiaohongshu import Xiaohongshu,LoginNeeded,URL
from publisher.wechat import PlatformError,UncertainError


class XhsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.folder=self.root/'article';self.folder.mkdir();(self.folder/'images').mkdir()
        for name in ['2.png','01.png']:Image.new('RGB',(10,10),'red').save(self.folder/'images'/name)
        (self.folder/'article.md').write_text('# 测试标题\n\n正文',encoding='utf-8');self.bundle=load_bundle(self.folder)
        self.store=Store(self.root/'state');self.job,_=self.store.begin(self.bundle,'xiaohongshu','publish','test-profile')
        self.page=MagicMock();self.page.url=URL
        self.title=MagicMock();self.title.input_value.return_value=self.bundle['xhs_title']
        self.editor=MagicMock();self.editor.inner_text.return_value=self.bundle['xhs_body']
        self.button=MagicMock();self.button.count.return_value=1;self.button.is_visible.return_value=True;self.button.is_enabled.return_value=True
        self.page.get_by_role.return_value=self.button
        self.upload=MagicMock();self.upload.get_attribute.side_effect=lambda name: 'image/*' if name=='accept' else None
        self.inputs=MagicMock();self.inputs.count.return_value=1;self.inputs.nth.return_value=self.upload
        self.success=MagicMock();self.success.inner_text.return_value='发布成功'
        def locator(s):
            if s=='input[type="file"]':return self.inputs
            return MagicMock()
        self.page.locator.side_effect=locator
        def by_text(text,**kw):
            obj=MagicMock()
            if isinstance(text,str):return obj
            if text.pattern.startswith('^'):obj.first=self.success
            else:obj.first.is_visible.return_value=False
            return obj
        self.page.get_by_text.side_effect=by_text
        self.xhs=Xiaohongshu(self.root/'state');self.xhs.browser=lambda:self.page;self.xhs.page=self.page;self.xhs.screenshot=lambda id:None
        self.xhs.open_image_mode=lambda page:self.upload
        self.xhs.image_inputs=lambda page:[self.upload]
        self.xhs.first_visible=lambda p,selectors:self.title if selectors[0].startswith('input') else self.editor
    def tearDown(self):self.tmp.cleanup()
    def test_upload_order_and_explicit_success(self):
        result=self.xhs.run(self.bundle,self.store,self.job)
        paths=[Path(c.args[0]).name for c in self.upload.set_input_files.call_args_list]
        self.assertEqual(paths,['01.png','2.png']);self.button.click.assert_called_once()
        self.assertEqual(result['status'],'platform_confirmed')
    def test_no_success_signal_is_uncertain_not_success(self):
        self.success.wait_for.side_effect=TimeoutError('no success')
        with self.assertRaises(UncertainError):self.xhs.run(self.bundle,self.store,self.job)
        self.button.click.assert_called_once()
    def test_prepare_fills_content_without_publishing(self):
        result=self.xhs.run(self.bundle,self.store,self.job,action='prepare')
        self.assertEqual(result['status'],'editor_ready')
        self.title.fill.assert_called_once_with(self.bundle['xhs_title'])
        self.editor.fill.assert_called_once_with(self.bundle['xhs_body'])
        self.button.click.assert_not_called()
    def test_multiple_upload_submits_all_files_in_order(self):
        self.upload.get_attribute.side_effect=lambda name: '' if name=='multiple' else 'image/*'
        self.xhs.run(self.bundle,self.store,self.job,action='prepare')
        self.upload.set_input_files.assert_called_once()
        paths=self.upload.set_input_files.call_args.args[0]
        self.assertEqual([Path(p).name for p in paths],['01.png','2.png'])
    def test_login_required_never_clicks_publish(self):
        self.page.url='https://creator.xiaohongshu.com/login'
        with self.assertRaises(LoginNeeded):self.xhs.run(self.bundle,self.store,self.job)
        self.button.click.assert_not_called()
    def test_changed_editor_never_clicks_publish(self):
        self.editor.inner_text.return_value='平台上的错误内容'
        with self.assertRaises(PlatformError):self.xhs.run(self.bundle,self.store,self.job)
        self.button.click.assert_not_called()


class ImageModeTests(unittest.TestCase):
    def test_topics_and_original_confirmation(self):
        from playwright.sync_api import sync_playwright
        with tempfile.TemporaryDirectory() as folder, sync_playwright() as runtime:
            browser=runtime.chromium.launch(channel='msedge',headless=True)
            page=browser.new_page()
            page.set_content('''<div contenteditable=true id=editor>正文</div>
                <div id=creator-editor-topic-container hidden><div class=item onclick="window.topicSelected=true;this.parentElement.hidden=true"><span>Gemini</span><small>讨论</small></div></div>
                <div class=custom-switch-card>原创声明<div class=d-switch onclick="document.querySelector('.footer').hidden=false">开关<input type=checkbox hidden></div></div>
                <div class=footer hidden>原创声明须知<label class=d-checkbox><input type=checkbox>同意</label><button onclick="document.querySelector('.d-switch input').checked=true;this.parentElement.hidden=true">声明原创</button></div>
                <script>document.querySelector('#editor').oninput=()=>{document.querySelector('#creator-editor-topic-container').hidden=!document.querySelector('#editor').textContent.includes('#Gemini')};</script>''')
            adapter=Xiaohongshu(Path(folder))
            adapter.fill_topics(page,page.locator('#editor'),['Gemini'])
            self.assertTrue(page.evaluate('window.topicSelected'))
            adapter.set_original(page)
            self.assertTrue(page.locator('.d-switch input').is_checked())
            self.assertTrue(page.locator('.footer input').is_checked())
            browser.close()

    def test_hidden_duplicate_and_delayed_upload_input(self):
        from playwright.sync_api import sync_playwright
        with tempfile.TemporaryDirectory() as folder, sync_playwright() as runtime:
            browser=runtime.chromium.launch(channel='msedge',headless=True)
            page=browser.new_page()
            page.set_content('''<div hidden>上传图文</div>
                <aside><button onclick="window.wrongClick=true">上传图文</button></aside>
                <nav><span>上传视频</span>
                <button onclick="setTimeout(()=>{document.querySelector('#image').disabled=false},200)">上传图文</button>
                <span>写长文</span><span>发播客</span></nav>
                <nav style="position:absolute;left:-10000px"><span>上传视频</span><button>上传图文</button><span>写长文</span></nav>
                <input type=file accept="video/*">
                <input id=image type=file accept="image/png,image/jpeg" disabled hidden>''')
            adapter=Xiaohongshu(Path(folder))
            target=adapter.open_image_mode(page)
            self.assertEqual(target.get_attribute('id'),'image')
            self.assertFalse(page.evaluate('Boolean(window.wrongClick)'))
            self.assertEqual(len(adapter.image_inputs(page)),1)
            page.locator('#image').evaluate('el=>el.insertAdjacentHTML("afterend",el.outerHTML)')
            with self.assertRaisesRegex(PlatformError,'不唯一'):
                adapter.open_image_mode(page)
            browser.close()


if __name__=='__main__':unittest.main()
