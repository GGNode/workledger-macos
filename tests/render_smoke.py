"""Offline UI rendering tests. No navigation, network, or browser policy changes."""
import json,os,sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workledger.config import Config
from workledger.doctor import doctor
from workledger.demo import create_demo
from playwright.sync_api import sync_playwright
root=Path(__file__).resolve().parents[1]
artifacts=Path(os.environ.get('WORKLEDGER_QA_OUTPUT',tempfile.mkdtemp(prefix='workledger-render-')));artifacts.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory() as temp:
 cfg=Config(Path(temp)/'home');cfg.save({'timezone':'Asia/Hong_Kong'})
 status={'events':0,'sessions':0,'issues':[],'config':cfg.data,'home':str(cfg.home),'doctor':doctor(cfg),'last_capture':None}
 with sync_playwright() as p:
  browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_EXECUTABLE','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
  page=browser.new_page(viewport={'width':1350,'height':1050});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  markup=(root/'workledger/static/index.html').read_text().replace('<script src="/app.js"></script>','')
  page.set_content(markup)
  page.evaluate('''(status)=>{
    window.fixtureStatus=status;
    Object.defineProperty(window,'sessionStorage',{value:{getItem:()=> 'test-token',setItem:()=>{}}});
    window.fetch=async(url,init={})=>{
      let value={};
      if(url==='/api/status')value=window.fixtureStatus;
      else if(url==='/api/reports')value={dates:[]};
      else if(url==='/api/config') {const payload=JSON.parse(init.body);Object.assign(window.fixtureStatus.config,payload);value={saved:true};}
      else if(url.startsWith('/api/events?'))value={events:[]};
      return {ok:true,status:200,json:async()=>JSON.parse(JSON.stringify(value))};
    };
  }''',status)
  page.add_script_tag(content=(root/'workledger/static/app.js').read_text())
  page.wait_for_selector('#workspace:not([hidden])')
  page.locator('#add-project').click();page.locator('.project-name').fill('模型研究');page.locator('.project-paths').fill('/demo/research');page.locator('#save-projects').click();page.wait_for_timeout(100)
  assert page.evaluate('fixtureStatus.config.projects[0].name')=='模型研究'
  page.locator('#llm-mode').select_option('opencode')
  page.locator('#opencode-dir').fill('/synthetic/normal-project')
  page.locator('#opencode-executable').fill('/synthetic/bin/opencode')
  page.locator('#save-llm').click();page.wait_for_timeout(100)
  assert page.evaluate('fixtureStatus.config.llm.mode')=='opencode'
  assert page.evaluate('fixtureStatus.config.llm.opencode_dir')=='/synthetic/normal-project'
  assert '远程' in page.locator('#opencode-options').inner_text()
  page.screenshot(path=str(artifacts/'control-panel.png'),full_page=True)
  demo=create_demo(Path(temp)/'demo')
  page.set_content(demo.read_text());page.wait_for_timeout(100)
  assert page.locator('h1').inner_text()=='今天，工作推进到了哪里'
  assert '演示数据' in page.inner_text('body')
  page.screenshot(path=str(artifacts/'demo-desktop.png'),full_page=True)
  page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(artifacts/'demo-mobile.png'),full_page=True)
  assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
  # Root evidence stays folded until a reference is explicitly clicked.
  assert not page.locator('#evidence').evaluate('(e)=>e.open')
  page.locator('a[href^="#e-"]').first.click();assert page.locator('#evidence').evaluate('(e)=>e.open')
  assert not errors,errors
  browser.close()
 print(json.dumps({'status':'passed','scope':'offline HTML + real app.js with explicitly mocked fetch, not a live site or live extension','checks':['dashboard render','project form save payload','OpenCode analysis settings save/data-flow disclosure','desktop report','mobile 390px no overflow','evidence reference expands detail','no JavaScript exceptions'],'screenshots':str(artifacts)},ensure_ascii=False,indent=2))
