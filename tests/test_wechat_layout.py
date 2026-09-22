"""Optional integration with the upstream layout engine, beyond DOM mocks."""
import unittest
from pathlib import Path
from playwright.sync_api import sync_playwright
from publisher.content import render

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / '.state/qa-tools/verify-article-structure-spec/cli/dist/verify-article-structure.browser.js'


@unittest.skipUnless(ENGINE.exists(), 'Optional upstream layout engine is not built')
class WeChatLayoutTests(unittest.TestCase):
    def test_mixed_inline_text_against_upstream_detector(self):
        body = ('普通文字和 **加粗强调的长句子需要能够自动换行而不会丢失正文结构** 混排，'
                '还有 [资料链接](https://example.com) 以及 `inline_code`。\n\n') * 3
        body += '## 小标题\n\n> 引用文字与 **强调** 混排。\n\n- 第一项 **重点**\n- 第二项普通文字\n'
        markup = render({'body': body, 'refs': [], 'images': []}, lambda ref: ref)
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page()
            page.add_script_tag(path=str(ENGINE))
            result = page.evaluate('''async markup=>{
                const root=document.createElement('div');root.innerHTML=markup;
                return await VerifyArticleStructure.verifyArticleStructure(root);
            }''', markup)
            browser.close()
        self.assertTrue(result['isValid'], result.get('inValidInfo'))


if __name__ == '__main__':
    unittest.main()
