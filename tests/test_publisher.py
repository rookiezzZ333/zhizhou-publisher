import io
import json
import tempfile
import unittest
from pathlib import Path
import httpx
from PIL import Image
from publisher.content import load_bundle,render
from publisher.state import Store
from publisher.wechat import WeChat,UncertainError,PlatformError


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.article=self.root/'article';self.article.mkdir();(self.article/'images').mkdir()
        for name in ['10.png','2.png','01.png']:Image.new('RGB',(100,100),'blue').save(self.article/'images'/name)
        self.write('# 测试文章\n\n开头\n\n![插图](images/2.png)\n\n结尾')
        self.store=Store(self.root/'state')
    def tearDown(self):self.tmp.cleanup()
    def write(self,text):(self.article/'article.md').write_text(text,encoding='utf-8')
    def test_new_installation_has_no_preconfigured_account(self):
        settings=self.store.settings()
        self.assertEqual(settings['appid'],'')
        self.assertEqual(settings['account_id'],'')

    def test_image_sort_and_inline_placement(self):
        b=load_bundle(self.article)
        self.assertEqual(b['images'],['images/01.png','images/2.png','images/10.png'])
        html=render(b,lambda x:'https://example.invalid/'+x)
        self.assertLess(html.index('开头'),html.index('<img'));self.assertLess(html.index('<img'),html.index('结尾'))
        self.assertNotIn('10.png',html)
    def test_image_gallery_without_markers(self):
        self.write('# 测试\n\n正文');b=load_bundle(self.article);html=render(b,lambda x:x)
        self.assertEqual(html.count('<img'),3)
        self.assertLess(html.index('01.png'),html.index('2.png'));self.assertLess(html.index('2.png'),html.index('10.png'))
    def test_traversal_and_remote_images_blocked(self):
        for path in ['../outside.png','https://example.com/image.png','file:///C:/secret.png','images/../../secret.png']:
            self.write('# 标题\n\n![x]('+path+')')
            with self.assertRaises(ValueError):load_bundle(self.article)
    def test_html_is_text(self):
        self.write('# 标题\n\n<script>alert(1)</script>\n\n[坏链接](javascript:alert(1))')
        output=render(load_bundle(self.article),lambda x:x)
        self.assertNotIn('<script>',output);self.assertNotIn('href="javascript:',output)
    def test_image_change_changes_revision(self):
        a=load_bundle(self.article)['fingerprint'];Image.new('RGB',(100,100),'red').save(self.article/'images/01.png')
        self.assertNotEqual(a,load_bundle(self.article)['fingerprint'])
    def test_xhs_override_no_truncation(self):
        text='内容'*700;(self.article/'xiaohongshu.md').write_text('# 短标题\n\n'+text,encoding='utf-8')
        b=load_bundle(self.article);self.assertEqual(b['xhs_title'],'短标题');self.assertEqual(b['xhs_body'],text)
    def test_dedupe_across_actions_and_restart(self):
        b=load_bundle(self.article);job,new=self.store.begin(b,'wechat','draft','wx-test');self.assertTrue(new)
        other,new=self.store.begin(b,'wechat','publish','wx-test');self.assertFalse(new);self.assertEqual(other['id'],job['id'])
        restarted=Store(self.root/'state');self.assertEqual(restarted.get(job['id'])['status'],'uncertain')
        _,new=restarted.begin(b,'wechat','publish','wx-test');self.assertFalse(new)
    def mock_wechat(self,timeout_path=None,status=0):
        self.calls=[]
        def handler(request):
            path=request.url.path;self.calls.append((path,request))
            if timeout_path and path.endswith(timeout_path):raise httpx.ReadTimeout('simulated timeout',request=request)
            if path.endswith('stable_token'):data={'access_token':'test-token','expires_in':7200}
            elif path.endswith('uploadimg'):data={'url':'https://mmbiz.qpic.cn/test-image.jpg'}
            elif path.endswith('add_material'):
                self.assertEqual(request.url.params.get('type'),'image');data={'media_id':'cover-id'}
            elif path.endswith('draft/add'):
                body=json.loads(request.content);self.remote=body['articles'][0];self.assertIn('https://mmbiz.qpic.cn/',body['articles'][0]['content']);data={'media_id':'draft-id'}
            elif path.endswith('draft/get'):data={'news_item':[self.remote]}
            elif path.endswith('freepublish/submit'):data={'errcode':0,'publish_id':'publish-id'}
            elif path.endswith('freepublish/get'):data={'publish_status':status,'article_id':'article-id','article_detail':{'item':[{'article_url':'https://mp.weixin.qq.com/s/test'}]}}
            else:data={'errcode':0}
            return httpx.Response(200,json=data)
        return WeChat({'appid':'wx-test','secret':'test-secret'},httpx.Client(transport=httpx.MockTransport(handler)))
    def test_draft_then_publish_reuses_draft_and_checks_result(self):
        b=load_bundle(self.article);wx=self.mock_wechat();job,_=self.store.begin(b,'wechat','draft','wx-test')
        result=wx.run(b,'draft',self.store,job);self.assertEqual(result['status'],'draft_created')
        job,new=self.store.begin(b,'wechat','publish','wx-test');self.assertTrue(new)
        result=wx.run(b,'publish',self.store,job);self.assertEqual(result['status'],'published')
        self.assertEqual(sum(path.endswith('draft/add') for path,_ in self.calls),1)
        self.assertEqual(result['result']['links'],['https://mp.weixin.qq.com/s/test'])
    def test_publish_timeout_never_auto_retries(self):
        b=load_bundle(self.article);wx=self.mock_wechat(timeout_path='freepublish/submit');job,_=self.store.begin(b,'wechat','publish','wx-test')
        with self.assertRaises(UncertainError):wx.run(b,'publish',self.store,job)
        self.assertEqual(sum(path.endswith('freepublish/submit') for path,_ in self.calls),1)
        self.assertEqual(self.store.get(job['id'])['status'],'submitting')
    def test_changed_remote_draft_is_not_published(self):
        b=load_bundle(self.article);wx=self.mock_wechat();job,_=self.store.begin(b,'wechat','draft','wx-test');wx.run(b,'draft',self.store,job)
        self.remote['title']='别人改过的标题';job,_=self.store.begin(b,'wechat','publish','wx-test')
        with self.assertRaises(PlatformError):wx.run(b,'publish',self.store,job)
        self.assertFalse(any(p.endswith('freepublish/submit') for p,_ in self.calls))
    def test_pending_is_not_success(self):
        b=load_bundle(self.article);wx=self.mock_wechat(status=1);job,_=self.store.begin(b,'wechat','publish','wx-test')
        self.assertEqual(wx.run(b,'publish',self.store,job)['status'],'submitted')
    def test_no_secret_means_no_network(self):
        with self.assertRaises(PlatformError):WeChat({'appid':'wx-test','secret':''}).token()
    def test_platform_errors_do_not_expose_secret(self):
        wx=WeChat({'appid':'wx-test','secret':'my-actual-test-secret'},httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'errcode':40164,'errmsg':'anything secret'}))))
        with self.assertRaises(PlatformError) as caught:wx.token()
        self.assertIn('白名单',str(caught.exception));self.assertNotIn('my-actual-test-secret',str(caught.exception))


if __name__=='__main__':unittest.main()
