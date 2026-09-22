import io,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch,Mock
import httpx
from PIL import Image
import app
from publisher.state import Store
from publisher.video import load_video,read_config,write_config,migrate_legacy
from publisher.content import load_bundle
MP4=b'\x00\x00\x00\x18ftypmp42'+b'\0'*24

class VideoApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.old=(app.VIDEOS,app.ARTICLES,app.STATE,app.STORE,app.PORT)
        app.VIDEOS=self.root/'videos';app.VIDEOS.mkdir();app.ARTICLES=self.root/'articles';app.ARTICLES.mkdir()
        app.STATE=self.root/'state';app.STORE=Store(app.STATE)
        self.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler);app.PORT=self.server.server_port
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.05},daemon=True);self.thread.start()
        self.client=httpx.Client(base_url=f'http://127.0.0.1:{app.PORT}',headers={'X-Studio-Token':app.TOKEN});self.client.get('/')
    def tearDown(self):
        self.client.close();self.server.shutdown();self.server.server_close();self.thread.join()
        app.VIDEOS,app.ARTICLES,app.STATE,app.STORE,app.PORT=self.old;self.tmp.cleanup()
    def create(self):
        r=self.client.post('/api/videos/create',json={'title':'独立视频'});self.assertEqual(r.status_code,200);return r.json()
    def upload(self,b,data=MP4):
        return self.client.post('/api/videos/'+b['id']+'/upload',content=data,headers={'X-Content-Version':b['fingerprint'],'Content-Type':'video/mp4'})
    def test_independent_upload_range_auth_and_invalid_replacement(self):
        b=self.create();self.assertFalse((app.VIDEOS/b['id']/'article.md').exists());b=self.upload(b).json()
        self.assertEqual(self.client.get('/api/articles').json(),[])
        r=self.client.get(b['video_url'],headers={'Range':'bytes=4-7'});self.assertEqual(r.status_code,206);self.assertEqual(r.content,b'ftyp')
        r=self.client.get(b['video_url'],headers={'Range':'bytes=-4'});self.assertEqual(r.content,MP4[-4:])
        self.assertEqual(self.upload(b,b'bad').status_code,400)
        self.assertEqual(load_video(app.VIDEOS/b['id'])['fingerprint'],b['fingerprint'])
        with httpx.Client(base_url=str(self.client.base_url)) as other:self.assertEqual(other.get(b['video_url']).status_code,403)
    def test_platform_copy_isolation_cover_and_stale_version(self):
        b=self.upload(self.create()).json()
        r=self.client.post('/api/videos/text',json={'video_id':b['id'],'fingerprint':b['fingerprint'],'platform':'bilibili','title':'B 站标题','body':'简介','tags':['动画'],'copyright':'original','category':'动画 / 综合'})
        self.assertEqual(r.status_code,200);new=r.json()
        self.assertEqual(new['copies']['douyin_video']['title'],'独立视频');self.assertEqual(new['copies']['bilibili']['title'],'B 站标题')
        self.assertEqual(self.upload(b).status_code,400)
        im=io.BytesIO();Image.new('RGB',(10,10),'red').save(im,format='PNG')
        r=self.client.post('/api/videos/'+new['id']+'/cover',content=im.getvalue(),headers={'X-Content-Version':new['fingerprint'],'Content-Type':'image/png'})
        self.assertEqual(r.status_code,200);self.assertTrue(r.json()['cover_url'])
    def test_prepare_snapshot_and_duplicate_protection(self):
        b=self.upload(self.create()).json()
        b=self.client.post('/api/videos/text',json={'video_id':b['id'],'fingerprint':b['fingerprint'],'platform':'bilibili','title':'标题','body':'正文','tags':['原创']}).json()
        payload={'video_id':b['id'],'fingerprint':b['fingerprint'],'platform':'bilibili'}
        with patch.object(app.BILI_POOL,'submit') as submit:
            r=self.client.post('/api/videos/prepare',json=payload);self.assertEqual(r.status_code,202,r.text)
            self.assertTrue(r.json()['created']);self.assertEqual(submit.call_count,1)
            frozen=submit.call_args.args[1];self.assertTrue((Path(frozen['folder'])/'video.yaml').is_file())
            job=r.json()['job'];app.STORE.update(job['id'],'manual_published','手动确认')
            self.assertFalse(self.client.post('/api/videos/prepare',json=payload).json()['created']);self.assertEqual(submit.call_count,1)
    def test_delete_retains_recoverable_files_and_rejects_traversal(self):
        b=self.upload(self.create()).json()
        self.assertEqual(self.client.post('/api/videos/delete',json={'video_id':'../outside','fingerprint':b['fingerprint'],'confirmed':True}).status_code,400)
        r=self.client.post('/api/videos/delete',json={'video_id':b['id'],'fingerprint':b['fingerprint'],'confirmed':True})
        self.assertEqual(r.status_code,200);self.assertEqual(self.client.get('/api/videos').json(),[])
        self.assertEqual(len(list((app.STATE/'trash/videos').glob('*/video.yaml'))),1)
    def test_migration_is_idempotent_and_preserves_published_job(self):
        f=app.ARTICLES/'article_legacy';f.mkdir();(f/'clip.mp4').write_bytes(MP4)
        (f/'article.md').write_text('---\ntitle: 旧视频\ndouyin_video: clip.mp4\n---\n# 旧视频\n\n介绍',encoding='utf-8')
        old=load_bundle(f);job,_=app.STORE.begin(old,'douyin_video','prepare','local-douyin-profile');app.STORE.update(job['id'],'manual_published','手动确认')
        self.assertEqual(len(migrate_legacy(app.ARTICLES,app.VIDEOS,app.STATE,app.STORE,video_only=['article_legacy'])),1)
        self.assertEqual(migrate_legacy(app.ARTICLES,app.VIDEOS,app.STATE,app.STORE),[])
        new=load_video(app.VIDEOS/'video_legacy')
        self.assertEqual((app.VIDEOS/'video_legacy/media/video.mp4').read_bytes(),MP4)
        self.assertTrue((f/'clip.mp4').exists());self.assertTrue(load_bundle(f)['library_hidden'])
        kept=app.STORE.get(job['id']);self.assertEqual(kept['status'],'manual_published');self.assertEqual(kept['article'],'video_legacy')
        self.assertEqual(kept['fingerprint'],new['fingerprint'])
        self.assertFalse(app.STORE.begin(new,'douyin_video','prepare','local-douyin-profile')[1])
        import shutil
        shutil.rmtree(app.VIDEOS/'video_legacy')
        self.assertEqual(migrate_legacy(app.ARTICLES,app.VIDEOS,app.STATE,app.STORE),[])
        self.assertFalse((app.VIDEOS/'video_legacy').exists())
    def test_configuration_cannot_read_outside(self):
        b=self.create();folder=app.VIDEOS/b['id'];meta=read_config(folder);meta['file']='../../outside.mp4';write_config(folder,meta)
        with self.assertRaises(ValueError):load_video(folder)

class BilibiliTests(unittest.TestCase):
    def test_real_dom_fills_tags_and_never_publishes(self):
        from playwright.sync_api import sync_playwright
        from publisher.bilibili import Bilibili
        with tempfile.TemporaryDirectory() as tmp,sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True);page=browser.new_page()
            html="""<meta charset="utf-8"><input type=file accept="video/*"><input placeholder="标题"><textarea placeholder="简介"></textarea><input placeholder="标签"><div id=tags></div><button onclick="window.published=true">立即投稿</button>
<script>document.querySelector('[placeholder=标签]').onkeydown=e=>{if(e.key==='Enter'){const s=document.createElement('span');s.className='tag-item';s.textContent=e.target.value;document.querySelector('#tags').append(s);e.target.value='';}};</script>"""
            page.route('https://member.bilibili.com/**',lambda route:route.fulfill(body=html,content_type='text/html'))
            folder=Path(tmp);(folder/'clip.mp4').write_bytes(MP4)
            b={'folder':str(folder),'video':'clip.mp4','fingerprint':'test','copies':{'bilibili':{'title':'B 站独立标题','body':'第一段\n第二段'}},'bilibili_tags':['动画','设计']}
            adapter=Bilibili(folder);adapter.browser=lambda:page;adapter.screenshot=lambda job:None
            store=Mock();adapter.run(b,store,{'id':'a'*32})
            self.assertEqual(page.locator('[placeholder=标题]').input_value(),'B 站独立标题')
            self.assertEqual(page.locator('.tag-item').all_text_contents(),['动画','设计'])
            self.assertIsNone(page.evaluate('window.published'));self.assertEqual(store.update.call_args.args[1],'editor_ready')
            page.locator('[placeholder=标题]').fill('用户正在编辑');adapter.run(b,store,{'id':'a'*32})
            self.assertEqual(page.locator('[placeholder=标题]').input_value(),'用户正在编辑');browser.close()

    def test_iframe_selection_and_existing_title_protection(self):
        from playwright.sync_api import sync_playwright
        from publisher.bilibili import Bilibili
        from publisher.wechat import PlatformError
        import html
        with tempfile.TemporaryDirectory() as tmp,sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True);page=browser.new_page()
            frame='<input type=file accept="video/*"><input placeholder="标题" value="保留我的编辑"><textarea placeholder="简介">正文</textarea>'
            page.set_content('<input type=file accept="image/*"><iframe srcdoc="'+html.escape(frame,quote=True)+'"></iframe>')
            page.frame_locator('iframe').locator('input[placeholder]').wait_for()
            adapter=Bilibili(Path(tmp));scope=adapter.scope(page)
            self.assertNotEqual(scope,page)
            bundle={'video':'clip.mp4','copies':{'bilibili':{'title':'新标题','body':'新简介'}},'bilibili_tags':[]}
            with self.assertRaises(PlatformError):adapter.fill_form(scope,bundle)
            self.assertEqual(scope.locator('textarea').input_value(),'正文')
            browser.close()
