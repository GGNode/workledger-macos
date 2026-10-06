"""Run real subprocess contracts against an explicitly fake OpenCode executable.

No credentials/providers are required and no network requests are made.
"""
import json
import os
import signal
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from workledger.config import Config
from workledger.analysis.backend import ModelClient, AnalysisError, bounded_process, restricted_environment, classify_error


def object_validator(obj):
    if not isinstance(obj,dict) or obj.get('ok') is not True:
        raise ValueError('Expected ok object')
    return obj

class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.cfg=Config(self.root/'data');self.directory=self.root/'normal project with spaces';self.directory.mkdir()
        self.exe=self.root/'fake-opencode';self.captured=self.root/'captured.json'
        self.cfg.save({'llm':{'mode':'opencode','opencode_executable':str(self.exe),'opencode_dir':str(self.directory)},'analysis':{'retries':1}})
    def tearDown(self):self.temp.cleanup()
    def program(self,body):
        self.exe.write_text('#!'+sys.executable+'\n'+body);self.exe.chmod(0o755)
    def respond(self,extra=''):
        self.program('import os,sys,json\nfrom pathlib import Path\nraw=sys.stdin.read()\n'+extra+'\nprint(json.dumps({"type":"text","sessionID":"ses_synthetic","part":{"id":"txt","text":json.dumps({"ok":True})}}),flush=True)\n')
    def request(self,client=None,data=None):
        return (client or ModelClient(self.cfg)).request('test',data or {'evidence':'synthetic'},'Return JSON',object_validator)
    def assert_error(self,code,action):
        with self.assertRaises(AnalysisError) as cm:action()
        self.assertEqual(cm.exception.code,code)
    def test_explicit_dir_normal_environment_and_no_hardcoded_model(self):
        self.respond('Path('+repr(str(self.captured))+').write_text(json.dumps({"argv":sys.argv,"cwd":os.getcwd(),"HOME":os.environ.get("HOME"),"XDG_CONFIG_HOME":os.environ.get("XDG_CONFIG_HOME"),"inline":json.loads(os.environ["OPENCODE_CONFIG_CONTENT"]),"permission":os.environ["OPENCODE_PERMISSION"],"input":raw}))')
        user_inline={'plugin':['normal-plugin'],'model':'normal-provider/normal-model','agent':{'custom':{'permission':'allow'}}}
        with patch.dict(os.environ,{'OPENCODE_CONFIG_CONTENT':json.dumps(user_inline),'XDG_CONFIG_HOME':str(self.root/'ordinary config')}):
            self.assertEqual(self.request(),{'ok':True})
            row=json.loads(self.captured.read_text())
            self.assertEqual(row['HOME'],os.environ['HOME']);self.assertEqual(row['XDG_CONFIG_HOME'],os.environ['XDG_CONFIG_HOME'])
            self.assertEqual(json.loads(os.environ['OPENCODE_CONFIG_CONTENT']),user_inline)
        argv=row['argv'];self.assertEqual(argv[argv.index('--dir')+1],str(self.directory.resolve()));self.assertEqual(Path(row['cwd']).resolve(),self.directory.resolve())
        self.assertNotIn('--model',argv);self.assertNotIn('--pure',argv);self.assertNotIn('--continue',argv)
        self.assertTrue(row['input'].startswith('WORKLEDGER_ANALYSIS_RUN='));self.assertNotIn('synthetic',' '.join(argv))
        self.assertEqual(row['inline']['plugin'],['normal-plugin']);self.assertEqual(row['inline']['model'],user_inline['model'])
        agent=row['inline']['agent'][argv[argv.index('--agent')+1]]
        self.assertNotIn('model',agent);self.assertEqual(agent['permission'],{'*':'deny'})
        self.assertEqual(row['inline']['share'],'disabled')
        self.assertEqual(json.loads(row['permission']),{'*':'deny'})
        self.assertFalse((self.cfg.home/'analysis'/'auth.json').exists())
    def test_explicit_model_is_optional_not_hardcoded(self):
        self.cfg.save({'llm':{'model':'chosen/provider-model'}})
        self.respond('Path('+repr(str(self.captured))+').write_text(json.dumps(sys.argv))')
        self.request();argv=json.loads(self.captured.read_text())
        self.assertEqual(argv[argv.index('--model')+1],'chosen/provider-model')
    def test_session_registered_before_process_and_retained_after(self):
        self.respond('runs=list(Path('+repr(str(self.cfg.home/'analysis/runs'))+').glob("*.json"));assert runs and "finished_at" not in json.loads(runs[0].read_text())')
        self.request();rows=list((self.cfg.home/'analysis/runs').glob('*.json'))
        self.assertEqual(len(rows),1);row=json.loads(rows[0].read_text())
        self.assertEqual(row['session_ids'],['ses_synthetic']);self.assertIn('finished_at',row)
        self.assertNotIn('evidence',row)
    def test_missing_cli_error_classification(self):
        self.assert_error('unavailable',lambda:self.request())
    def test_free_tier_policy_is_not_misreported_as_authentication(self):
        self.assertEqual(classify_error('{"name":"APIError","data":{"statusCode":403,"message":"OpenCode\'s free tier can only be used from within OpenCode","responseBody":"FreeTierError"}}'),'provider_policy')
        self.assertEqual(classify_error('401 Unauthorized'),'authentication')
    def test_nonexistent_dir_is_explicit_error(self):
        self.respond();self.cfg.save({'llm':{'opencode_dir':str(self.root/'not-existing')}})
        self.assert_error('directory',lambda:self.request())
    def test_tool_use_is_rejected_and_session_id_preserved(self):
        self.program('import json,sys,time\nsys.stdin.read()\nprint(json.dumps({"type":"tool_use","sessionID":"bad-session","part":{"tool":"bash"}}),flush=True)\ntime.sleep(5)\n')
        started=time.monotonic();self.assert_error('tool_attempt',lambda:self.request())
        self.assertLess(time.monotonic()-started,2)
        row=json.loads(next((self.cfg.home/'analysis/runs').glob('*.json')).read_text())
        self.assertEqual(row['session_ids'],['bad-session'])
    def test_stream_non_json_is_not_assumed_success(self):
        self.program('print("not-json",flush=True)')
        self.assert_error('invalid_json',lambda:self.request())
    def test_stream_empty_is_invalid_not_a_report(self):
        self.program('pass')
        self.assert_error('invalid_json',lambda:self.request())
    def test_invalid_executable_format_is_classified(self):
        self.exe.write_text('not an executable image');self.exe.chmod(0o755)
        self.assert_error('configuration',lambda:self.request())
    def test_output_bound_stops_process(self):
        self.cfg.save({'analysis':{'max_output_bytes':1024}});self.program('print("x"*4000,flush=True)')
        self.assert_error('output_limit',lambda:self.request())
    def test_provider_auth_error_not_retried_for_every_task(self):
        self.program('import json\nprint(json.dumps({"type":"error","sessionID":"auth-fail","error":{"message":"401 Unauthorized"}}))')
        client=ModelClient(self.cfg);self.assert_error('authentication',lambda:self.request(client))
        self.assertEqual(client.calls,1)
    def test_timeout_is_bounded_and_does_not_retry(self):
        self.program('import time\ntime.sleep(8)');self.cfg.save({'llm':{'timeout':1}})
        started=time.monotonic();client=ModelClient(self.cfg)
        self.assert_error('timeout',lambda:self.request(client));self.assertLess(time.monotonic()-started,3)
        self.assertEqual(client.calls,1)
    def test_cache_reuses_valid_response_no_cli_relaunch(self):
        self.respond();client=ModelClient(self.cfg);self.request(client);self.request(client)
        self.assertEqual(client.calls,1);self.assertEqual(client.hits,1)
        self.assertEqual(len(list((self.cfg.home/'analysis/runs').glob('*.json'))),1)
    def test_new_client_reuses_cache(self):
        self.respond();self.request();client=ModelClient(self.cfg);self.request(client)
        self.assertEqual(client.calls,0);self.assertEqual(client.hits,1)
    def test_normalized_output_does_not_destroy_cache_validation_receipt(self):
        client=ModelClient(self.cfg)
        def validator(obj):
            if obj.get('covered_item_ids') != ['item-1']:raise ValueError('receipt missing')
            return {'ok':True}
        with patch.object(client,'_invoke',return_value='{"ok":true,"covered_item_ids":["item-1"]}') as invoke:
            for _ in range(2):client.request('theme',{'items':['item-1']},'JSON',validator)
            self.assertEqual(invoke.call_count,1);self.assertEqual(client.hits,1)
    def test_changed_evidence_invalidates_cache(self):
        self.respond();client=ModelClient(self.cfg);self.request(client,{'id':'a'});self.request(client,{'id':'b'})
        self.assertEqual(client.calls,2)
    def test_refresh_bypasses_cache(self):
        self.respond();self.request();client=ModelClient(self.cfg,refresh=True);self.request(client)
        self.assertEqual(client.calls,1);self.assertEqual(client.hits,0)
    def test_invalid_json_repair_is_bounded(self):
        client=ModelClient(self.cfg)
        with patch.object(client,'_invoke',side_effect=['not json','{"ok":true}']) as invoke:
            self.assertEqual(self.request(client),{'ok':True});self.assertEqual(invoke.call_count,2)
    def test_illegal_schema_repair_is_bounded(self):
        client=ModelClient(self.cfg)
        with patch.object(client,'_invoke',return_value='{"wrong":true}') as invoke:
            self.assert_error('schema',lambda:self.request(client));self.assertEqual(invoke.call_count,2)
    def test_invalid_cached_data_is_revalidated(self):
        self.respond();self.request()
        cache=next((self.cfg.home/'analysis/cache').glob('*.json'));obj=json.loads(cache.read_text());obj['result']={'wrong':True};cache.write_text(json.dumps(obj))
        client=ModelClient(self.cfg);self.request(client);self.assertEqual(client.calls,1);self.assertEqual(client.hits,0)
    def test_failure_cooldown_persists_between_requests(self):
        client=ModelClient(self.cfg);self.assert_error('unavailable',lambda:self.request(client))
        next_client=ModelClient(self.cfg);self.assert_error('unavailable',lambda:self.request(next_client));self.assertEqual(next_client.calls,0)
    def test_global_call_budget_is_enforced(self):
        self.respond();self.cfg.save({'analysis':{'max_calls':1}});client=ModelClient(self.cfg)
        self.request(client,{'id':'a'});self.assert_error('budget',lambda:self.request(client,{'id':'b'}))
    def test_timeout_budget_counts_real_elapsed_time(self):
        self.cfg.save({'analysis':{'total_timeout':1}});client=ModelClient(self.cfg);client.started-=2
        self.assert_error('budget',lambda:self.request(client));self.assertEqual(client.calls,0)
    def test_malformed_inherited_config_is_not_silently_discarded(self):
        self.assert_error('configuration',lambda:restricted_environment({'OPENCODE_CONFIG_CONTENT':'not json'},'private-agent'))
    def test_normal_settings_and_plugins_preserved(self):
        base={'HOME':'/normal','XDG_DATA_HOME':'/normal/data','OPENCODE_CONFIG':'/normal/settings','OPENCODE_CONFIG_DIR':'/normal/custom','SENTINEL':'unchanged'}
        out=restricted_environment(base,'analysis-x')
        for k,v in base.items():self.assertEqual(out[k],v)
        self.assertNotIn('XDG_CONFIG_HOME',out);self.assertNotIn('OPENCODE_DISABLE_PLUGINS',out)
    def test_error_categories_are_distinct(self):
        self.assertEqual(classify_error('429 too many requests'),'rate_limit')
        self.assertEqual(classify_error('unknown option --dir'),'configuration')
        self.assertEqual(classify_error('connection refused'),'provider')
    def test_config_rejects_invalid_budgets_and_bool_timeout(self):
        for update in [{'llm':{'timeout':True}},{'analysis':{'max_calls':0}},{'analysis':{'retries':99}}]:
            with self.assertRaises(ValueError):self.cfg.save(update)
    def test_partial_and_repeated_text_parts_keep_final_value(self):
        self.program('import json\nfor text in ["not yet",json.dumps({"ok":True})]:\n print(json.dumps({"type":"text","sessionID":"s","part":{"id":"same","text":text}}),flush=True)\n')
        self.assertEqual(self.request(),{'ok':True})

if __name__=='__main__':unittest.main()
