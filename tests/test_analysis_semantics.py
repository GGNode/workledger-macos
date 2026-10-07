"""Semantic acceptance contracts with synthetic expected responses.

These tests reject false attribution/unsupported completion and check content
organization. They do not claim a live model necessarily reasons correctly.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workledger.config import Config
from workledger.store import Store
from workledger.util import day_bounds
from workledger.report import build_report, render_html, render_markdown
from workledger.analysis import schema
from workledger.analysis.backend import AnalysisError
from workledger.analysis.evidence import prepare, packet_input, native_root, exclusions
from support_analysis import load_fixture, ExpectedResponseReplay

DAY='2026-10-06'
AT='2026-10-06T09:00:00Z'
LATER='2026-10-06T10:00:00Z'

class Base(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.cfg=Config(self.root/'data');self.cfg.save({'timezone':'UTC','report_open':False})
        self.store=Store(self.cfg.db_path)
    def tearDown(self):
        self.store.close();self.temp.cleanup()
    def events(self):return self.store.events(*day_bounds(DAY,'UTC'))
    def plan(self):return prepare(self.cfg,self.store,self.events(),self.store.all_sessions(),DAY)
    def fixture_report(self):
        self.cfg.save({'llm':{'mode':'opencode'},'projects':[{'name':'WorkLedger','paths':['/synthetic/workledger']}]})
        fixture,aliases,tasks=load_fixture(self.cfg,self.store)
        self.fixture,self.aliases,self.tasks=fixture,aliases,tasks
        self.replay=ExpectedResponseReplay(fixture,aliases,tasks)
        return build_report(self.cfg,self.store,DAY,analysis_client=self.replay)

class ReadabilityTests(Base):
    def test_entire_synthetic_day_is_analyzed_before_cards(self):
        r=self.fixture_report();a=r['analysis']
        self.assertEqual(a['status'],'complete');self.assertEqual(len(a['themes']),4)
        self.assertEqual(a['coverage']['evidence_analyzed'],a['coverage']['today_evidence'])
        self.assertEqual(a['coverage']['tasks_analyzed'],6)
        self.assertEqual(self.replay.stages,['map']*6+['route','theme','theme','theme','theme','day'])
    def test_unanalyzed_observations_stay_out_of_main_narrative(self):
        r=self.fixture_report();theme=copy.deepcopy(r['analysis']['themes'][0])
        theme.update(id='pending-only',title='Synthetic pending observations',analysis_status='observation_only')
        r['analysis']['themes'].append(theme);r['analysis']['status']='partial'
        html=render_html(r);main=html.split('id="main-narrative"',1)[1].split('class="evidence-area"',1)[0]
        self.assertNotIn(theme['title'],main)
        folded=html.split('id="unanalyzed"',1)[1].split('</details>',1)[0]
        self.assertIn(theme['title'],folded)
        md=render_markdown(r);self.assertNotIn(theme['title'],md.split('<details><summary>尚未形成语义分析的记录</summary>',1)[0])
        self.assertIn(theme['title'],md);self.assertEqual(r['analysis']['themes'][-1],theme)
    def test_semantic_merging_never_rewrites_native_lineage(self):
        before=None
        r=self.fixture_report();before=self.store.all_sessions()
        ledger=r['analysis']['themes'][0]
        self.assertGreaterEqual(len(ledger['task_ids']),2)
        self.assertEqual(before,self.store.all_sessions())
        self.assertEqual(before['opencode:ledger-child']['relation'],'delegation')
        self.assertIsNone(before['codex:ledger-tests']['parent_id'])
    def test_main_view_answers_progress_and_impact_without_raw_log(self):
        r=self.fixture_report();html=render_html(r)
        main=html.split('id="main-narrative"',1)[1].split('class="evidence-area"',1)[0]
        for noise in ['Objective:','PROBE_OK','apply_patch: failed','FileNotFoundError']:
            self.assertNotIn(noise,main)
        for meaningful in ['42','标签','验证','本人确认','用户推动','待验证']:
            self.assertIn(meaningful,main)
        self.assertIn('缺少',main)
    def test_markdown_contains_same_analysis_not_legacy_cards(self):
        r=self.fixture_report();md=render_markdown(r)
        for t in r['analysis']['themes']:
            self.assertIn(t['title'],md)
            for section in schema.SECTIONS:
                for c in t[section]:self.assertIn(c['text'],md)
        self.assertNotIn('Objective:',md);self.assertNotIn('PROBE_OK',md)
        self.assertNotIn('apply_patch: failed',md)
    def test_all_important_claims_have_real_evidence_targets(self):
        r=self.fixture_report();html=render_html(r);known=r['analysis']['evidence']
        for theme in r['analysis']['themes']:
            for claim in schema.item_claims(theme):
                for eid in claim['evidence_ids']:
                    self.assertIn(eid,known);self.assertIn('id="e-'+eid+'"',html)
    def test_recovered_failure_is_folded_but_blocker_visible(self):
        r=self.fixture_report();from workledger.analysis.render import main_issues
        self.assertEqual(main_issues(r['analysis']['themes'][0]),[])
        blocks=main_issues(r['analysis']['themes'][1]);self.assertEqual(len(blocks),1)
        self.assertEqual(blocks[0]['state'],'open');self.assertEqual(blocks[0]['severity'],'blocking')
    def test_different_unconfigured_topics_are_not_other_work_blob(self):
        r=self.fixture_report()
        self.assertNotEqual(r['analysis']['themes'][0]['title'],r['analysis']['themes'][1]['title'])
        self.assertIn('谱',r['analysis']['themes'][1]['title'])
        self.assertNotIn('其他工作',[t['title'] for t in r['analysis']['themes']])
    def test_request_only_is_not_a_result(self):
        r=self.fixture_report();t=r['analysis']['themes'][2]
        self.assertTrue(t['work']);self.assertEqual(t['results'],[]);self.assertTrue(t['remaining'])
    def test_agent_score_claim_remains_unverified(self):
        r=self.fixture_report();t=r['analysis']['themes'][1]
        self.assertTrue(any(c['basis']=='agent_claim' for c in t['results']))
        self.assertFalse(any(c['basis']=='tool_observed' and '0.41' in c['text'] for c in t['results']))
    def test_old_context_is_not_counted_as_today_completion(self):
        r=self.fixture_report();a=r['analysis']
        self.assertTrue(any(e['scope']=='history' for e in a['evidence'].values()))
        self.assertTrue(all(c['scope']=='today' for t in a['themes'] for c in t['results']))
    def test_self_sessions_excluded_but_real_development_retained(self):
        r=self.fixture_report();by=r['analysis']['evidence']
        self.assertNotIn('opencode:analysis-self',{e.get('session_id') for e in by.values()})
        self.assertNotIn('opencode:analysis-self-child',{e.get('session_id') for e in by.values()})
        self.assertIn('opencode:ledger-review',{e.get('session_id') for e in by.values()})
    def test_no_human_confirmation_is_not_no_work(self):
        self.store.event('x','request','user_message',occurred_at=AT,text='An important request',actor='unknown')
        r=build_report(self.cfg,self.store,DAY)
        self.assertEqual(r['stats']['human_confirmed'],0)
        self.assertTrue(r['analysis']['themes'])
        self.assertIn('不代表本人没有工作',render_html(r))
    def test_only_unknown_document_change_does_not_become_personal_work(self):
        self.store.event('documents','d','document_change',occurred_at=AT,artifact='/synthetic/docs/outline.docx',actor='unknown',text='Changed')
        r=build_report(self.cfg,self.store,DAY);t=r['analysis']['themes'][0]
        self.assertEqual(t['results'],[]);self.assertEqual(t['remaining'][0]['basis'],'unverified_change')
        self.assertEqual(r['analysis']['status'],'disabled')
    def test_off_never_calls_client(self):
        class NoCalls:
            def request(self,*a,**k):raise AssertionError('off called model')
        self.store.event('x','n','note',occurred_at=AT,actor='human',text='record')
        r=build_report(self.cfg,self.store,DAY,analysis_client=NoCalls())
        self.assertEqual(r['analysis']['status'],'disabled')

class AssertionGuards(Base):
    def setUp(self):
        super().setUp()
        self.store.session('x','s')
        self.refs={}
        for key,kind,actor,meta,at in [('ask','user_message','unknown',{},AT),('say','agent_message','agent',{},LATER),('bad','tool_result','agent',{'success':False},AT),('ok','tool_result','agent',{'success':True},LATER),('past','tool_result','agent',{'success':True},'2026-10-05T09:00:00Z'),('doc','document_change','unknown',{},AT),('person','note','human',{},AT)]:
            eid=self.store.event('x',key,kind,session_id='x:s',actor=actor,metadata=meta,occurred_at=at,text=key)
            self.refs[key]=eid
        self.ev=self.plan()['evidence']
    def c(self,key,basis='inference',scope='today'):
        return {'text':'一条需要核查的结论','evidence_ids':[self.refs[key]],'basis':basis,'scope':scope}
    def test_unknown_reference_rejected(self):
        c=self.c('ask');c['evidence_ids']=['NOT_REAL']
        with self.assertRaises(ValueError):schema.claim(c,self.ev)
    def test_request_cannot_be_completed_result_even_as_inference(self):
        with self.assertRaises(ValueError):schema.claim(self.c('ask'),self.ev,section='results')
    def test_agent_statement_is_not_successful_tool_evidence(self):
        with self.assertRaises(ValueError):schema.claim(self.c('say','tool_observed'),self.ev)
    def test_user_channel_is_not_human_authorship(self):
        with self.assertRaises(ValueError):schema.claim(self.c('ask','human_confirmed'),self.ev)
    def test_history_alone_cannot_support_today_claim(self):
        with self.assertRaises(ValueError):schema.claim(self.c('past','tool_observed'),self.ev)
    def test_historical_results_cannot_be_today_results(self):
        with self.assertRaises(ValueError):schema.claim(self.c('past','tool_observed','history'),self.ev,section='results')
    def problem(self,fix):
        return {'state':'resolved','severity':'routine','problem':self.c('bad'),'impact':self.c('bad'),'resolution':fix}
    def test_single_tool_failure_is_not_definitive_task_blocker(self):
        obj={'state':'open','severity':'blocking','problem':self.c('bad'),'impact':self.c('bad'),'resolution':None}
        self.assertEqual(schema.issue(obj,self.ev)['state'],'uncertain')
    def test_last_done_is_not_recovery(self):
        out=schema.issue(self.problem(self.c('say','agent_claim')),self.ev)
        self.assertEqual(out['state'],'uncertain')
    def test_later_tool_result_can_support_recovery(self):
        out=schema.issue(self.problem(self.c('ok','tool_observed')),self.ev)
        self.assertEqual(out['state'],'resolved')
    def test_earlier_success_is_not_recovery(self):
        out=schema.issue(self.problem(self.c('past','tool_observed','history')),self.ev)
        self.assertEqual(out['state'],'uncertain')
    def test_accounted_but_uncited_is_not_coverage(self):
        obj={'items':[],'accounted_ids':[self.refs['ask']],'ignored':[]}
        with self.assertRaises(ValueError):schema.validate_map(obj,self.ev,{'x:s'},{self.refs['ask']})
    def test_route_must_include_each_item_exactly_once(self):
        for groups in [[],[{'title':'T','item_ids':['a']}],[{'title':'T','item_ids':['a','b']},{'title':'U','item_ids':['b']}]]:
            with self.assertRaises(ValueError):schema.validate_routes({'groups':groups},{'a','b'})
    def test_resolved_requires_explicit_resolution(self):
        with self.assertRaises(ValueError):schema.issue(self.problem(None),self.ev)
    def test_raw_objective_is_not_valid_heading(self):
        with self.assertRaises(ValueError):schema.sentence('Objective: implement X')
    def test_scope_and_basis_survive_roundtrip(self):
        out=schema.claim(self.c('person','human_confirmed'),self.ev)
        self.assertEqual(out['basis'],'human_confirmed');self.assertEqual(out['scope'],'today')

class EvidencePlanningTests(Base):
    def test_large_event_keeps_tail_and_is_split_not_first_60_facts(self):
        text='A'*70000+'TAIL_CONCLUSION'
        eid=self.store.event('x','long','agent_message',occurred_at=AT,actor='agent',text=text)
        plan=self.plan();parts=[r for packet in plan['packets'] for r in packet if r['id']==eid]
        self.assertGreater(len(parts),1)
        self.assertIn('TAIL_CONCLUSION',''.join(r['content'] for r in parts))
    def test_many_tasks_are_round_robin_and_stratified_across_day(self):
        self.cfg.save({'analysis':{'chunk_chars':3000}})
        for i in range(100):
            self.store.session('x',str(i),cwd='/synthetic/shared')
            self.store.event('x',str(i),'user_message',session_id='x:'+str(i),occurred_at=f'2026-10-06T09:{i//60:02}:{i%60:02}Z',text='A'*5000,actor='unknown')
        p=self.plan();first=[r for packet in p['packets'][:100] for r in packet]
        self.assertEqual(len({r['task_id'] for r in first}),100)
        self.assertTrue(all(packet[0]['part']==1 for packet in p['packets'][:100]))
        self.assertTrue(all(len({r['task_id'] for r in packet})==1 for packet in p['packets']))
        indices=[int(r['task_id'].split(':')[1]) for r in first[:5]]
        self.assertLess(min(indices),30);self.assertGreater(max(indices),70)
    def test_partial_long_event_is_not_counted_fully_analyzed(self):
        self.cfg.save({'llm':{'mode':'opencode'},'analysis':{'chunk_chars':3000,'max_map_packets':1}})
        eid=self.store.event('x','l','agent_message',occurred_at=AT,actor='agent',text='z'*15000)
        class ContractOnly:
            calls=0;hits=0;actual_models=set()
            def request(self,stage,data,instruction,validator):
                self.calls+=1
                return validator({'items':[],'accounted_ids':[r['id'] for r in data['records']], 'ignored':[{'evidence_ids':[r['id'] for r in data['records']],'reason':'routine'}]})
        a=build_report(self.cfg,self.store,DAY,analysis_client=ContractOnly())['analysis']
        self.assertIn(eid,a['coverage']['missing_evidence_ids']);self.assertEqual(a['coverage']['evidence_analyzed'],0)
    def test_source_truncation_is_visible(self):
        eid=self.store.event('x','oversize','agent_message',occurred_at=AT,actor='agent',text='x'*140000)
        p=self.plan();self.assertIn(eid,p['truncated_evidence_ids'])
        self.assertLess(len(p['evidence'][eid]['text']),140000)
    def test_legacy_tool_excerpt_is_a_coverage_gap(self):
        eid=self.store.event('x','legacy','tool_result',occurred_at=AT,actor='agent',metadata={'output_excerpt':'old limited text'})
        self.assertIn(eid,self.plan()['truncated_evidence_ids'])
    def test_bounded_history_reports_omitted_rows(self):
        self.cfg.save({'analysis':{'history_events_per_task':2}});self.store.session('x','root')
        for i in range(9):self.store.event('x','old'+str(i),'agent_message',session_id='x:root',occurred_at=f'2026-10-05T09:00:0{i}Z',actor='agent',text='old')
        self.store.event('x','new','user_message',session_id='x:root',occurred_at=AT,actor='unknown',text='today')
        p=self.plan();self.assertEqual(p['history_omitted'],7)
        self.assertEqual(sum(e['scope']=='history' for e in p['evidence'].values()),2)
    def test_later_chunks_have_same_day_goal_and_feedback_context(self):
        self.cfg.save({'analysis':{'chunk_chars':3000}});self.store.session('x','root')
        request=self.store.event('x','goal','user_message',session_id='x:root',occurred_at=AT,text='The purpose of this task',actor='unknown')
        self.store.event('x','many','agent_message',session_id='x:root',occurred_at=LATER,text='large '*4000,actor='agent')
        p=self.plan();data=packet_input(p,p['packets'][-1],self.cfg)
        self.assertIn(request,[e['id'] for e in data['context']])
        self.assertEqual(next(e for e in data['context'] if e['id']==request)['scope'],'today')
    def test_packet_task_cardinality_is_bounded_for_large_inputs(self):
        for i in range(120):
            self.store.session('x',str(i));self.store.event('x',str(i),'user_message',session_id='x:'+str(i),occurred_at=AT,text='A different small task',actor='unknown')
        p=self.plan()
        self.assertTrue(all(len({r['task_id'] for r in b})<=16 for b in p['packets']))
        self.assertEqual(len({r['task_id'] for b in p['packets'] for r in b}),120)
    def test_partial_map_retains_valid_claims_without_counting_omitted_records(self):
        kept=self.store.event('x','kept','note',occurred_at=AT,text='Confirmed synthetic work',actor='human')
        omitted=self.store.event('x','omitted','user_message',occurred_at=AT,text='Independent synthetic requirement')
        p=self.plan();row=p['evidence'][kept]
        value={'items':[{'title':'Synthetic confirmed work','task_ids':[row['task_id']],'work':[{'text':'Confirmed work','evidence_ids':[kept],'basis':'human_confirmed','scope':'today'}],'results':[],'remaining':[],'suggestions':[],'issues':[]}],'accounted_ids':[kept,omitted],'ignored':[]}
        with self.assertRaises(ValueError):schema.validate_map(value,p['evidence'],p['tasks'],{kept,omitted})
        out=schema.validate_map(value,p['evidence'],p['tasks'],{kept,omitted},allow_partial=True)
        self.assertEqual(out['unaccounted_ids'],[omitted]);self.assertEqual(out['items'][0]['work'][0]['evidence_ids'],[kept])
        bad=copy.deepcopy(value);bad['items'][0]['work'][0]['evidence_ids']=['unknown']
        with self.assertRaises(ValueError):schema.validate_map(bad,p['evidence'],p['tasks'],{kept,omitted},allow_partial=True)
    def test_early_packet_includes_later_delivery_without_changing_time_or_actor(self):
        request=self.store.event('x','request','user_message',occurred_at=AT,text='Write the synthetic review',session_id='task')
        delivery=self.store.event('x','delivery','agent_message',occurred_at=LATER,text='Synthetic review delivered; no independent runtime test',session_id='task',actor='agent')
        write=self.store.event('x','write','file_edit',occurred_at=LATER,text='synthetic review written',session_id='task',actor='agent',evidence='successful_tool_result')
        p=self.plan();r=next(r for batch in p['packets'] for r in batch if r['id']==request)
        data=packet_input(p,[r],self.cfg);context={e['id']:e for e in data['context']}
        self.assertIn(delivery,context);self.assertIn(write,context)
        self.assertEqual(context[delivery]['at'],p['evidence'][delivery]['occurred_at']);self.assertEqual(context[delivery]['actor'],'agent')
        self.assertEqual(context[delivery]['scope'],'today');self.assertTrue(data['tasks'][0]['partial_task_input'])
        self.assertEqual(context[write]['evidence'],'successful_tool_result')
    def test_undated_web_history_does_not_become_today(self):
        self.store.event('chatgpt','old','agent_message',observed_at=AT,actor='agent',text='old without creation time')
        self.assertEqual(self.plan()['today_ids'],[])
    def test_fork_is_not_delegation(self):
        self.store.session('x','a');self.store.session('x','b',parent='a',relation='fork')
        self.assertEqual(native_root('x:b',self.store.all_sessions()),'x:b')
    def test_generated_artifacts_excluded_not_source_code(self):
        bad=self.store.event('docs','out','document_change',occurred_at=AT,artifact=str(self.cfg.home/'reports/day/report.html'))
        good=self.store.event('docs','src','document_change',occurred_at=AT,artifact='/synthetic/workledger/report.py')
        ex=exclusions(self.cfg,self.events(),self.store.all_sessions())
        self.assertIn(bad,ex);self.assertNotIn(good,ex)
    def test_exact_probe_only_not_session_discussing_probe(self):
        probe=self.store.event('x','p','agent_message',occurred_at=AT,text='PROBE_OK')
        real=self.store.event('x','r','user_message',occurred_at=AT,text='Remove PROBE_OK from our report')
        p=self.plan();self.assertNotIn(probe,p['today_ids']);self.assertIn(real,p['today_ids'])
    def test_all_classified_noise_is_not_provider_failure(self):
        self.cfg.save({'llm':{'mode':'opencode'}})
        self.store.event('x','n','agent_message',occurred_at=AT,actor='agent',text='Session started')
        class IgnoreOnly:
            def request(self,stage,data,instruction,validator):
                refs=[r['id'] for r in data['records']]
                return validator({'items':[],'accounted_ids':refs,'ignored':[{'evidence_ids':refs,'reason':'routine'}]})
        a=build_report(self.cfg,self.store,DAY,analysis_client=IgnoreOnly())['analysis']
        self.assertEqual(a['status'],'complete');self.assertEqual(a['themes'],[])

if __name__=='__main__':unittest.main()
