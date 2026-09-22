import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from playwright.sync_api import sync_playwright, TimeoutError

import app
from publisher.wechat_browser import BODY_STATE, WeChatBrowser
from publisher.wechat import PlatformError
from publisher.content import load_bundle
from publisher.wechat_editor import resolve_field, read_field, write_field, check_document, check_cover, wait_for_paste
from PIL import Image
import io


class EditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = sync_playwright().start()
        cls.browser = cls.runtime.chromium.launch(channel='msedge', headless=True)
        cls.page = cls.browser.new_page()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()

    def empty(self, markup):
        self.page.set_content('<div id="editor" contenteditable="true">' + markup + '</div>')
        editor = self.page.locator('#editor')
        before = editor.inner_html()
        result = editor.evaluate(BODY_STATE)['empty']
        self.assertEqual(editor.inner_html(), before)
        return result

    def test_empty_editor_decorations(self):
        for markup in ['', '<p><br></p>', '<p>\u200b\ufeff</p>',
                       '<p class="placeholder">从这里开始写正文</p>',
                       '<span contenteditable="false">请输入正文</span><p><br></p>',
                       '<p data-placeholder="从这里开始写正文"><br></p>',
                       '<img class="ProseMirror-separator">']:
            with self.subTest(markup=markup):
                self.assertTrue(self.empty(markup))

    def test_real_text_and_media_remain_protected(self):
        for markup in ['<p>正文</p>', '<p>从这里开始写正文</p>',
                       '<p class="placeholder">已有正文</p>',
                       '<span contenteditable="false">请输入正文</span><p>文章</p>',
                       '<img src="data:,">', '<table><tr><td></td></tr></table>',
                       '<video></video>', '<hr>', '<svg></svg>']:
            with self.subTest(markup=markup):
                self.assertFalse(self.empty(markup))

    def test_visible_placeholder_field_over_hidden_legacy_id(self):
        self.page.set_content('<input id="title" hidden><textarea placeholder="请在这里输入标题"></textarea>')
        target = resolve_field(self.page, 'title')
        write_field(target, '正确标题', 'title')
        self.assertEqual(read_field(target), '正确标题')
        self.assertEqual(self.page.locator('#title').input_value(), '')

    def test_editable_title_and_ambiguous_fields(self):
        self.page.set_content('<div contenteditable="true" data-placeholder="请输入标题"></div>')
        target = resolve_field(self.page, 'title')
        write_field(target, '富文本标题', 'title')
        self.assertEqual(read_field(target), '富文本标题')
        self.page.set_content('<input placeholder="标题"><input placeholder="标题">')
        with self.assertRaises(PlatformError):
            resolve_field(self.page, 'title')

    def test_prosemirror_title_ignores_caret_break_not_authored_text(self):
        self.page.set_content('<div contenteditable="true" data-placeholder="标题"><p>Gemini 4 标题<br class="ProseMirror-trailingBreak"></p></div>')
        target = resolve_field(self.page, 'title')
        self.assertNotEqual(target.inner_text(), 'Gemini 4 标题')
        self.assertEqual(read_field(target), 'Gemini 4 标题')
        target.evaluate('el=>el.innerHTML="<p>第一行</p><p>第二行</p>"')
        self.assertEqual(read_field(target), '第一行\n第二行')
        target.evaluate('el=>el.innerHTML="<p>标题 <br>换行<br class=ProseMirror-trailingBreak></p>"')
        self.assertEqual(read_field(target), '标题 \n换行')

    def test_prosemirror_field_write_does_not_send_tab(self):
        self.page.set_content((Path(__file__).parent / 'fixtures/wechat_editor.html').read_text(encoding='utf-8'))
        title = resolve_field(self.page, 'title')
        write_field(title, 'Gemini 4 标题', 'title')
        self.assertEqual(read_field(title), 'Gemini 4 标题')
        self.assertNotIn('\t', title.text_content())
        self.assertEqual(title.locator('p').count(), 1)

    def test_waits_for_asynchronous_paste(self):
        self.page.set_content('<div id="body" contenteditable="true"></div>')
        self.page.evaluate('''() => setTimeout(()=>{
            document.querySelector('#body').innerHTML='<p>第一段</p><p>第二段</p>';
        },200)''')
        wait_for_paste(self.page, self.page.locator('#body'), '第一段第二段', ['第一段', '第二段'], [], timeout=2)

    def test_structure_warning_never_clicks_continue(self):
        self.page.set_content('<div id="body" contenteditable="true"></div><div role="dialog"><h2>内容结构检测</h2><button onclick="window.continued=true">继续插入</button></div>')
        with self.assertRaisesRegex(PlatformError, '内容结构检测'):
            wait_for_paste(self.page, self.page.locator('#body'), '正文', ['正文'], [], timeout=1)
        self.assertFalse(self.page.evaluate('Boolean(window.continued)'))

    def test_waiting_does_not_accept_partial_content(self):
        self.page.set_content('<div id="body" contenteditable="true"><p>第一段</p></div>')
        with self.assertRaisesRegex(PlatformError, '超时'):
            wait_for_paste(self.page, self.page.locator('#body'), '第一段第二段', ['第一段', '第二段'], [], timeout=.1)

    def test_nonempty_title_blocks_insertion(self):
        self.page.set_content('<input placeholder="标题" value="已有标题"><input placeholder="作者"><textarea placeholder="摘要"></textarea><div class="rich_media_content"><div class="ProseMirror" contenteditable="true"></div></div>')
        with tempfile.TemporaryDirectory() as folder:
            adapter = WeChatBrowser(Path(folder))
            with self.assertRaises(PlatformError):
                adapter.fill(self.page, {'title': '新标题', 'author': '作者', 'digest': '摘要'}, MagicMock(), {'id': 'test'})
            self.assertIsNone(adapter.active)
        self.assertEqual(self.page.get_by_placeholder('标题').input_value(), '已有标题')

    def test_missing_field_fails_before_changing_body_or_title(self):
        self.page.set_content('<input placeholder="标题"><div class="rich_media_content"><div class="ProseMirror" contenteditable="true"></div></div>')
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(PlatformError):
                WeChatBrowser(Path(folder)).fill(self.page, {'title': '新标题', 'author': '作者', 'digest': '摘要'}, MagicMock(), {'id': 'test'})
        self.assertEqual(self.page.get_by_placeholder('标题').input_value(), '')
        self.assertEqual(self.page.locator('.ProseMirror').inner_text(), '')

    def test_full_fill_with_repeated_images_and_nonfirst_cover(self):
        self.page.set_content((Path(__file__).parent / 'fixtures/wechat_editor.html').read_text(encoding='utf-8'))
        png = io.BytesIO();Image.new('RGB', (20, 20), 'blue').save(png, format='PNG')
        self.page.route('https://mmbiz.qpic.cn/**', lambda route: route.fulfill(body=png.getvalue(), content_type='image/png'))
        try:
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder);(root / 'images').mkdir()
                for name in ('one.png', 'two.png'):
                    (root / 'images' / name).write_bytes(png.getvalue())
                (root / 'article.md').write_text('---\ntitle: 测试文章\nauthor: 辞忧公子的杂货铺\ncover: images/two.png\n---\n\n第一段 **重点**。\n\n![一](images/one.png)\n\n## 小标题\n\n第二段。\n\n![二](images/two.png)\n\n![重复](images/one.png)\n\n结尾。', encoding='utf-8')
                bundle = load_bundle(root);adapter = WeChatBrowser(root)
                # Resume the observed failure state: title filled, body empty.
                write_field(resolve_field(self.page, 'title'), bundle['title'], 'title')
                # Cover failure leaves a verified body checkpoint. Retrying
                # must choose the cover without pasting or uploading again.
                with patch('publisher.wechat_browser.set_cover', side_effect=PlatformError('封面未完成')):
                    with self.assertRaisesRegex(PlatformError, '封面未完成'):
                        adapter.fill(self.page, bundle, MagicMock(), {'id': 'test'})
                self.assertFalse(adapter.active['ready'])
                # Simulate restarting the application after losing the browser
                # connection. The persisted checkpoint must be revalidated.
                adapter = WeChatBrowser(root)
                body = self.page.locator('.rich_media_content .ProseMirror')
                original = body.inner_html()
                body.evaluate('el=>el.append("人工修改")')
                self.assertFalse(adapter.restore_checkpoint(self.page, bundle))
                body.evaluate('(el,html)=>el.innerHTML=html', original)
                self.page.wait_for_function('''() => [...document.querySelectorAll('.rich_media_content img')].every(i=>i.complete&&i.naturalWidth)''')
                with patch.object(adapter, 'editor', return_value=self.page), patch.object(adapter, 'screenshot', return_value=None):
                    adapter.run(bundle, 'prepare', MagicMock(), {'id': 'retry'})
                self.assertTrue(adapter.active['ready'])
                self.assertEqual(read_field(resolve_field(self.page, 'title')), '测试文章')
                self.assertEqual(read_field(resolve_field(self.page, 'author')), '辞忧公子的杂货铺')
                self.assertEqual(self.page.evaluate('uploads'), ['one.png', 'two.png', 'one.png'])
                self.assertEqual(self.page.locator('.ProseMirror h2').inner_text(), '小标题')
                self.assertEqual(self.page.locator('.ProseMirror strong').inner_text(), '重点')
                self.assertNotIn('ZZIMAGE', self.page.locator('.rich_media_content .ProseMirror').inner_text())
                self.assertIn('/two.png/', self.page.locator('#js_cover_area img').get_attribute('src'))
                recovered = WeChatBrowser(root)
                self.assertTrue(recovered.restore_checkpoint(self.page, bundle))
                self.assertTrue(recovered.active['ready'])
                self.page.locator('#js_cover_area').evaluate('el=>el.replaceChildren()')
                with self.assertRaisesRegex(PlatformError, '封面'):
                    check_cover(self.page, adapter.active['cover'])
        finally:
            self.page.unroute('https://mmbiz.qpic.cn/**')

    def test_cover_failure_does_not_mark_editor_ready(self):
        self.page.set_content((Path(__file__).parent / 'fixtures/wechat_editor.html').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'article.md').write_text('# 测试\n\n正文。', encoding='utf-8')
            adapter = WeChatBrowser(root)
            with patch('publisher.wechat_browser.set_cover', side_effect=PlatformError('封面未完成')):
                with self.assertRaisesRegex(PlatformError, '封面未完成'):
                    adapter.fill(self.page, load_bundle(root), MagicMock(), {'id': 'test'})
            self.assertFalse(adapter.active['ready'])


class BrowserFailureTests(unittest.TestCase):
    def test_closed_editor_reuses_remaining_browser_tab(self):
        with tempfile.TemporaryDirectory() as folder:
            adapter = WeChatBrowser(Path(folder))
            adapter.page = MagicMock()
            adapter.page.is_closed.return_value = True
            live = MagicMock(); live.is_closed.return_value = False
            adapter.context = MagicMock(); adapter.context.pages = [live]
            self.assertIs(adapter.browser(), live)
            adapter.context.close.assert_not_called()

    def test_flattened_paragraphs_are_not_accepted(self):
        with self.assertRaisesRegex(PlatformError, '段落'):
            check_document({'text': '第一段第二段', 'blocks': ['第一段第二段'], 'images': []}, '第一段第二段', ['第一段', '第二段'])

    def test_swapped_images_and_missing_markers_are_not_accepted(self):
        with self.assertRaises(PlatformError):
            check_document({'text': '正文', 'blocks': ['正文'], 'images': []}, '正文ZZSLOT0END', ['正文', 'ZZSLOT0END'], sources=['https://mmbiz.qpic.cn/a/0'])
        with self.assertRaises(PlatformError):
            check_document({'text': 'ZZSLOT0END正文ZZSLOT1END', 'blocks': ['正文'], 'images': [{'src': 'b', 'ok': True}, {'src': 'a', 'ok': True}]}, 'ZZSLOT0END正文ZZSLOT1END', ['正文'], sources=['a', 'b'])

    def test_interruption_records_stage_without_raw_error(self):
        for status, expected in [('running', 'failed'), ('submitting', 'uncertain')]:
            with self.subTest(status=status), tempfile.TemporaryDirectory() as folder:
                adapter = WeChatBrowser(Path(folder))
                adapter.step = '上传正文图片 1/2'
                adapter.run = MagicMock(side_effect=TimeoutError('private-url-token'))
                adapter.screenshot = MagicMock(return_value=None)
                store = MagicMock()
                store.get.return_value = {'status': status}
                with patch.object(app, 'WX_BROWSER', adapter), patch.object(app, 'STORE', store):
                    app.run_job({}, 'wechat_browser', 'prepare', {'id': 'test'}, {})
                args = store.update.call_args.args
                self.assertEqual(args[1], expected)
                self.assertIn('上传正文图片 1/2', args[2])
                self.assertIn('TimeoutError', args[2])
                self.assertNotIn('private-url-token', args[2])


if __name__ == '__main__':
    unittest.main()
