"""Lossless provider envelopes never bypass provenance or native-task checks."""
import copy
import unittest

from workledger.analysis.schema import validate_theme, validate_day


class ThemeEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.evidence = {'e': {'id': 'e', 'task_id': 't', 'scope': 'today',
                               'kind': 'agent_message', 'actor': 'agent', 'metadata': {}}}
        self.statement = {'text': 'Synthetic result reported by an agent.',
                          'evidence_ids': ['e'], 'basis': 'agent_claim', 'scope': 'today'}
        self.item = {'title': 'Synthetic work', 'task_ids': ['t'],
                     'work': [self.statement], 'results': [], 'remaining': [],
                     'suggestions': [], 'issues': []}

    def test_single_wrapper_preserves_content_and_input(self):
        wrapped = {'work_items': [copy.deepcopy(self.item)], 'covered_item_ids': ['input-item']}
        original = copy.deepcopy(wrapped)
        self.assertEqual(validate_theme(wrapped, self.evidence, {'t'}, {'t'}),
                         validate_theme(self.item, self.evidence, {'t'}, {'t'}))
        self.assertEqual(wrapped, original)

    def test_multiple_or_conflicting_envelopes_are_not_guessed(self):
        cases = [
            {'work_items': [], 'covered_item_ids': ['input-item']},
            {'work_items': [self.item, self.item], 'covered_item_ids': ['input-item']},
            {'work_items': [self.item], 'covered_item_ids': ['input-item'], 'title': 'Other title'},
            {'work_items': [{**self.item, 'covered_item_ids': ['other']}], 'covered_item_ids': ['input-item']},
        ]
        for wrapped in cases:
            with self.subTest(wrapped=wrapped), self.assertRaises(ValueError):
                validate_theme(wrapped, self.evidence, {'t'}, {'t'})

    def test_wrapper_keeps_false_attribution_and_unknown_refs_rejected(self):
        for change in ({'basis': 'human_confirmed'}, {'evidence_ids': ['invented']}):
            item = {**self.item, 'work': [{**self.statement, **change}]}
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_theme({'work_items': [item], 'covered_item_ids': ['input-item']},
                               self.evidence, {'t'}, {'t'})
        with self.assertRaises(ValueError):
            validate_theme({'work_items': [self.item], 'covered_item_ids': ['input-item']},
                           self.evidence, {'t', 'missing-task'}, {'t', 'missing-task'})

    def test_daily_highlight_limit_rejects_instead_of_truncating(self):
        exact = {**self.statement, 'text': '简' * 180}
        self.assertEqual(validate_day({'highlights': [exact]}, self.evidence)['highlights'][0]['text'], exact['text'])
        with self.assertRaises(ValueError):
            validate_day({'highlights': [{**exact, 'text': exact['text'] + '长'}]}, self.evidence)


if __name__ == '__main__':
    unittest.main()
