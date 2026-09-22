"""Optional QA using wechatjs/verify-article-structure-spec's browser engine.

Build its cli/build-browser.mjs under .state/qa-tools first. Runs only on local
HTML in an isolated headless Edge, never on the signed-in publishing profile.
"""
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / '.state/qa-tools/verify-article-structure-spec/cli/dist/verify-article-structure.browser.js'


def main():
    if not ENGINE.is_file():
        raise SystemExit('Build the optional wechatjs QA engine first; see TESTING.md.')
    failed = False
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(viewport={'width': 677, 'height': 800})
        # All test assets must be inline or local. Do not send article text out.
        page.route('http://**/*', lambda route: route.abort())
        page.route('https://**/*', lambda route: route.abort())
        page.add_script_tag(path=str(ENGINE))
        for name in sys.argv[1:]:
            path = Path(name).resolve()
            result = page.evaluate('''async markup => {
                const root=document.createElement('div');root.innerHTML=markup;
                return await VerifyArticleStructure.verifyArticleStructure(root);
            }''', path.read_text(encoding='utf-8'))
            report = path.with_suffix('.report.json')
            report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            groups = result.get('inValidInfo', {})
            failed = failed or not result['isValid']
            print(json.dumps({'file': path.name, 'valid': result['isValid'],
                              'issues': {key: len(value.get('items', [])) for key, value in groups.items()},
                              'report': str(report)}, ensure_ascii=False))
        browser.close()
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
