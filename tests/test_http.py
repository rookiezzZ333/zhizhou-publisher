import base64
import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from PIL import Image
import app
from publisher.state import Store
from publisher.wechat import PlatformError


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.previous=(app.ARTICLES,app.STATE,app.STORE,app.PORT)
        app.ARTICLES=self.root/'articles';app.ARTICLES.mkdir()
        app.STATE=self.root/'state';app.STORE=Store(app.STATE)
        self.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler);app.PORT=self.server.server_port
        self.thread=threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.05},daemon=True);self.thread.start()
        self.client=httpx.Client(base_url=f'http://127.0.0.1:{app.PORT}',headers={'X-Studio-Token':app.TOKEN})
        image=io.BytesIO();Image.new('RGB',(10,10),'red').save(image,format='PNG')
        self.payload={'name':'article_测试','files':[{'path':'article.md','data':base64.b64encode('# 测试\n\n正文\n\n![图](images/01.png)'.encode()).decode()},{'path':'images/01.png','data':base64.b64encode(image.getvalue()).decode()}]}
    def tearDown(self):
        self.client.close();self.server.shutdown();self.server.server_close();self.thread.join()
        app.ARTICLES,app.STATE,app.STORE,app.PORT=self.previous;self.tmp.cleanup()
    def test_folder_import_preview_and_image(self):
        self.client.get('/')
        response=self.client.post('/api/import',json=self.payload);self.assertEqual(response.status_code,200)
        data=response.json();self.assertEqual(data['title'],'测试');self.assertEqual(data['images'],['images/01.png'])
        image=self.client.get(data['image_items'][0]['url']);self.assertEqual(image.status_code,200)
        self.assertEqual(self.client.post('/api/import',json=self.payload).json()['id'],data['id'])
        self.assertEqual(len(self.client.get('/api/articles').json()),1)

    def test_video_upload_preview_range_and_copy(self):
        self.client.get('/')
        bundle=self.client.post('/api/import',json=self.payload).json()
        from urllib.parse import quote
        endpoint='/api/video-upload/'+quote(bundle['id'])
        headers={'X-Content-Version':bundle['fingerprint'],'Content-Type':'video/mp4'}
        self.assertEqual(self.client.post(endpoint,headers=headers,content=b'not video').status_code,400)
        content=b'\x00\x00\x00\x18ftypmp42'+b'0'*40
        response=self.client.post(endpoint,headers=headers,content=content)
        self.assertEqual(response.status_code,200,response.text)
        uploaded=response.json()
        self.assertEqual(self.client.post(endpoint,headers=headers,content=content).status_code,400)
        media=self.client.get(uploaded['video_url'],headers={'Range':'bytes=4-7'})
        self.assertEqual(media.status_code,206);self.assertEqual(media.content,b'ftyp')
        self.assertEqual(self.client.get(uploaded['video_url'],headers={'Range':'bytes=999-'}).status_code,416)
        copy=self.client.post('/api/articles/video-text',json={'article_id':bundle['id'],'fingerprint':uploaded['fingerprint'],'title':'视频短标题','body':'独立说明 #教程'})
        self.assertEqual(copy.status_code,200,copy.text)
        self.assertEqual(copy.json()['douyin_video_title'],'视频短标题')
        self.assertEqual(copy.json()['title'],bundle['title'])
        frozen=app.snapshot(app.load_bundle(app.ARTICLES/bundle['id']),'video-test')
        self.assertEqual(frozen['fingerprint'],copy.json()['fingerprint'])
    def test_remove_image_and_delete_article(self):
        b=self.client.post('/api/import',json=self.payload).json()
        request={'article_id':b['id'],'fingerprint':b['fingerprint'],'path':'images/01.png','confirmed':True}
        removed=self.client.post('/api/articles/remove-image',json=request)
        self.assertEqual(removed.status_code,200,removed.text)
        data=removed.json();self.assertEqual(data['images'],[])
        self.assertNotIn('<img',data['html']);self.assertEqual(data['cover'],'')
        self.assertTrue((app.ARTICLES/b['id']/'images/01.png').exists())
        self.assertEqual(self.client.post('/api/articles/delete',json=request).status_code,400)
        request['fingerprint']=data['fingerprint']
        self.assertEqual(self.client.post('/api/articles/delete',json=request).status_code,200)
        self.assertEqual(self.client.get('/api/articles').json(),[])
        self.assertEqual(len(list((app.STATE/'trash').iterdir())),1)
        request['article_id']='../state'
        self.assertEqual(self.client.post('/api/articles/delete',json=request).status_code,400)

    def test_delete_record_keeps_duplicate_protection(self):
        b=self.client.post('/api/import',json=self.payload).json()
        job,_=app.STORE.begin(b,'xiaohongshu','prepare','test')
        args={'id':job['id'],'confirmed':True}
        self.assertEqual(self.client.post('/api/jobs/delete',json=args).status_code,400)
        app.STORE.update(job['id'],'manual_published','confirmed')
        self.assertEqual(self.client.post('/api/jobs/delete',json=args).status_code,200)
        self.assertEqual(app.STORE.list_jobs(),[])
        _,created=app.STORE.begin(b,'xiaohongshu','publish','test')
        self.assertFalse(created)
    def test_resume_editor_is_atomic_and_never_reopens_published(self):
        b=self.client.post('/api/import',json=self.payload).json()
        job,_=app.STORE.begin(b,'douyin','prepare','test')
        self.assertFalse(app.STORE.resume_editor(job['id']))
        app.STORE.update(job['id'],'editor_ready','filled')
        self.assertTrue(app.STORE.resume_editor(job['id']))
        self.assertFalse(app.STORE.resume_editor(job['id']))
        app.STORE.update(job['id'],'manual_published','confirmed')
        self.assertFalse(app.STORE.resume_editor(job['id']))
    def test_manual_publication_preserves_failure_and_blocks_repeat(self):
        b=self.client.post('/api/import',json=self.payload).json()
        job,_=app.STORE.begin(b,'xiaohongshu','prepare','test')
        endpoint='/api/jobs/confirm-published'
        self.assertEqual(self.client.post(endpoint,json={'id':job['id'],'confirmed':True}).status_code,400)
        app.STORE.update(job['id'],'failed','正文校验中断',screenshot='/test.png')
        self.assertEqual(self.client.post(endpoint,json={'id':job['id']}).status_code,400)
        response=self.client.post(endpoint,json={'id':job['id'],'confirmed':True})
        self.assertEqual(response.status_code,200)
        data=response.json();self.assertEqual(data['status'],'manual_published')
        self.assertEqual(data['result']['manual_confirmation']['previous_message'],'正文校验中断')
        self.assertEqual(data['result']['screenshot'],'/test.png')
        self.assertEqual(self.client.post(endpoint,json={'id':job['id'],'confirmed':True}).json(),data)
        _,created=app.STORE.begin(b,'xiaohongshu','publish','test')
        self.assertFalse(created)
    def test_import_path_and_non_content_rejected(self):
        for path in ['../article.md','images/../../secrets.png','settings.json','images/x.py','images/C:/secret.png']:
            payload={'name':'bad','files':[{'path':path,'data':'eA=='}]}
            self.assertEqual(self.client.post('/api/import',json=payload).status_code,400)
    def test_missing_auth_and_wrong_origin(self):
        self.assertEqual(httpx.post(f'http://127.0.0.1:{app.PORT}/api/settings',json={}).status_code,403)
        self.assertEqual(self.client.post('/api/settings',json={},headers={'Origin':'https://unrelated.example'}).status_code,403)
    def test_plaintext_local_secret_not_returned(self):
        response=self.client.post('/api/settings',json={'appid':'wx0000000000000000','account_id':'test-id','secret':'fake-test-secret'})
        self.assertEqual(response.status_code,200);self.assertNotIn('fake-test-secret',response.text)
        self.assertIn('fake-test-secret',(app.STATE/'settings.json').read_text())
        self.assertNotIn('fake-test-secret',self.client.get('/api/settings').text)
    def test_stale_preview_and_missing_credentials_stop_before_job(self):
        b=self.client.post('/api/import',json=self.payload).json()
        p={'article_id':b['id'],'fingerprint':'stale','platform':'wechat','action':'publish'}
        self.assertEqual(self.client.post('/api/publish',json=p).status_code,400)
        p['fingerprint']=b['fingerprint'];self.assertEqual(self.client.post('/api/publish',json=p).status_code,400)
        self.assertEqual(self.client.get('/api/jobs').json(),[])
    def test_accepted_publish_query_failure_stays_submitted(self):
        b=self.client.post('/api/import',json=self.payload).json();job,_=app.STORE.begin(b,'wechat','publish','wx-test')
        class Accepted:
            def __init__(self,*args):pass
            def run(self,bundle,action,store,job):
                store.update(job['id'],'submitted','accepted',publish_id='pid')
                raise PlatformError('query unavailable')
        with patch.object(app,'WeChat',Accepted):app.run_job(b,'wechat','publish',job,{})
        self.assertEqual(app.STORE.get(job['id'])['status'],'submitted')


if __name__=='__main__':unittest.main()
