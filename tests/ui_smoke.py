"""Optional browser integration smoke test; synthetic fixtures, NOT real-site validation."""
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workledger.config import Config
from workledger.server import make_server
from workledger.demo import create_demo
from workledger.store import Store
from playwright.sync_api import sync_playwright

root=Path(__file__).resolve().parents[1]
artifacts=Path(os.environ.get('WORKLEDGER_QA_OUTPUT',tempfile.mkdtemp(prefix='workledger-ui-')))
artifacts.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory() as temp:
    cfg=Config(Path(temp)/'home');cfg.save({'timezone':'UTC','report_open':False})
    server=make_server(cfg,port=0);t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_EXECUTABLE', '/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1350,'height':1050})
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{server.server_port}/#token={cfg.token}')
            page.wait_for_selector('#workspace:not([hidden])');page.wait_for_timeout(300)
            assert page.locator('#connection').inner_text()=='本机服务已连接'
            page.locator('#add-project').click();page.locator('.project-name').fill('模型研究');page.locator('.project-paths').fill(str(Path(temp)/'research'))
            page.locator('#save-projects').click();page.wait_for_timeout(400)
            assert Config(cfg.home).data['projects'][0]['name']=='模型研究'
            page.screenshot(path=str(artifacts/'control-panel.png'),full_page=True)
            report=create_demo(Path(temp)/'demo')
            page.goto(report.as_uri());page.wait_for_timeout(200)
            assert page.locator('h1').inner_text()=='2026-10-06 工作简报'
            assert '演示数据' in page.inner_text('body')
            page.screenshot(path=str(artifacts/'demo-desktop.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(artifacts/'demo-mobile.png'),full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert not errors,errors
            browser.close()
            # Load the real MV3 extension into Chromium and feed a synthetic ChatGPT DOM.
            ext=root/'extensions/browser'
            context=p.chromium.launch_persistent_context(str(Path(temp)/'profile'),executable_path=os.environ.get('CHROMIUM_EXECUTABLE', '/usr/bin/chromium'),headless=True,args=['--no-sandbox',f'--disable-extensions-except={ext}',f'--load-extension={ext}'])
            worker=context.service_workers[0] if context.service_workers else context.wait_for_event('serviceworker')
            worker.evaluate('(v)=>chrome.storage.local.set(v)',{'url':f'http://127.0.0.1:{server.server_port}','token':cfg.token,'enabled':True})
            chat=context.new_page()
            fixture='''<!doctype html><title>Fixture chat</title><div id="messages"><div data-message-id="old" data-message-author-role="assistant">Old answer from yesterday</div></div><textarea id="prompt-textarea"></textarea><button data-testid="send-button" aria-label="Send">Send</button><script>document.querySelector('button').onclick=()=>{const input=document.querySelector('textarea');const user=document.createElement('div');user.setAttribute('data-message-id','new-user');user.setAttribute('data-message-author-role','user');user.textContent=input.value;const reply=document.createElement('div');reply.setAttribute('data-message-id','new-assistant');reply.setAttribute('data-message-author-role','assistant');reply.textContent='Today result';document.querySelector('#messages').append(user,reply);input.value='';};</script>'''
            chat.route('https://chatgpt.com/**',lambda route:route.fulfill(status=200,content_type='text/html',body=fixture))
            chat.goto('https://chatgpt.com/c/fixture-conversation');chat.wait_for_timeout(1500)
            chat.locator('#prompt-textarea').fill('Today question');chat.get_by_role('button',name='Send').click();chat.wait_for_timeout(2500)
            with Store(cfg.db_path) as db:
                messages=[dict(r) for r in db.conn.execute("select text,chronology,actor from events where source='chatgpt'")]
                assert len(messages)==2,{'messages':messages,'extension':worker.evaluate('()=>chrome.storage.local.get(null)')}
                assert all(m['chronology']=='live_observed' for m in messages)
                assert not any('yesterday' in m['text'] for m in messages)
            # Inject an older history item without sending. It must remain excluded.
            chat.evaluate("()=>{const el=document.createElement('div');el.dataset.messageId='older';el.dataset.messageAuthorRole='user';el.textContent='Old history';document.querySelector('#messages').prepend(el)}")
            chat.wait_for_timeout(1500)
            with Store(cfg.db_path) as db:assert db.conn.execute("select count(*) from events where source='chatgpt'").fetchone()[0]==2
            context.close()
        print(json.dumps({'status':'passed','checks':['dashboard authentication','project settings save','desktop report','390px mobile no horizontal overflow','no browser JavaScript exceptions','real MV3 extension to local API on synthetic ChatGPT DOM','new messages accepted','old messages excluded'],'screenshots':str(artifacts)},ensure_ascii=False,indent=2))
    finally:
        server.shutdown();server.server_close();t.join()
