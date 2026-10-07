"""Synthetic semantic catalog assignments; no claim attribution is normalized."""
import copy
import unittest
from workledger.analysis.schema import validate_routes
from workledger.analysis.pipeline import route_items

class RoutesTests(unittest.TestCase):
    def test_missing_items_preserve_valid_groups_and_remain_explicit(self):
        obj={'groups':[{'title':'Synthetic topic','item_ids':['a','b']}]};original=copy.deepcopy(obj)
        out=validate_routes(obj,{'a','b','c'},allow_partial=True)
        self.assertEqual(out['groups'],obj['groups']);self.assertEqual(out['unassigned_item_ids'],['c']);self.assertEqual(obj,original)
    def test_conflicting_item_is_removed_from_both_routes_without_guessing(self):
        out=validate_routes({'groups':[{'title':'A','item_ids':['a','b']},{'title':'B','item_ids':['b','c']}]},{'a','b','c','d'},allow_partial=True)
        self.assertEqual(out['groups'],[{'title':'A','item_ids':['a']},{'title':'B','item_ids':['c']}]);self.assertEqual(out['unassigned_item_ids'],['b','d'])
    def test_unknown_ids_and_malformed_groups_still_rejected(self):
        for groups in [[{'title':'A','item_ids':['fake']}],['A'],[{'title':'A','item_ids':[]}]]:
            with self.subTest(groups=groups),self.assertRaises(ValueError):validate_routes({'groups':groups},{'a'},allow_partial=True)
    def test_empty_catalog_response_keeps_every_item_unassigned(self):
        out=validate_routes({'groups':[]},{'a','b'},allow_partial=True)
        self.assertEqual(out,{'groups':[],'unassigned_item_ids':['a','b']})
    def test_production_routing_retains_omitted_and_conflicting_items_once(self):
        items=[{'id':i,'title':'Work '+i,'task_ids':['t'],'work':[],'results':[],'remaining':[]} for i in ['a','b','c','d']]
        original=copy.deepcopy(items)
        class Client:
            def request(self,stage,data,instruction,validator):
                return validator({'groups':[{'title':'A','item_ids':['a','b']},{'title':'B','item_ids':['b','c']}]})
        warnings=[];out=route_items(items,{'tasks':{'t':{'workspace':'Synthetic'}}},Client(),10000,warnings)
        self.assertEqual(sorted(i for g in out for i in g['item_ids']),['a','b','c','d']);self.assertEqual(len(out),4)
        self.assertEqual(warnings[0]['code'],'incomplete');self.assertEqual(items,original)
