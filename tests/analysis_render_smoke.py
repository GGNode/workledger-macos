"""Offline readable-analysis UI checks; expected JSON replay, never a live model."""
import json
import os
import sys
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from playwright.sync_api import sync_playwright
from workledger.config import Config
from workledger.store import Store
from workledger.report import build_report,render_html,render_markdown
from support_analysis import load_fixture,ExpectedResponseReplay

out=Path(os.environ.get('WORKLEDGER_QA_OUTPUT', tempfile.mkdtemp(prefix='workledger-analysis-render-')));out.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory() as temp:
    cfg=Config(Path(temp)/'data');cfg.save({'timezone':'UTC','llm':{'mode':'opencode'}})
    with Store(cfg.db_path) as store:
        fixture,aliases,tasks=load_fixture(cfg,store)
        report=build_report(cfg,store,'2026-10-06',analysis_client=ExpectedResponseReplay(fixture,aliases,tasks))
    report['demo']=True;report['demo_notice']=fixture['notice']
    report['analysis']['transport'].update(data_flow='合成响应回放，没有调用 OpenCode 或模型服务',test_double=True)
    markup=render_html(report)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_EXECUTABLE','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1360,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.set_default_timeout(2500);page.set_content(markup)
        main=page.locator('#main-narrative').inner_text()
        assert 'Objective:' not in main and 'PROBE_OK' not in main
        assert 'apply_patch: failed' not in main
        assert '标签' in main and '42' in main and '待验证' in main
        assert len(page.locator('#main-narrative > article').all())==4
        assert not page.locator('#evidence').evaluate('(e)=>e.open')
        page.screenshot(path=str(out/'analysis-desktop.png'),full_page=True)
        page.screenshot(path=str(out/'analysis-first-screen.png'))
        page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(out/'analysis-mobile.png'),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        # Every link must resolve and reveal the enclosing folded evidence.
        links=page.locator('[data-evidence]').all();count=0
        for link in links:
            targets=link.get_attribute('data-evidence').split(',')
            for eid in targets:assert page.locator('[id="e-'+eid+'"]').count()==1
            if link.is_visible():
                link.evaluate('(a)=>a.click()');assert page.locator('#evidence').evaluate('(e)=>e.open');count+=1
        assert count>10 and not errors,errors
        browser.close()
    # Files are portable examples and explicitly identified as fixture replay.
    (out/'report.html').write_text(markup);(out/'report.md').write_text(render_markdown(report))
    print(json.dumps({'status':'passed','scope':'synthetic expected-response replay; no real model','checks':['main view work/progress/blocker/verification','four distinct themes','noise hidden','desktop layout','mobile 390px no overflow','all references resolve','click reveals evidence','no JS exceptions'],'links_clicked':count},ensure_ascii=False,indent=2))
