"""Synthetic replies test recovery; all replies pass the real claim validator."""
import copy
import unittest
from workledger.analysis.mapping import request_map
from workledger.analysis.backend import AnalysisError


def claim(eid, basis='inference'):
    return {'text':'Synthetic work statement.', 'evidence_ids':[eid], 'basis':basis, 'scope':'today'}


def result(ids, statements=None, ignored=None, issues=None):
    items=[]
    if statements or issues:
        items=[{'title':'Synthetic work','task_ids':['t'],'work':statements or [],'results':[],
                'remaining':[], 'suggestions':[], 'issues':issues or []}]
    return {'items':items, 'accounted_ids':ids, 'ignored':ignored or []}


class Client:
    def __init__(self, replies):
        self.replies=iter(replies);self.calls=[];self.instructions=[];self.started='unchanged';self.disabled_until=7
    def request(self, stage, data, instruction, validator):
        self.calls.append(copy.deepcopy(data));self.instructions.append(instruction)
        value=next(self.replies)
        if isinstance(value,Exception):raise value
        try:return validator(copy.deepcopy(value))
        except ValueError as error:raise AnalysisError('schema',str(error)) from error


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.evidence={eid:{'id':eid,'task_id':'t','scope':'today','kind':kind,'actor':actor,'metadata':{},'occurred_at':'2026-10-06T09:00:00Z'}
                       for eid,kind,actor in [('e1','note','human'),('e2','file_edit','unknown'),('ctx','agent_message','agent')]}
        self.data={'records':[self.evidence['e1'],self.evidence['e2']], 'context':[self.evidence['ctx']],
                   'tasks':[{'id':'t','native_session_ids':['s']}],'date':'2026-10-06'}
    def run_map(self,client,data=None):
        d=data or self.data
        return request_map(client,d,'Analyze',self.evidence,{'t'},{r['id'] for r in d['records']})
    def initial(self):return result(['e1','e2'],[claim('e1','human_confirmed')])
    def test_missing_record_refined_with_actual_classification(self):
        c=Client([self.initial(),result(['e2'],ignored=[{'evidence_ids':['e2'],'reason':'context_only'}])]);original=copy.deepcopy(self.data)
        out=self.run_map(c)
        self.assertEqual(out['unaccounted_ids'],[]);self.assertEqual(len(c.calls),2)
        self.assertEqual([r['id'] for r in c.calls[1]['records']],['e2'])
        self.assertEqual(c.calls[1]['context'],self.data['context']);self.assertEqual(c.calls[1]['tasks'],self.data['tasks'])
        self.assertEqual(self.data,original);self.assertEqual(c.started,'unchanged');self.assertEqual(c.disabled_until,7)
    def test_refinement_is_bounded_and_does_not_invent_coverage(self):
        c=Client([self.initial(),result(['e2']),result(['e2'])]);out=self.run_map(c)
        self.assertEqual(out['unaccounted_ids'],['e2']);self.assertEqual(len(c.calls),3)
    def test_refinement_cannot_cite_record_absent_from_refinement_input(self):
        c=Client([self.initial(),result(['e2'],[claim('e1')])]);out=self.run_map(c)
        self.assertEqual(out['unaccounted_ids'],['e2']);self.assertEqual(out['items'],[self.initial()['items'][0]])
    def test_bad_initial_output_split_keeps_successful_half(self):
        c=Client([AnalysisError('invalid_json'),result(['e1'],[claim('e1','human_confirmed')]),AnalysisError('schema')]);out=self.run_map(c)
        self.assertEqual(out['unaccounted_ids'],['e2']);self.assertEqual(len(c.calls),3)
        self.assertEqual([r['id'] for r in c.calls[1]['records']],['e1'])
        self.assertEqual([r['id'] for r in c.calls[2]['records']],['e2'])
        self.assertTrue(all(v['context']==self.data['context'] for v in c.calls))
    def test_split_complete_uses_real_issue_references_for_coverage(self):
        problem={'problem':claim('e1','human_confirmed'),'impact':claim('e1'),'state':'open','severity':'blocking','resolution':None}
        c=Client([AnalysisError('schema'),result(['e1'],issues=[problem]),result(['e2'],ignored=[{'evidence_ids':['e2'],'reason':'routine'}])]);out=self.run_map(c)
        self.assertEqual(out['unaccounted_ids'],[]);self.assertEqual(out['items'][0]['issues'][0]['severity'],'blocking')
    def test_failed_halves_raise_original_error(self):
        first=AnalysisError('invalid_json');c=Client([first,AnalysisError('schema'),AnalysisError('schema')])
        with self.assertRaises(AnalysisError) as error:self.run_map(c)
        self.assertIs(error.exception,first);self.assertEqual(len(c.calls),3)
    def test_fatal_initial_failures_never_retry_or_reset(self):
        for code in ['timeout','budget','unavailable','directory','configuration','tool_attempt','authentication','provider_policy','output_limit']:
            with self.subTest(code=code):
                c=Client([AnalysisError(code)])
                with self.assertRaises(AnalysisError) as error:self.run_map(c)
                self.assertEqual(error.exception.code,code);self.assertEqual(len(c.calls),1);self.assertEqual(c.disabled_until,7)
    def test_transient_refinement_failure_preserves_valid_initial_analysis(self):
        for code in ['provider','rate_limit']:
            with self.subTest(code=code):
                c=Client([self.initial(),AnalysisError(code)]);out=self.run_map(c)
                self.assertEqual(out['items'],self.initial()['items']);self.assertEqual(out['unaccounted_ids'],['e2'])
                self.assertEqual(len(c.calls),2);self.assertEqual(c.disabled_until,7);self.assertEqual(c.started,'unchanged')

    def test_browser_visits_receive_observation_contract_without_changing_basis(self):
        row={**self.evidence['e2'],'kind':'context','evidence':'browser_visit_database'}
        data={'records':[row],'context':[]};c=Client([result(['e2'],[claim('e2')])])
        out=request_map(c,data,'Analyze',{'e2':row},{'t'},{'e2'})
        self.assertIn('浏览器访问记录是当天线索',c.instructions[0]);self.assertIn('不推断阅读',c.instructions[0])
        self.assertEqual(out['items'][0]['work'][0]['basis'],'inference')
        other={**row,'evidence':'snapshot_diff','text':'browser_visit_database'};c=Client([result(['e2'],[claim('e2')])])
        request_map(c,{'records':[other],'context':[]},'Analyze',{'e2':other},{'t'},{'e2'})
        self.assertEqual(c.instructions,['Analyze'])

    def test_fatal_refinement_propagates(self):
        c=Client([self.initial(),AnalysisError('authentication')])
        with self.assertRaises(AnalysisError) as error:self.run_map(c)
        self.assertEqual(error.exception.code,'authentication');self.assertEqual(len(c.calls),2)
    def test_false_attribution_and_fabricated_refs_remain_rejected(self):
        for statement in [claim('e2','human_confirmed'),claim('e2','tool_observed'),claim('invented')]:
            with self.subTest(statement=statement):
                c=Client([result(['e2'],[statement])]);d={**self.data,'records':[self.evidence['e2']]}
                with self.assertRaises(AnalysisError):self.run_map(c,d)
                self.assertEqual(len(c.calls),1)
    def test_existing_blocker_is_not_removed_by_refinement(self):
        blocker={'problem':claim('e1'),'impact':claim('e1'),'state':'open','severity':'blocking','resolution':None}
        c=Client([result(['e1','e2'],issues=[blocker]),result(['e2'],ignored=[{'evidence_ids':['e2'],'reason':'routine'}])]);out=self.run_map(c)
        self.assertEqual(out['items'][0]['issues'][0],blocker)
    def test_discarded_uncited_count_is_not_lost_or_double_counted(self):
        first=self.initial();first['items'][0]['remaining']=[{'text':'Unsupported','evidence_ids':[],'basis':'inference','scope':'today'}]
        c=Client([first,result(['e2'],ignored=[{'evidence_ids':['e2'],'reason':'routine'}])]);out=self.run_map(c)
        self.assertEqual(out['discarded_uncited_statements'],1)
