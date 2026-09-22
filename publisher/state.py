import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


def now(): return datetime.now().isoformat(timespec='seconds')


class Store:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(exist_ok=True,parents=True)
        self.lock=threading.RLock()
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, fingerprint TEXT, platform TEXT, action TEXT, account TEXT, article TEXT, status TEXT, message TEXT, result TEXT, created TEXT, updated TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS hidden_jobs (id TEXT PRIMARY KEY)')
            # Interrupted submissions must never be automatically retried after a restart.
            db.execute("UPDATE jobs SET status='uncertain',message='程序上次中断，请先到平台核实结果，避免重复发布' WHERE status IN ('running','submitting')")
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.root/'jobs.sqlite',timeout=10);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()
    def settings(self,private=False):
        path=self.root/'settings.json'
        data=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
        appid=data.get('appid','')
        result=dict(appid=appid,account_id=data.get('account_id',''),has_secret=bool(data.get('secret') or os.getenv('WECHAT_APP_SECRET')),secret_storage='environment' if os.getenv('WECHAT_APP_SECRET') else 'local-file' if data.get('secret') else 'none')
        if private:result['secret']=os.getenv('WECHAT_APP_SECRET') or data.get('secret','')
        return result
    def save_settings(self,data):
        import re
        appid=str(data.get('appid','')).strip()
        if not re.fullmatch(r'wx[a-zA-Z0-9]{16}',appid):raise ValueError('AppID 格式不正确')
        with self.lock:
            path=self.root/'settings.json';old=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
            if old.get('appid',appid)!=appid:
                old.pop('secret',None)
            old.update(appid=appid,account_id=str(data.get('account_id','')).strip())
            if data.get('secret'):
                old['secret']=str(data['secret']).strip()
            temp=path.with_suffix('.tmp');temp.write_text(json.dumps(old),encoding='utf-8');temp.replace(path)
        return self.settings()
    def list_jobs(self):
        with self.db() as db:return [self.decode(row) for row in db.execute('SELECT * FROM jobs WHERE id NOT IN (SELECT id FROM hidden_jobs) ORDER BY created DESC,rowid DESC LIMIT 100')]
    def hide_job(self,id):
        with self.lock,self.db() as db:
            row=db.execute('SELECT status FROM jobs WHERE id=?',(id,)).fetchone()
            if row is None:raise ValueError('记录不存在')
            if row['status'] in ('running','submitting','submitted','uncertain'):raise ValueError('进行中或结果待核实的记录不能删除')
            db.execute('INSERT OR IGNORE INTO hidden_jobs VALUES (?)',(id,))
        return {'ok':True}
    def resume_editor(self,id):
        with self.lock,self.db() as db:
            changed=db.execute("UPDATE jobs SET status='running',message='正在打开已有编辑器',updated=? WHERE id=? AND platform IN ('douyin','douyin_video','xiaohongshu','wechat_browser') AND action='prepare' AND status='editor_ready'",(now(),id)).rowcount
        return bool(changed)
    def decode(self,row):
        data=dict(row);data['result']=json.loads(data['result'] or '{}');return data
    def get(self,id):
        with self.db() as db:
            row=db.execute('SELECT * FROM jobs WHERE id=?',(id,)).fetchone()
            if row is None:raise ValueError('发布记录不存在')
            return self.decode(row)
    def begin(self,bundle,platform,action,account):
        with self.lock,self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            pending=db.execute("SELECT * FROM jobs WHERE fingerprint=? AND platform=? AND account=? AND status IN ('running','submitting','uncertain','submitted','published','platform_confirmed','manual_published') ORDER BY rowid DESC LIMIT 1",(bundle['fingerprint'],platform,account)).fetchone()
            if pending:return self.decode(pending),False
            row=db.execute("SELECT * FROM jobs WHERE fingerprint=? AND platform=? AND action=? AND account=? AND status NOT IN ('failed','needs_login','resolved_not_sent') ORDER BY rowid DESC LIMIT 1",(bundle['fingerprint'],platform,action,account)).fetchone()
            if row:return self.decode(row),False
            id=uuid.uuid4().hex
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)',(id,bundle['fingerprint'],platform,action,account,bundle['name'],'running','正在准备','{}',now(),now()))
        return self.get(id),True
    def update(self,id,status,message,**result):
        with self.lock,self.db() as db:
            existing=db.execute('SELECT result FROM jobs WHERE id=?',(id,)).fetchone()
            current=json.loads(existing['result'] or '{}');current.update(result)
            db.execute('UPDATE jobs SET status=?,message=?,result=?,updated=? WHERE id=?',(status,message,json.dumps(current,ensure_ascii=False),now(),id))
        return self.get(id)
    def confirm_published(self,id):
        with self.lock,self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM jobs WHERE id=?',(id,)).fetchone()
            if row is None:raise ValueError('发布记录不存在')
            job=self.decode(row)
            if job['status']=='manual_published':return job
            if job['status'] not in ('failed','needs_login','uncertain','editor_ready','draft_created','resolved_not_sent'):
                raise ValueError('当前记录不能标记手动发布；请等待正在进行的操作结束')
            result=job['result']
            result['manual_confirmation']={'confirmed_at':now(),'source':'user',
                'previous_status':job['status'],'previous_message':job['message']}
            db.execute('UPDATE jobs SET status=?,message=?,result=?,updated=? WHERE id=?',
                ('manual_published','用户确认已在平台手动发布；此结果不是程序自动检测。',json.dumps(result,ensure_ascii=False),now(),id))
        return self.get(id)
    def prior_draft(self,bundle,account):
        with self.db() as db:
            rows=db.execute("SELECT * FROM jobs WHERE fingerprint=? AND platform='wechat' AND account=? ORDER BY rowid DESC",(bundle['fingerprint'],account)).fetchall()
            for row in rows:
                job=self.decode(row)
                if job['result'].get('media_id'):return job['result']
        return None
