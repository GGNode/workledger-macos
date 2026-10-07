"""Product integration: versioned publication, API, attribution and scheduling."""
import copy
import json
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from workledger.config import Config
from workledger.store import Store
from workledger.util import digest, day_bounds
from workledger.analysis.publish import publish
from workledger.analysis.pipeline import consolidate, analyze
from workledger.analysis.backend import AnalysisError
from workledger.analysis.evidence import prepare, packet_input
from workledger.report import build_report
from workledger.runtime import capture_and_report, processing_lock
from workledger.server import make_server
from workledger.adapters.common import Writer
from workledger.documents import changes
from support_analysis import load_fixture, ExpectedResponseReplay

DAY='2026-10-06';AT='2026-10-06T09:00:00Z';LATER='2026-10-06T10:00:00Z'

class ProductTests(unittest.TestCase):
    def test_report_does_not_wait_for_active_collection(self):
        self.store.event('test','snapshot','note',occurred_at=AT,text='confirmed synthetic note',actor='human')
        self.store.conn.commit()
        started=time.monotonic()
        with processing_lock(self.cfg), patch('workledger.ingest.collect',side_effect=AssertionError('must not start concurrent collection')):
            path=capture_and_report(self.cfg,day=DAY,open_after=False)
        self.assertLess(time.monotonic()-started,2)
        report=json.loads((path.parent/'report.json').read_text())
        self.assertTrue(report['coverage']['capture_pending'])
        self.assertIn('已提交的数据快照',path.read_text())
        self.assertEqual(report['stats']['human_confirmed'],1)
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.cfg=Config(self.root/'data');self.cfg.save({'timezone':'UTC','report_open':False})
        self.store=Store(self.cfg.db_path)
    def tearDown(self):self.store.close();self.temp.cleanup()
    def example(self):
        self.cfg.save({'llm':{'mode':'opencode'}})
        fixture,aliases,tasks=load_fixture(self.cfg,self.store)
        return build_report(self.cfg,self.store,DAY,analysis_client=ExpectedResponseReplay(fixture,aliases,tasks))
    def test_all_formats_and_state_share_one_generation(self):
        r=self.example();path=publish(self.cfg,r);root=path.parent
        state=json.loads((root/'state.json').read_text())
        for name,checksum in state['files'].items():
            self.assertEqual(digest((root/name).read_bytes()),checksum)
            self.assertEqual((root/name).resolve().parent.name,state['generation'])
        self.assertEqual(json.loads((root/'report.json').read_text())['analysis']['status'],state['analysis_status'])
    def test_real_pipeline_unavailable_backend_publishes_explicit_degradation(self):
        self.cfg.save({'llm':{'mode':'opencode','opencode_executable':str(self.root/'missing')}})
        self.store.event('x','request','user_message',occurred_at=AT,text='An uncompleted request',actor='unknown')
        r=build_report(self.cfg,self.store,DAY);path=publish(self.cfg,r)
        self.assertEqual(r['analysis']['status'],'degraded')
        self.assertEqual(r['analysis']['warnings'][0]['code'],'unavailable')
        self.assertEqual(json.loads((path.parent/'state.json').read_text())['analysis_status'],'degraded')
        self.assertEqual(r['model_summary']['status'],'degraded')
    def test_model_failure_preserves_previous_good_report_and_link(self):
        r=self.example();path=publish(self.cfg,r);old=path.resolve();oldbytes=old.read_bytes()
        degraded=copy.deepcopy(r);degraded['analysis'].update(status='degraded',warnings=[{'code':'timeout','stage':'map'}])
        publish(self.cfg,degraded)
        self.assertTrue(old.exists());self.assertEqual(old.read_bytes(),oldbytes)
        self.assertEqual(degraded['previous_success']['url'],old.as_uri())
        self.assertIn('打开保留版本',path.read_text())
    def test_renderer_failure_does_not_replace_any_live_format(self):
        path=publish(self.cfg,self.example());old={n:(path.parent/n).read_bytes() for n in ('report.html','report.md','report.json','state.json')}
        with patch('workledger.analysis.publish.render_html',side_effect=RuntimeError('renderer fail')):
            with self.assertRaises(RuntimeError):publish(self.cfg,self.example())
        self.assertEqual(old,{n:(path.parent/n).read_bytes() for n in old})
    def test_legacy_v1_report_is_kept_on_first_failed_upgrade(self):
        root=self.cfg.reports/DAY;root.mkdir();(root/'report.html').write_text('OLD V1')
        self.store.event('x','request','user_message',occurred_at=AT,text='request')
        r=build_report(self.cfg,self.store,DAY);publish(self.cfg,r)
        saved=list((root/'versions').glob('legacy-*/report.html'))
        self.assertEqual(len(saved),1);self.assertEqual(saved[0].read_text(),'OLD V1')
        self.assertIn('previous_success',r)
    def test_new_report_generation_never_calls_model_when_read(self):
        path=publish(self.cfg,self.example())
        with patch('workledger.analysis.backend.ModelClient._invoke',side_effect=AssertionError('read called model')):
            for _ in range(4):
                json.loads((path.parent/'report.json').read_text());path.read_text()
    def test_failed_semantic_reduction_cannot_hide_original_blocker(self):
        r=self.example();theme=r['analysis']['themes'][1]
        evidence=r['analysis']['evidence'];tasks={tid:{} for tid in theme['task_ids']}
        class DropsBlocker:
            def request(self,stage,data,instruction,validator):
                obj=copy.deepcopy(theme);obj['issues']=[];obj['covered_item_ids']=[theme['id']]
                try:return validator(obj)
                except ValueError as exc:raise AnalysisError('schema') from exc
        warnings=[];out=consolidate([theme],{'evidence':evidence,'tasks':tasks},DropsBlocker(),theme['title'],60000,warnings)
        self.assertEqual(out[0]['issues'][0]['state'],'open');self.assertEqual(warnings[0]['code'],'schema')
    def test_partial_map_publishes_analysis_and_preserves_missing_evidence(self):
        self.cfg.save({'llm':{'mode':'opencode'}})
        kept=self.store.event('test','kept','note',occurred_at=AT,text='Synthetic confirmed work',actor='human',session_id='kept-task')
        omitted=self.store.event('test','omitted','user_message',occurred_at=AT,text='Independent requirement',session_id='kept-task')
        self.store.conn.commit()
        class PartialResponse:
            calls=0;actual_models=set()
            def request(self,stage,data,instruction,validator):
                self.calls+=1
                if stage=='map':
                    r=next((v for v in data['records'] if v['id']==kept),None)
                    if r is None:
                        return validator({'items':[],'accounted_ids':[v['id'] for v in data['records']],'ignored':[]})
                    self.work={'title':'Confirmed work','task_ids':[r['task_id']],'work':[{'text':'Confirmed work','evidence_ids':[kept],'basis':'human_confirmed','scope':'today'}],'results':[],'remaining':[],'suggestions':[],'issues':[]}
                    value={'items':[self.work],'accounted_ids':[kept,omitted],'ignored':[]}
                elif stage=='route':value={'groups':[{'title':'Confirmed work','item_ids':[v['id'] for v in data['catalog']]}]}
                elif stage=='theme':value={**self.work,'covered_item_ids':[v['id'] for v in data['items']]}
                elif stage=='day':value={'highlights':self.work['work']}
                return validator(value)
        r=build_report(self.cfg,self.store,DAY,analysis_client=PartialResponse());a=r['analysis']
        self.assertEqual(a['status'],'partial');self.assertEqual(a['coverage']['evidence_analyzed'],1)
        self.assertEqual(a['coverage']['missing_evidence_ids'],[omitted]);self.assertEqual(a['coverage']['packets_partial'],1)
        self.assertEqual(a['coverage']['packets_analyzed'],0);self.assertTrue(a['highlights'])
        self.assertTrue(any(t.get('analysis_status')=='observation_only' for t in a['themes']))
    def test_mapping_reserves_budget_for_daily_conclusions(self):
        self.cfg.save({'llm':{'mode':'opencode'},'analysis':{'chunk_chars':3000}})
        for i in range(2):
            self.store.event('test','note-'+str(i),'note',occurred_at=AT,text='synthetic confirmed note '+('x'*2000),actor='human',session_id='task-'+str(i))
        self.store.conn.commit()
        class SlowMap:
            def __init__(self):
                self.started=time.monotonic()-400;self.calls=0;self.stages=[];self.actual_models=set()
            def request(self,stage,data,instruction,validator):
                self.calls+=1;self.stages.append(stage)
                if stage=='map':
                    rows=data['records'];r=rows[0]
                    c={'text':'Confirmed synthetic work','evidence_ids':list(dict.fromkeys(v['id'] for v in rows)),'basis':'human_confirmed','scope':'today'}
                    self.work={'title':'Synthetic work','task_ids':list(dict.fromkeys(v['task_id'] for v in rows)),'work':[c],'results':[],'remaining':[],'suggestions':[],'issues':[]}
                    value={'items':[self.work],'accounted_ids':list(dict.fromkeys(v['id'] for v in rows)),'ignored':[]}
                elif stage=='route':value={'groups':[{'title':'Synthetic work','item_ids':[v['id'] for v in data['catalog']]}]}
                elif stage=='theme':value={**self.work,'covered_item_ids':[v['id'] for v in data['items']]}
                elif stage=='day':value={'highlights':self.work['work']}
                else:raise AssertionError(stage)
                return validator(value)
        client=SlowMap();plan=prepare(self.cfg,self.store,self.store.events(*day_bounds(DAY,'UTC')),self.store.all_sessions(),DAY)
        self.assertGreater(len(plan['packets']),1)
        out=analyze(self.cfg,self.store,self.store.events(*day_bounds(DAY,'UTC')),self.store.all_sessions(),DAY,client=client)
        self.assertEqual(client.stages,['map','route','theme','day'])
        self.assertEqual(out['status'],'partial');self.assertTrue(out['highlights']);self.assertTrue(out['coverage']['missing_evidence_ids'])
    def test_map_and_context_preserve_tool_settlement_provenance(self):
        self.store.event('test','ok','tool_result',occurred_at=AT,text='synthetic output',actor='agent',session_id='synthetic-session',metadata={'success':True})
        self.store.event('test','bad','tool_result',occurred_at=LATER,text='synthetic failure',actor='agent',session_id='synthetic-session',metadata={'success':False})
        self.store.conn.commit()
        plan=prepare(self.cfg,self.store,self.store.events(*day_bounds(DAY,'UTC')),self.store.all_sessions(),DAY)
        records=[r for packet in plan['packets'] for r in packet]
        self.assertEqual({r['success'] for r in records},{True,False})
        successful=next(e for e in plan['evidence'].values() if e['metadata'].get('success') is True)
        failed=next(r for r in records if r['success'] is False)
        plan['tasks'][failed['task_id']]['history_ids']=[successful['id']]
        data=packet_input(plan,[failed],self.cfg)
        self.assertTrue(next(c for c in data['context'] if c['id']==successful['id'])['success'])
    def test_collector_issue_does_not_become_task_failure(self):
        self.store.issue('documents','scan','Permission denied')
        self.store.event('x','req','user_message',occurred_at=AT,text='request')
        r=build_report(self.cfg,self.store,DAY)
        self.assertTrue(r['coverage']['issues']);self.assertTrue(all(not t['issues'] for t in r['analysis']['themes']))
    def test_tool_output_after_first_2000_characters_is_retained(self):
        w=Writer(self.store,'x','a',cwd='/synthetic')
        w.call('c','bash',{'command':'synthetic test'},AT)
        w.result('c','noise '*1200+'IMPORTANT FINAL VALIDATION',LATER,explicit_ok=True)
        row=next(e for e in self.store.events(*day_bounds(DAY,'UTC')) if e['kind']=='tool_result')
        self.assertNotIn('FINAL VALIDATION',row['metadata']['output_excerpt'])
        self.assertIn('FINAL VALIDATION',row['metadata']['output'])
    def test_result_without_call_does_not_invent_operation_equivalence(self):
        w=Writer(self.store,'x','a');w.result('a','failed',AT,explicit_ok=False);w.result('b','done',LATER,explicit_ok=True)
        rows=self.store.events(*day_bounds(DAY,'UTC'))
        self.assertTrue(all(not e['metadata'].get('operation_id') for e in rows))
    def test_retry_association_requires_same_recorded_operation(self):
        w=Writer(self.store,'x','a',cwd='/synthetic')
        w.call('a','bash',{'command':'test --suite same'},AT);w.result('a','bad',AT,explicit_ok=False)
        w.call('b','bash',{'command':'test --suite different'},LATER);w.result('b','ok',LATER,explicit_ok=True)
        p=prepare(self.cfg,self.store,self.store.events(*day_bounds(DAY,'UTC')),self.store.all_sessions(),DAY)
        self.assertFalse(any(e.get('possible_retry_success') for e in p['evidence'].values()))
        w.call('c','bash',{'command':'test --suite same'},LATER);w.result('c','ok',LATER,explicit_ok=True)
        p=prepare(self.cfg,self.store,self.store.events(*day_bounds(DAY,'UTC')),self.store.all_sessions(),DAY)
        self.assertTrue(any(e.get('possible_retry_success') for e in p['evidence'].values()))
    def test_document_truncation_is_explicit(self):
        delta=changes({'body':'a'*3000},{'body':'b'*4000})
        self.assertTrue(delta[0]['content_truncated']);self.assertEqual(delta[0]['after_chars'],4000)
    def test_inference_does_not_hold_capture_lock_or_writer_transaction(self):
        self.store.close();self.store=Store(self.cfg.db_path)
        captured=threading.Event();release=threading.Event();fail=[]
        def slow_report(cfg,store,day,**kw):
            captured.set();release.wait(3)
            return cfg.reports/'test.html'
        def run():
            try:capture_and_report(self.cfg,day=DAY,open_after=False,collect_first=False)
            except Exception as exc:fail.append(exc)
        with patch('workledger.report.write_report',side_effect=slow_report):
            t=threading.Thread(target=run);t.start();self.assertTrue(captured.wait(2))
            try:
                with processing_lock(self.cfg),Store(self.cfg.db_path) as other:
                    other.event('x','during','note',occurred_at=AT,text='collected during inference')
            finally:release.set();t.join(3)
        self.assertFalse(t.is_alive());self.assertFalse(fail)

class ReportAPI(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.cfg=Config(Path(self.temp.name)/'data')
        self.cfg.save({'timezone':'UTC','report_open':False})
        self.server=make_server(self.cfg,port=0);self.port=self.server.server_port
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()
    def request(self,path,body=None):
        req=urllib.request.Request(f'http://127.0.0.1:{self.port}'+path,data=json.dumps(body).encode() if body is not None else None,headers={'Authorization':'Bearer '+self.cfg.token,'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=4) as response:return response.status,json.load(response)
    def test_report_job_returns_202_and_is_reused_while_running(self):
        started=threading.Event();release=threading.Event();result=self.cfg.reports/DAY/'report.html';result.parent.mkdir()
        result.write_text('synthetic');(result.parent/'state.json').write_text('{"analysis_status":"partial"}')
        def slow(*args,**kwargs):started.set();release.wait(3);return result
        with patch('workledger.runtime.capture_and_report',side_effect=slow) as invoke:
            try:
                code,job=self.request('/api/report',{'date':DAY,'open':False});self.assertEqual(code,202)
                self.assertTrue(started.wait(1));code,again=self.request('/api/report',{'date':DAY})
                self.assertEqual(job['id'],again['id']);self.assertEqual(invoke.call_count,1)
                code,status=self.request('/api/report-job?id='+job['id']);self.assertEqual(status['status'],'running')
                release.set()
                for _ in range(40):
                    _,status=self.request('/api/report-job?id='+job['id'])
                    if status['status']=='done':break
                    time.sleep(.025)
                self.assertEqual(status['status'],'done');self.assertEqual(status['analysis_status'],'partial')
            finally:release.set()
    def test_reading_reports_and_status_never_triggers_inference(self):
        with patch('workledger.runtime.capture_and_report',side_effect=AssertionError('GET triggered inference')) as invoke:
            self.request('/api/status');self.request('/api/reports');self.assertEqual(invoke.call_count,0)
    def test_config_api_exposes_analysis_backend_and_budget(self):
        self.request('/api/config',{'llm':{'mode':'opencode','opencode_dir':str(self.cfg.home)},'analysis':{'max_calls':17}})
        _,status=self.request('/api/status');self.assertEqual(status['config']['llm']['mode'],'opencode')
        self.assertEqual(status['config']['analysis']['max_calls'],17)

if __name__=='__main__':unittest.main()
