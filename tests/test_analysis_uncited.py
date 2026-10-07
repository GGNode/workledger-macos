import copy
import unittest
from workledger.analysis.schema import validate_map

class UncitedStatementTests(unittest.TestCase):
    def setUp(self):
        self.evidence={'e':{'id':'e','task_id':'t','scope':'today','kind':'document_change','actor':'unknown','metadata':{}}}
        self.good={'text':'Observed a change; authorship and runtime effect remain unverified.','evidence_ids':['e'],'basis':'unverified_change','scope':'today'}
        self.obj={'items':[{'title':'Observed change','task_ids':['t'],'work':[self.good],'results':[], 'remaining':[{'text':'Unsupported speculation','evidence_ids':[],'basis':'inference','scope':'today'}],'suggestions':[],'issues':[]}],'accounted_ids':['e'],'ignored':[]}
    def check(self,obj=None,partial=True):
        return validate_map(obj or self.obj,self.evidence,{'t'},{'e'},allow_partial=partial)
    def test_unsupported_standalone_claim_is_not_accepted(self):
        original=copy.deepcopy(self.obj);v=self.check()
        self.assertEqual(v['items'][0]['remaining'],[])
        self.assertEqual(v['items'][0]['work'],[self.good])
        self.assertEqual(v['unaccounted_ids'],[])
        self.assertEqual(v['discarded_uncited_statements'],1)
        self.assertEqual(self.obj,original)
    def test_strict_contract_still_rejects_uncited(self):
        with self.assertRaises(ValueError):self.check(partial=False)
    def test_nonempty_unknown_reference_still_rejects(self):
        self.obj['items'][0]['remaining'][0]['evidence_ids']=['invented']
        with self.assertRaises(ValueError):self.check()
    def test_false_human_attribution_still_rejects(self):
        self.obj['items'][0]['work'][0]['basis']='human_confirmed'
        with self.assertRaises(ValueError):self.check()
    def test_false_tool_settlement_still_rejects(self):
        self.obj['items'][0]['work'][0]['basis']='tool_observed'
        with self.assertRaisesRegex(ValueError,"evidence_ids=e"):self.check()
    def test_uncited_issue_is_never_silently_discarded(self):
        self.obj['items'][0]['issues']=[{'problem':self.good,'impact':{'text':'Unsupported impact','evidence_ids':[],'basis':'inference','scope':'today'},'state':'open','severity':'blocking','resolution':None}]
        with self.assertRaises(ValueError):self.check()
