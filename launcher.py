import os
import runpy
import socket
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

root=Path(__file__).resolve().parent
os.chdir(root)
port=int(os.getenv('ZHIZHOU_PORT','8766'))
url=f'http://127.0.0.1:{port}'
try:
    with urllib.request.urlopen(url,timeout=2) as r:
        existing=r.read().decode('utf-8',errors='replace')
    if 'STUDIO_TOKEN' in existing and '纸舟' in existing:
        print('工作台已在运行。');webbrowser.open(url);raise SystemExit(0)
    print('端口已被其他程序占用，请关闭占用程序或设置 ZHIZHOU_PORT。');raise SystemExit(1)
except (OSError,urllib.error.URLError):pass
def open_ready():
    for _ in range(50):
        try:
            with socket.create_connection(('127.0.0.1',port),timeout=.2):pass
            webbrowser.open(url);return
        except OSError:time.sleep(.2)
threading.Thread(target=open_ready,daemon=True).start()
runpy.run_path(str(root/'app.py'),run_name='__main__')
