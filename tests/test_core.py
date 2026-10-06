import io
import json
import os
import plistlib
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from workledger.config import Config, defaults, validate
from workledger.util import stamp, day_bounds, intervals_seconds, content_text, redact, now
from workledger.store import Store
from workledger.adapters.native import parse_codex, parse_claude, parse_pi, parse_dsh
from workledger.adapters.opencode import parse_opencode_export, import_opencode_db
from workledger.adapters.bridge import ingest_bridge, import_chatgpt_export
from workledger.ingest import collect, read_rows
from workledger.documents import structure, changes, collect_documents
from workledger.report import build_report, render_html, root_session, short_result
from workledger.runtime import due_dates
from workledger.macos import launchd_plist, open_output
from workledger.server import make_server, valid_host, valid_origin
from workledger.llm import validate_url, summarize
from workledger.hooks import process_hook

AT='2026-10-06T09:00:00Z'
LATER='2026-10-06T09:30:00Z'

class Base(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.cfg=Config(self.root/'data');self.cfg.save({'timezone':'UTC','report_open':False})
        self.store=Store(self.cfg.db_path)
    def tearDown(self):
        self.store.close();self.temp.cleanup()
    def events(self):
        return self.store.events(*day_bounds('2026-10-06','UTC'))
    def putlog(self,name,rows):
        p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(''.join(json.dumps(r)+'\n' for r in rows));return p

class TimeTests(Base):
    def test_source_time_not_seen(self):self.assertIsNone(stamp('2026-10-06T09:00:00'))
    def test_unix_milliseconds(self):self.assertEqual(stamp(1791277200000),stamp('2026-10-06T09:00:00Z'))
    def test_dst_spring(self):
        a,b=day_bounds('2026-03-08','America/Los_Angeles');self.assertEqual((datetime.fromisoformat(b)-datetime.fromisoformat(a)).total_seconds(),23*3600)
    def test_dst_fall(self):
        a,b=day_bounds('2026-11-01','America/Los_Angeles');self.assertEqual((datetime.fromisoformat(b)-datetime.fromisoformat(a)).total_seconds(),25*3600)
    def test_union_intervals(self):
        a,b=day_bounds('2026-10-06','UTC');self.assertEqual(intervals_seconds([(stamp(AT),stamp(LATER)),(stamp('2026-10-06T09:15:00Z'),stamp('2026-10-06T10:00:00Z'))],a,b),3600)
    def test_timezone_boundary(self):
        self.store.event('test','a','note',occurred_at='2026-10-05T17:00:00Z');self.assertEqual(len(self.store.events(*day_bounds('2026-10-06','Asia/Hong_Kong'))),1);self.assertEqual(self.events(),[])

class StoreTests(Base):
    def test_existing_reader_opens_during_uncommitted_import(self):
        self.store.conn.execute('BEGIN IMMEDIATE')
        self.store.cache_set('in-progress', True)
        try:
            with Store(self.cfg.db_path) as reader:
                self.assertIsNone(reader.cache_get('in-progress'))
                self.assertEqual(reader.summary()['events'], 0)
        finally:
            self.store.conn.rollback()
    def test_dedup_revisions(self):
        eid=self.store.event('x','1','note',occurred_at=AT,text='first')
        self.store.event('x','1','note',occurred_at=AT,text='first')
        self.assertEqual(len(self.events()),1);self.assertEqual(self.store.conn.execute('select count(*) from revisions').fetchone()[0],0)
        self.store.event('x','1','note',occurred_at=AT,text='second');self.assertEqual(self.store.conn.execute('select count(*) from revisions').fetchone()[0],1)
    def test_attribution_audit_survives_import(self):
        eid=self.store.event('x','1','document_change',occurred_at=AT)
        self.store.annotate(eid,'human','confirmed');self.store.event('x','1','document_change',occurred_at=AT,text='changed')
        self.assertEqual(self.store.get_event(eid)['actor'],'human');self.assertEqual(self.store.get_event(eid)['recorded_actor'],'unknown')
    def test_historical_excluded(self):
        self.store.event('x','1','user_message',occurred_at=AT,chronology='historical');self.assertFalse(self.events())
    def test_missing_time_not_today(self):
        self.store.event('x','1','note',observed_at=AT);self.assertFalse(self.events())
    def test_secret_metadata_redacted(self):
        eid=self.store.event('x','s','note',metadata={'api_key':'secret','nested':{'text':'sk-'+('a'*30)}})
        self.assertNotIn('secret',json.dumps(self.store.get_event(eid)['metadata']))
    def test_parent_cycle_safe(self):
        self.store.session('x','a',parent='b',relation='delegation');self.store.session('x','b',parent='a',relation='delegation')
        root,issue=root_session('x:a',self.store.all_sessions());self.assertTrue(issue)
    def test_fork_not_subagent(self):
        self.store.session('x','a');self.store.session('x','b',parent='a',relation='fork')
        self.assertEqual(root_session('x:b',self.store.all_sessions())[0],'x:b')
    def test_missing_parent(self):
        self.store.session('x','b',parent='a',relation='delegation');self.assertTrue(root_session('x:b',self.store.all_sessions())[1])
    def test_self_parent(self):
        self.store.session('x','a',parent='a',relation='delegation');self.assertIsNone(self.store.all_sessions()['x:a']['parent_id'])
    def test_explicit_session_link_survives_native_import(self):
        self.store.session('pi','c',parent='p',relation='delegation',evidence='explicit_session_link')
        self.store.session('pi','c',parent='p',relation='fork')
        self.assertEqual(self.store.all_sessions()['pi:c']['relation'],'delegation')
    def test_message_reclassification_has_one_identity(self):
        first=self.store.event('x','1','user_message',occurred_at=AT,text='task')
        second=self.store.event('x','1','delegated_instruction',occurred_at=AT,text='task',actor='agent')
        self.assertEqual(first,second);self.assertEqual(len(self.events()),1)
    def test_session_title_secret_redacted(self):
        secret='sk-'+('a'*30)
        self.store.session('x','a',title=secret)
        self.assertNotIn(secret,self.store.all_sessions()['x:a']['title'])
        self.store.session('x','a',title='new '+secret)
        self.assertNotIn(secret,self.store.all_sessions()['x:a']['title'])
    def test_invalid_source_paths_rejected(self):
        with self.assertRaises(ValueError):self.cfg.save({'sources':{'codex':{'paths':'/bad'}}})
    def test_private_database_permissions(self):self.assertEqual(self.cfg.db_path.stat().st_mode&0o777,0o600)

class NativeTests(Base):
    def codex(self,failed=False):
        return [
          {'type':'session_meta','payload':{'id':'a','cwd':'/project','source':{'subagent':{'thread_spawn':{'parent_thread_id':'main','depth':1}}}}},
          {'timestamp':AT,'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':'fix test'}]}},
          {'timestamp':AT,'type':'response_item','payload':{'type':'custom_tool_call','call_id':'call1','name':'apply_patch','input':'*** Begin Patch\n*** Update File: a.py\n+x\n*** End Patch'}},
          {'timestamp':LATER,'type':'response_item','payload':{'type':'custom_tool_call_output','call_id':'call1','output':'Error: not permitted' if failed else 'Success. Updated the following files:\nM a.py'}},
        ]
    def test_codex_child(self):
        parse_codex(self.codex(),self.root/'a.jsonl',self.store)
        self.assertEqual(self.store.all_sessions()['codex:a']['parent_id'],'codex:main')
        self.assertEqual(len([e for e in self.events() if e['kind']=='file_edit']),1)
        self.assertEqual(len([e for e in self.events() if e['kind']=='user_message']),0)
    def test_codex_failed_write(self):
        parse_codex(self.codex(True),self.root/'a.jsonl',self.store);self.assertFalse([e for e in self.events() if e['kind']=='file_edit'])
    def test_codex_idempotent(self):
        parse_codex(self.codex(),self.root/'a',self.store);n=len(self.events());parse_codex(self.codex(),self.root/'a',self.store);self.assertEqual(n,len(self.events()))
    def test_codex_event_message_not_duplicate(self):
        rows=self.codex()+[{'timestamp':AT,'type':'event_msg','payload':{'type':'user_message','message':'fix test'}}]
        parse_codex(rows,self.root/'a',self.store);self.assertEqual(sum(e['kind']=='delegated_instruction' for e in self.events()),1)
    def test_claude_tool_result_not_human(self):
        rows=[{'sessionId':'m','uuid':'1','timestamp':AT,'message':{'role':'assistant','content':[{'type':'tool_use','id':'t','name':'Edit','input':{'file_path':'/a.py'}}]}},
              {'sessionId':'m','uuid':'2','parentUuid':'1','timestamp':LATER,'message':{'role':'user','content':[{'type':'tool_result','tool_use_id':'t','content':'saved'}]}}]
        parse_claude(rows,self.root/'m.jsonl',self.store)
        self.assertFalse([e for e in self.events() if e['kind']=='user_message']);self.assertEqual(sum(e['kind']=='file_edit' for e in self.events()),1)
        self.assertIsNone(self.store.all_sessions()['claude:m']['parent_id'])
    def test_claude_subagent_path(self):
        rows=[{'sessionId':'m','uuid':'u','timestamp':AT,'message':{'role':'user','content':'delegated job'}}]
        parse_claude(rows,self.root/'m/subagents/agent-xyz.jsonl',self.store)
        self.assertEqual(self.store.all_sessions()['claude:m/xyz']['parent_id'],'claude:m')
    def test_pi_message_parent_not_session_parent(self):
        rows=[{'type':'session','id':'p','version':3,'timestamp':AT}, {'type':'message','id':'msg','parentId':'old-message','timestamp':AT,'message':{'role':'user','content':[{'type':'text','text':'hello'}]}}]
        parse_pi(rows,self.root/'pi',self.store);self.assertIsNone(self.store.all_sessions()['pi:p']['parent_id'])
    def test_pi_fork_classification(self):
        parent=self.putlog('parent.jsonl',[{'type':'session','id':'parent','version':3}]);parse_pi([{'type':'session','id':'child','version':3,'parentSession':str(parent)}],self.root/'child',self.store)
        self.assertEqual(self.store.all_sessions()['pi:child']['relation'],'fork')
    def test_pi_tools(self):
        rows=[{'type':'session','id':'p','version':3}, {'type':'message','id':'m1','timestamp':AT,'message':{'role':'assistant','content':[{'type':'toolCall','id':'tc','name':'write','arguments':{'path':'/p.py'}}]}}, {'type':'message','id':'m2','timestamp':LATER,'message':{'role':'toolResult','toolCallId':'tc','isError':False,'content':[{'type':'text','text':'saved'}]}}]
        parse_pi(rows,self.root/'p',self.store);self.assertEqual(sum(e['kind']=='file_edit' for e in self.events()),1)
    def test_dsh_seed_filter_and_result_v4(self):
        ms=int(datetime.fromisoformat(stamp(AT)).timestamp()*1000)
        rows=[{'type':'session','version':4,'id':'d','isSeeded':True,'origin':'subagent','parentSession':'main','delegationDepth':1},
              {'seq':0,'time':ms,'type':'user/message','data':{'source':{'kind':'user'},'content':[{'type':'text','text':'old copied task'}]}},
              {'seq':1,'time':ms,'type':'session/end-seed','data':{'inherited':True}},
              {'seq':2,'time':ms,'type':'user/message','data':{'source':{'kind':'user'},'content':[{'type':'text','text':'new task'}]}},
              {'seq':3,'time':ms,'type':'tool/call','data':{'callId':'c','name':'write','arguments':'{"path":"/a.py"}'}},
              {'seq':4,'time':ms,'type':'tool/result','data':{'message':{'role':'user','source':{'kind':'tool','callId':'c'},'content':[{'type':'tool_result','toolCallId':'c','isError':False,'content':[{'type':'text','text':'saved'}]}]}}}]
        parse_dsh(rows,self.root/'d',self.store)
        self.assertNotIn('old copied task',[e['text'] for e in self.events()]);self.assertEqual(sum(e['kind']=='file_edit' for e in self.events()),1)
    def test_dsh_seed_without_cut_refused(self):
        with self.assertRaises(ValueError):parse_dsh([{'version':3,'id':'d','isSeeded':True}],self.root/'d',self.store)
    def test_dsh_injected_context_not_user(self):
        rows=[{'version':3,'id':'d','isSeeded':False}, {'seq':0,'time':int(datetime.fromisoformat(stamp(AT)).timestamp()*1000),'type':'user/message','data':{'source':{'kind':'plugin'},'content':[{'type':'text','text':'injected instructions'}]}}]
        parse_dsh(rows,self.root/'d',self.store);self.assertFalse(self.events())
    def test_dsh_sequence_gap_refused(self):
        with self.assertRaises(ValueError):parse_dsh([{'version':3,'id':'d'}, {'seq':0,'data':{}},{'seq':2,'data':{}}],self.root/'d',self.store)
    def test_dsh_unknown_version(self):
        with self.assertRaises(ValueError):parse_dsh([{'version':99,'id':'d'}],self.root/'d',self.store)
    def test_opencode_export(self):
        data={'info':{'id':'o','parentID':'root','directory':'/project'},'messages':[{'info':{'id':'m','role':'assistant','time':{'created':1791277200000}},'parts':[{'type':'text','text':'result'}, {'type':'tool','callID':'t','tool':'edit','state':{'status':'completed','input':{'filePath':'a.py'},'time':{'start':1791277200000,'end':1791277210000},'output':'done'}}]}]}
        parse_opencode_export(data,self.store);self.assertEqual(self.store.all_sessions()['opencode:o']['relation'],'delegation');self.assertEqual(sum(e['kind']=='file_edit' for e in self.events()),1)
    def test_opencode_live_wal_read_only(self):
        dbpath=self.root/'opencode.db';db=sqlite3.connect(dbpath);db.execute('pragma journal_mode=WAL')
        db.executescript('create table session(id text,parent_id text,directory text,title text);create table message(id text,session_id text,time_created int,data text);create table part(id text,message_id text,data text);')
        db.execute('insert into session values(?,?,?,?)',('s',None,'/p','task'));db.execute('insert into message values(?,?,?,?)',('m','s',1791277200000,json.dumps({'role':'user'})));db.execute('insert into part values(?,?,?)',('p','m',json.dumps({'type':'text','text':'my task'})));db.commit()
        before=dbpath.read_bytes();import_opencode_db(dbpath,self.store);self.assertEqual(before,dbpath.read_bytes());self.assertIn('my task',[e['text'] for e in self.events()]);db.close()
    def test_hook_runs_separate_on_resume(self):
        for at,name in [(AT,'SubagentStart'),(LATER,'SubagentStop'),('2026-10-06T10:00:00Z','SubagentStart'),('2026-10-06T10:30:00Z','SubagentStop')]:
            process_hook({'received_at':at,'payload':{'session_id':'m','agent_id':'a','hook_event_name':name}},self.store)
        self.assertEqual(sum(e['kind']=='run_interval' for e in self.events()),2)

class WebChatTests(Base):
    def test_bridge_human_claim_demoted(self):
        ids=ingest_bridge([{'schema':'workledger.event.v1','source':'vscode','id':'a','kind':'document_change','actor':'human','occurred_at':AT}],self.store)
        self.assertEqual(self.store.get_event(ids[0])['actor'],'unknown')
    def test_bridge_missing_live_timestamp_refused(self):
        with self.assertRaises(ValueError):ingest_bridge([{'schema':'workledger.event.v1','id':'a','kind':'note','chronology':'live_observed'}],self.store)
    def test_old_conversation_new_messages_only(self):
        data={'id':'c','title':'old topic','create_time':1700000000,'update_time':1791277200,'current_node':'new', 'mapping':{'old':{'parent':None,'message':{'id':'old','create_time':1700000000,'author':{'role':'user'},'content':{'parts':['old text']}}}, 'new':{'parent':'old','message':{'id':'new','create_time':1791277200,'author':{'role':'user'},'content':{'parts':['today text']}}}}}
        import_chatgpt_export(data,self.store);self.assertEqual([e['text'] for e in self.events()],['today text'])
    def test_chatgpt_unselected_branch_not_counted(self):
        msg=lambda t:{'message':{'id':t,'create_time':1791277200,'author':{'role':'assistant'},'content':{'parts':[t]}}}
        import_chatgpt_export({'id':'c','current_node':'b','mapping':{'a':msg('a'),'b':msg('b')}},self.store)
        self.assertEqual([e['text'] for e in self.events()],['b'])
    def test_hidden_reasoning_excluded(self):
        self.assertEqual(content_text([{'type':'thinking','thinking':'private'}, {'type':'text','text':'visible'}]),'visible')

class DocumentTests(Base):
    def test_baseline_then_delta_unknown(self):
        project=self.root/'proj';project.mkdir();f=project/'paper.md';f.write_text('old')
        self.cfg.save({'projects':[{'name':'Research','paths':[str(project)]}]})
        collect_documents(self.cfg,self.store);self.assertEqual(self.store.summary()['events'],0)
        f.write_text('new result');collect_documents(self.cfg,self.store)
        rows=self.store.conn.execute('select * from events').fetchall();self.assertEqual(len(rows),1);self.assertEqual(rows[0]['actor'],'unknown')
    def test_excluded_secrets_and_external_symlink(self):
        project=self.root/'p';project.mkdir();(project/'.env').write_text('secret');outside=self.root/'private.txt';outside.write_text('private');(project/'link.txt').symlink_to(outside)
        self.cfg.save({'projects':[{'name':'p','paths':[str(project)]}]});collect_documents(self.cfg,self.store)
        self.assertEqual(self.store.conn.execute('select count(*) from snapshots').fetchone()[0],0)
    def test_ppt_slide_diff(self):
        def ppt(text):
            out=io.BytesIO()
            with zipfile.ZipFile(out,'w') as z:z.writestr('ppt/slides/slide7.xml',f'<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:t>{text}</a:t></p:sld>')
            return out.getvalue()
        a=structure(Path('a.pptx'),ppt('before'));b=structure(Path('a.pptx'),ppt('after'))
        self.assertEqual(len(changes(a,b)),1);self.assertIn('+after',changes(a,b)[0]['diff'])
    def test_xlsx_formula_not_cache(self):
        def book(cache):
            out=io.BytesIO()
            with zipfile.ZipFile(out,'w') as z:z.writestr('xl/worksheets/sheet1.xml',f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row><c r="A1"><f>B1+1</f><v>{cache}</v></c></row></sheetData></worksheet>')
            return out.getvalue()
        self.assertEqual(structure(Path('a.xlsx'),book(1)),structure(Path('a.xlsx'),book(2)))
    def test_zip_bomb_limit(self):
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as z:z.writestr('word/document.xml','x'*1000)
        with self.assertRaises(ValueError):structure(Path('a.docx'),out.getvalue(),limit=100)
    def test_docx_paragraph(self):
        out=io.BytesIO()
        with zipfile.ZipFile(out,'w') as z:z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>Hello</w:t></w:r></w:p></w:document>')
        self.assertEqual(structure(Path('a.docx'),out.getvalue()),{'正文':'Hello'})
    def test_new_file_after_baseline_and_deletion(self):
        project=self.root/'newfiles';project.mkdir()
        self.cfg.save({'projects':[{'name':'p','paths':[str(project)]}]})
        collect_documents(self.cfg,self.store)
        file=project/'new.md';file.write_text('new content')
        collect_documents(self.cfg,self.store)
        self.assertEqual(self.store.summary()['events'],1)
        file.unlink();collect_documents(self.cfg,self.store)
        self.assertEqual(self.store.summary()['events'],2)
    def test_ppt_media_changes(self):
        def ppt(image):
            out=io.BytesIO()
            with zipfile.ZipFile(out,'w') as z:
                z.writestr('ppt/slides/slide1.xml','<a:t xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">same</a:t>')
                z.writestr('ppt/media/image1.png',image)
            return structure(Path('a.pptx'),out.getvalue())
        self.assertTrue(changes(ppt(b'first'),ppt(b'second')))
    def test_no_home_wide_scan(self):
        with self.assertRaises(ValueError):self.cfg.save({'projects':[{'name':'all','paths':[str(Path.home())]}]})

class IngestionTests(Base):
    def test_completed_file_is_committed_before_next_file_failure(self):
        first=self.putlog('first.jsonl',[{}]);second=self.putlog('second.jsonl',[{}])
        sources={name:{**opts,'enabled':False} for name,opts in self.cfg.data['sources'].items()}
        sources['codex'].update(enabled=True,paths=[str(first),str(second)])
        self.cfg.save({'sources':sources})
        observed=[]
        def parser(rows,path,store):
            store.event('test',path.name,'note',occurred_at=AT)
            if path==second.resolve():
                with sqlite3.connect(self.cfg.db_path) as reader:
                    observed.append(reader.execute('SELECT count(*) FROM events').fetchone()[0])
                raise ValueError('synthetic parser failure after partial write')
        with patch.dict('workledger.ingest.PARSERS',{'codex':parser}):
            collect(self.cfg,self.store)
        self.assertEqual(observed,[1])
        self.assertEqual(self.store.summary()['events'],1)
        self.assertEqual(self.events()[0]['native_id'],'first.jsonl')
    def test_partial_last_json_record(self):
        p=self.root/'a';p.write_bytes(b'{"ok":1}\n{"incomplete":');rows,warnings=read_rows(p,1000);self.assertEqual(rows,[{'ok':1,'__workledger_line_number':1}]);self.assertTrue(warnings)
    def test_valid_json_without_newline_is_not_committed(self):
        p=self.root/'validtail';p.write_text('{"ok":1}')
        rows,warnings=read_rows(p,1000);self.assertEqual(rows,[]);self.assertTrue(warnings)
    def test_source_corruption_visible(self):
        p=self.putlog('bad.jsonl',[{'hello':'world'}]);self.cfg.save({'sources':{'codex':{'paths':[str(p)]}}});collect(self.cfg,self.store);self.assertTrue(any('session_meta' in i['detail'] for i in self.store.summary()['issues']))
    def test_pause(self):
        self.cfg.save({'capture_paused':True});self.assertTrue(collect(self.cfg,self.store)['paused'])
    def test_missing_source_visible(self):
        self.cfg.save({'sources':{'codex':{'paths':[str(self.root/'none')]}}});collect(self.cfg,self.store);self.assertTrue(any(i['source']=='codex' for i in self.store.summary()['issues']))

class ReportTests(Base):
    def test_cross_midnight_interval(self):
        self.store.event('x','run','run_interval',session_id='x:a',occurred_at='2026-10-05T23:30:00Z',ended_at='2026-10-06T00:30:00Z',actor='agent')
        r=build_report(self.cfg,self.store,'2026-10-06');self.assertEqual(r['stats']['observed_wall_seconds'],1800)
    def test_html_escapes_payload(self):
        self.store.event('x','evil','note',occurred_at=AT,text='<script>alert(1)</script>',actor='human')
        out=render_html(build_report(self.cfg,self.store,'2026-10-06'));self.assertNotIn('<script>alert(1)</script>',out);self.assertIn('&lt;script&gt;',out)
    def test_model_off_no_request(self):self.assertIsNone(summarize([],{'mode':'off'}))
    def test_remote_model_explicit_consent(self):
        with self.assertRaises(ValueError):validate_url('https://example.com/v1/chat/completions',False)
        self.assertTrue(validate_url('http://127.0.0.1:11434/api/chat',False))
    def test_no_human_time_inferred(self):
        self.store.event('x','u','user_message',occurred_at=AT,actor='unknown',text='task')
        r=build_report(self.cfg,self.store,'2026-10-06');self.assertEqual(r['stats']['human_confirmed'],0);self.assertEqual(r['stats']['observed_wall_seconds'],0)
    def test_parent_reply_preferred_over_child(self):
        self.store.session('x','a');self.store.session('x','b',parent='a',relation='delegation')
        self.store.event('x','1','agent_message',session_id='x:a',occurred_at=AT,text='root conclusion',actor='agent')
        self.store.event('x','2','agent_message',session_id='x:b',occurred_at=LATER,text='child verbose details',actor='agent')
        r=build_report(self.cfg,self.store,'2026-10-06');self.assertEqual(len(r['projects'][0]['agent_results']),1);self.assertEqual(r['projects'][0]['agent_results'][0]['text'],'root conclusion')
    def test_report_summary_bounded(self):
        for i in range(80):self.store.event('x',str(i),'note',actor='human',occurred_at=AT,text='item'+str(i))
        r=build_report(self.cfg,self.store,'2026-10-06');self.assertLessEqual(len(r['projects'][0]['personal']),4)
    def test_short_result_prefers_final_lines(self):
        text='Sure, here is what I did:\nI looked at the code and thought about it.\nDone. Updated the renderer to escape HTML.'
        out=short_result(text);self.assertNotIn('Sure, here is what I did',out);self.assertIn('Done. Updated the renderer',out)
    def test_short_result_preserves_date_prefix(self):
        self.assertTrue(short_result('2026-10-06 fixed login retry bug').startswith('2026-10-06'))
    def test_file_edit_without_artifact_has_text(self):
        self.store.event('x','e1','file_edit',session_id='x:a',occurred_at=AT,actor='agent',artifact='',text='apply_patch 已返回成功')
        r=build_report(self.cfg,self.store,'2026-10-06');self.assertTrue(all(c['text'] for c in r['projects'][0]['agent_files']))

class SchedulingTests(Base):
    def test_no_auto_when_disabled(self):self.assertEqual(due_dates(self.cfg,self.store,'2026-10-06T19:00:00Z'),[])
    def test_runs_once_and_catchup(self):
        self.cfg.save({'schedule':{'enabled':True,'time':'18:30'}})
        self.assertEqual(due_dates(self.cfg,self.store,'2026-10-06T19:00:00Z'),['2026-10-06'])
        self.store.cache_set('scheduled:2026-10-06',AT);self.assertEqual(due_dates(self.cfg,self.store,'2026-10-06T20:00:00Z'),[])
        self.store.cache_set('daemon_previous_tick','2026-10-07T17:00:00+00:00');self.assertEqual(due_dates(self.cfg,self.store,'2026-10-08T10:00:00Z'),['2026-10-07'])
    def test_weekend_skip(self):
        self.cfg.save({'schedule':{'enabled':True}});self.assertEqual(due_dates(self.cfg,self.store,'2026-10-10T20:00:00Z'),[])
    def test_launchd_paths_with_spaces(self):
        data=plistlib.loads(launchd_plist(self.cfg,'/a path/workledger'));self.assertEqual(data['ProgramArguments'][0],'/a path/workledger');self.assertTrue(data['RunAtLoad'])
    def test_open_rejects_external(self):
        with self.assertRaises(ValueError):open_output(self.cfg,Path('/etc/passwd'))

class SecurityTests(Base):
    def test_health_does_not_open_database_or_run_discovery(self):
        server=make_server(self.cfg,port=0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            request=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/health',headers={'Authorization':'Bearer '+self.cfg.token})
            with patch('workledger.server.Store',side_effect=AssertionError('health must not open the database')):
                with urllib.request.urlopen(request,timeout=2) as response:
                    self.assertEqual(json.load(response),{'ready':True})
        finally:
            server.shutdown();server.server_close();thread.join()
    def setUp(self):
        super().setUp();self.server=make_server(self.cfg,port=0);self.port=self.server.server_port;self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();super().tearDown()
    def request(self,path,body=None,token=True,headers=None):
        h={'Authorization':'Bearer '+self.cfg.token} if token else {}
        if body is not None:h['Content-Type']='application/json'
        h.update(headers or {})
        return urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{self.port}'+path,data=json.dumps(body).encode() if body is not None else None,headers=h),timeout=5)
    def test_no_token_denied(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:self.request('/api/status',token=False)
        self.assertEqual(cm.exception.code,401)
    def test_authenticated_status(self):
        with self.request('/api/status') as r:self.assertEqual(r.status,200)
    def test_host_rebinding_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:self.request('/api/status',headers={'Host':'evil.example'})
        self.assertEqual(cm.exception.code,403)
    def test_origin_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:self.request('/api/status',headers={'Origin':'https://evil.example'})
        self.assertEqual(cm.exception.code,403)
    def test_extension_origin_works(self):
        with self.request('/api/status',headers={'Origin':'chrome-extension://abcdef'}) as r:self.assertEqual(r.status,200)
    def test_traversal_rejected(self):
        with self.assertRaises(urllib.error.HTTPError):self.request('/api/open',{'date':'../../etc'})
    def test_batch_transaction_rollback(self):
        rows=[{'schema':'workledger.event.v1','source':'x','id':'ok','kind':'note','occurred_at':AT},{'bad':'row'}]
        with self.assertRaises(urllib.error.HTTPError):self.request('/api/events',{'events':rows})
        self.assertEqual(self.store.summary()['events'],0)
    def test_roundtrip_message(self):
        with self.request('/api/events',{'events':[{'schema':'workledger.event.v1','source':'x','id':'ok','kind':'note','occurred_at':AT,'text':'hello'}]}) as r:self.assertEqual(json.load(r)['accepted'],1)
        self.assertEqual(self.events()[0]['text'],'hello')

if __name__=='__main__':unittest.main()
