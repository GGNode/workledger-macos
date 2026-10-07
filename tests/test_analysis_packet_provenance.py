"""Synthetic tests for task-coherent packet planning and provenance hints.

These tests verify the evidence planner produces:
- One native task per packet with fair round-robin ordering
- Full raw record content/part identities preserved
- supported_bases hints matching schema.claim conditions exactly
- result_eligible hints for request/plan vs result distinction
- Context entries retaining native task/time/actor/evidence values
"""
import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone

from workledger.config import Config
from workledger.store import Store
from workledger.util import day_bounds
from workledger.analysis.evidence import (
    prepare, packet_input, supported_bases, result_eligible, observed, native_root
)


DAY = '2026-10-06'
AT = '2026-10-06T09:00:00Z'
LATER = '2026-10-06T10:00:00Z'


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cfg = Config(self.root / 'data')
        self.cfg.save({'timezone': 'UTC', 'report_open': False})
        self.store = Store(self.cfg.db_path)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def events(self):
        return self.store.events(*day_bounds(DAY, 'UTC'))

    def plan(self):
        return prepare(self.cfg, self.store, self.events(), self.store.all_sessions(), DAY)


class SupportedBasesTests(Base):
    """Test supported_bases hint matches schema.claim validation conditions exactly."""

    def test_human_actor_includes_human_confirmed(self):
        self.store.session('x', 's')
        eid = self.store.event('x', 'h', 'note', session_id='x:s', actor='human', occurred_at=AT, text='confirmed')
        e = self.plan()['evidence'][eid]
        self.assertIn('human_confirmed', supported_bases(e))
        self.assertIn('inference', supported_bases(e))

    def test_user_message_includes_user_direction(self):
        self.store.session('x', 's')
        eid = self.store.event('x', 'u', 'user_message', session_id='x:s', actor='unknown', occurred_at=AT, text='do this')
        e = self.plan()['evidence'][eid]
        self.assertIn('user_direction', supported_bases(e))
        self.assertNotIn('human_confirmed', supported_bases(e))  # actor is unknown

    def test_note_and_review_include_user_direction(self):
        for kind in ('note', 'review'):
            with self.subTest(kind=kind):
                self.store.session('x', f's_{kind}')
                eid = self.store.event('x', kind, kind, session_id=f'x:s_{kind}', actor='unknown', occurred_at=AT, text='feedback')
                e = self.plan()['evidence'][eid]
                self.assertIn('user_direction', supported_bases(e))

    def test_successful_tool_result_includes_tool_observed(self):
        self.store.session('x', 's')
        eid = self.store.event('x', 'ok', 'tool_result', session_id='x:s', actor='agent', occurred_at=AT, text='ran', metadata={'success': True})
        e = self.plan()['evidence'][eid]
        self.assertIn('tool_observed', supported_bases(e))

    def test_failed_tool_result_excludes_tool_observed(self):
        self.store.session('x', 's')
        eid = self.store.event('x', 'bad', 'tool_result', session_id='x:s', actor='agent', occurred_at=AT, text='failed', metadata={'success': False})
        e = self.plan()['evidence'][eid]
        self.assertNotIn('tool_observed', supported_bases(e))
        self.assertIn('inference', supported_bases(e))

    def test_agent_message_includes_agent_claim(self):
        self.store.session('x', 's')
        eid = self.store.event('x', 'say', 'agent_message', session_id='x:s', actor='agent', occurred_at=AT, text='I did it')
        e = self.plan()['evidence'][eid]
        self.assertIn('agent_claim', supported_bases(e))

    def test_unknown_document_change_includes_unverified_change(self):
        self.store.session('x', 's')
        eid = self.store.event('x', 'doc', 'document_change', session_id='x:s', actor='unknown', occurred_at=AT, text='changed', artifact='/synthetic/docs/x.md')
        e = self.plan()['evidence'][eid]
        self.assertIn('unverified_change', supported_bases(e))

    def test_known_actor_document_change_excludes_unverified_change(self):
        self.store.session('x', 's')
        eid = self.store.event('x', 'doc', 'document_change', session_id='x:s', actor='agent', occurred_at=AT, text='changed', artifact='/synthetic/docs/x.md')
        e = self.plan()['evidence'][eid]
        self.assertNotIn('unverified_change', supported_bases(e))

    def test_unknown_file_edit_includes_unverified_change(self):
        self.store.session('x', 's')
        eid = self.store.event('x', 'edit', 'file_edit', session_id='x:s', actor='unknown', occurred_at=AT, text='edited', artifact='/synthetic/workledger/x.py')
        e = self.plan()['evidence'][eid]
        self.assertIn('unverified_change', supported_bases(e))

    def test_successful_file_edit_includes_tool_observed(self):
        self.store.session('x', 's')
        eid = self.store.event('x', 'edit', 'file_edit', session_id='x:s', actor='agent', occurred_at=AT, text='edited', artifact='/synthetic/workledger/x.py', evidence='successful_tool_result')
        e = self.plan()['evidence'][eid]
        self.assertIn('tool_observed', supported_bases(e))
        self.assertNotIn('unverified_change', supported_bases(e))  # actor is agent

    def test_inference_always_present(self):
        for kind, actor in [('tool_call', 'agent'), ('browser_message', 'agent'), ('context', 'agent'), ('delegated_instruction', 'agent')]:
            with self.subTest(kind=kind):
                self.store.session('x', f's_{kind}')
                eid = self.store.event('x', kind, kind, session_id=f'x:s_{kind}', actor=actor, occurred_at=AT, text='x')
                e = self.plan()['evidence'][eid]
                self.assertIn('inference', supported_bases(e))

    def test_no_fabricated_bases(self):
        """Ensure no bases are added beyond the six defined in schema.BASES."""
        schema_bases = {"human_confirmed", "user_direction", "tool_observed", "agent_claim", "unverified_change", "inference"}
        for kind in ("user_message", "agent_message", "tool_result", "file_edit", "document_change", "note", "review", "tool_call", "browser_message", "context"):
            for actor in ("human", "agent", "unknown"):
                for success in (True, False, None):
                    with self.subTest(kind=kind, actor=actor, success=success):
                        self.store.session('x', f's_{kind}_{actor}')
                        meta = {'success': success} if kind == 'tool_result' else {}
                        evidence_val = 'successful_tool_result' if kind == 'file_edit' else ''
                        eid = self.store.event('x', f'{kind}_{actor}', kind, session_id=f'x:s_{kind}_{actor}', actor=actor, occurred_at=AT, text='x', metadata=meta, evidence=evidence_val)
                        e = self.plan()['evidence'][eid]
                        bases = set(supported_bases(e))
                        self.assertTrue(bases.issubset(schema_bases), f"Unexpected bases: {bases - schema_bases}")


class ResultEligibleTests(Base):
    """Test result_eligible hint correctly identifies request/plan vs result evidence."""

    def test_user_message_not_result_eligible(self):
        self.store.session('x', 's')
        eid = self.store.event('x', 'req', 'user_message', session_id='x:s', actor='unknown', occurred_at=AT, text='do X')
        e = self.plan()['evidence'][eid]
        self.assertFalse(result_eligible(e))

    def test_delegated_instruction_not_result_eligible(self):
        self.store.session('x', 's')
        eid = self.store.event('x', 'del', 'delegated_instruction', session_id='x:s', actor='agent', occurred_at=AT, text='delegated')
        e = self.plan()['evidence'][eid]
        self.assertFalse(result_eligible(e))

    def test_tool_call_not_result_eligible(self):
        self.store.session('x', 's')
        eid = self.store.event('x', 'tc', 'tool_call', session_id='x:s', actor='agent', occurred_at=AT, text='call')
        e = self.plan()['evidence'][eid]
        self.assertFalse(result_eligible(e))

    def test_context_not_result_eligible(self):
        self.store.session('x', 's')
        eid = self.store.event('x', 'ctx', 'context', session_id='x:s', actor='agent', occurred_at=AT, text='context')
        e = self.plan()['evidence'][eid]
        self.assertFalse(result_eligible(e))

    def test_agent_message_result_eligible(self):
        self.store.session('x', 's')
        eid = self.store.event('x', 'am', 'agent_message', session_id='x:s', actor='agent', occurred_at=AT, text='done')
        e = self.plan()['evidence'][eid]
        self.assertTrue(result_eligible(e))

    def test_successful_tool_result_result_eligible(self):
        self.store.session('x', 's')
        eid = self.store.event('x', 'ok', 'tool_result', session_id='x:s', actor='agent', occurred_at=AT, text='ok', metadata={'success': True})
        e = self.plan()['evidence'][eid]
        self.assertTrue(result_eligible(e))

    def test_history_scope_not_result_eligible(self):
        self.store.session('x', 's')
        past_eid = self.store.event('x', 'past', 'tool_result', session_id='x:s', actor='agent', occurred_at='2026-10-05T09:00:00Z', text='past', metadata={'success': True})
        today_eid = self.store.event('x', 'today', 'user_message', session_id='x:s', actor='unknown', occurred_at=AT, text='today')
        p = self.plan()
        self.assertIn(past_eid, p['evidence'])
        e = p['evidence'][past_eid]
        self.assertEqual(e['scope'], 'history')
        self.assertFalse(result_eligible(e))

    def test_successful_file_edit_result_eligible(self):
        self.store.session('x', 's')
        eid = self.store.event('x', 'edit', 'file_edit', session_id='x:s', actor='agent', occurred_at=AT, text='edited', artifact='/synthetic/x.py', evidence='successful_tool_result')
        e = self.plan()['evidence'][eid]
        self.assertTrue(result_eligible(e))


class PacketCoherenceTests(Base):
    """Test task-coherent packet packing: one native task per packet, fair round-robin."""

    def test_one_task_per_packet(self):
        """Each packet contains records from only one native task."""
        for i in range(20):
            self.store.session('x', str(i), cwd='/synthetic/shared')
            self.store.event('x', str(i), 'user_message', session_id='x:'+str(i), occurred_at=f'2026-10-06T09:{i//60:02}:{i%60:02}Z', text='Task '+str(i), actor='unknown')
        p = self.plan()
        for packet in p['packets']:
            task_ids = {r['task_id'] for r in packet}
            self.assertEqual(len(task_ids), 1, f"Packet mixes tasks: {task_ids}")

    def test_fair_first_round_spreads_across_day(self):
        """First round of packets covers diverse task indices, not just earliest."""
        for i in range(20):
            self.store.session('x', str(i), cwd='/synthetic/shared')
            self.store.event('x', str(i), 'user_message', session_id='x:'+str(i), occurred_at=f'2026-10-06T09:{i//60:02}:{i%60:02}Z', text='Task '+str(i), actor='unknown')
        p = self.plan()
        first_round = p['packets'][:20]
        first_task_indices = [int(r['task_id'].split(':')[1]) for pkt in first_round for r in pkt]
        self.assertEqual(len(set(first_task_indices)), 20)
        self.assertLess(min(first_task_indices), 5)
        self.assertGreater(max(first_task_indices), 15)

    def test_chronological_order_within_task(self):
        """Records within a task appear in chronological order."""
        self.store.session('x', 'task')
        for i in range(5):
            self.store.event('x', f'e{i}', 'user_message', session_id='x:task', occurred_at=f'2026-10-06T09:0{i}:00Z', text=f'Event {i}', actor='unknown')
        p = self.plan()
        task_packets = [pkt for pkt in p['packets'] if pkt[0]['task_id'] == 'x:task']
        all_records = [r for pkt in task_packets for r in pkt]
        times = [r['at'] for r in all_records]
        self.assertEqual(times, sorted(times))

    def test_all_parts_of_long_record_retained_and_ordered(self):
        """Long evidence split into parts retains all parts in order."""
        self.store.session('x', 'task')
        long_text = 'A' * 50000 + 'TAIL_MARKER'
        eid = self.store.event('x', 'long', 'agent_message', session_id='x:task', occurred_at=AT, actor='agent', text=long_text)
        p = self.plan()
        parts = [r for pkt in p['packets'] for r in pkt if r['id'] == eid]
        self.assertGreater(len(parts), 1)
        self.assertEqual([r['part'] for r in parts], list(range(1, len(parts)+1)))
        full = ''.join(r['content'] for r in parts)
        self.assertIn('TAIL_MARKER', full)

    def test_later_task_tails_retained(self):
        self.cfg.save({'analysis': {'chunk_chars': 3000}})
        self.store.session('x', 'task')
        for i in range(30):
            self.store.event('x', f'e{i}', 'user_message', session_id='x:task', occurred_at=f'2026-10-06T09:{i:02}:00Z', text='EVENT_'+str(i)*100, actor='unknown')
        p = self.plan()
        task_packets = [pkt for pkt in p['packets'] if pkt[0]['task_id'] == 'x:task']
        all_records = [r for pkt in task_packets for r in pkt]
        self.assertGreater(len(task_packets), 1)
        last_events = [r for r in all_records if 'EVENT_29' in r['content']]
        self.assertTrue(last_events)

    def test_no_tiny_one_record_packets_when_task_fits(self):
        """A task that fits within limit should not be split into many tiny packets."""
        self.cfg.save({'analysis': {'chunk_chars': 10000}})
        self.store.session('x', 'task')
        for i in range(3):
            self.store.event('x', f'e{i}', 'user_message', session_id='x:task', occurred_at=f'2026-10-06T09:0{i}:00Z', text='Small event', actor='unknown')
        p = self.plan()
        task_packets = [pkt for pkt in p['packets'] if pkt[0]['task_id'] == 'x:task']
        # Should fit in one packet (3 small records < 10000 chars)
        self.assertEqual(len(task_packets), 1)
        self.assertEqual(len(task_packets[0]), 3)

    def test_oversize_singleton_allowed(self):
        self.cfg.save({'analysis': {'chunk_chars': 3000}})
        self.store.session('x', 'task')
        huge = 'X' * 5000
        eid = self.store.event('x', 'huge', 'agent_message', session_id='x:task', occurred_at=AT, actor='agent', text=huge)
        p = self.plan()
        huge_parts = [r for pkt in p['packets'] for r in pkt if r['id'] == eid]
        self.assertGreater(len(huge_parts), 1)
        seen_parts = set()
        for r in huge_parts:
            self.assertNotIn(r['part'], seen_parts)
            seen_parts.add(r['part'])
        self.assertEqual(len(seen_parts), len(huge_parts))

    def test_every_actual_record_appears_exactly_once(self):
        """No record is dropped or duplicated across packets."""
        for i in range(50):
            self.store.session('x', str(i))
            self.store.event('x', str(i), 'user_message', session_id='x:'+str(i), occurred_at=AT, text='Task '+str(i), actor='unknown')
        p = self.plan()
        all_records = [r for pkt in p['packets'] for r in pkt]
        seen_ids = set()
        for r in all_records:
            key = (r['id'], r['part'])
            self.assertNotIn(key, seen_ids, f"Duplicate: {key}")
            seen_ids.add(key)
        # All today_ids should be covered
        for tid in p['today_ids']:
            self.assertIn(tid, {r['id'] for r in all_records})

    def test_budget_limited_prefix_spreads_across_tasks(self):
        """When budget cuts off, it should not exhaust first task before others."""
        self.cfg.save({'analysis': {'chunk_chars': 3000, 'max_map_packets': 5}})
        for i in range(10):
            self.store.session('x', str(i))
            # Each task has a large record
            self.store.event('x', str(i), 'user_message', session_id='x:'+str(i), occurred_at=AT, text='LARGE_'+'X'*1000, actor='unknown')
        p = self.plan()
        # With max_map_packets=5 and 10 tasks, we should see at least 5 different tasks
        analyzed_tasks = {pkt[0]['task_id'] for pkt in p['packets'][:5]}
        self.assertEqual(len(analyzed_tasks), 5)


class ContextHintsTests(Base):
    """Test context entries retain native values and have supported_bases/result_eligible."""

    def test_context_retains_native_task_time_actor_evidence(self):
        self.cfg.save({'analysis': {'chunk_chars': 3000}})
        self.store.session('x', 'task')
        req = self.store.event('x', 'req', 'user_message', session_id='x:task', occurred_at=AT, text='Write review '*500, actor='unknown')
        delivery = self.store.event('x', 'del', 'agent_message', session_id='x:task', occurred_at=LATER, text='Review done '*500, actor='agent')
        write = self.store.event('x', 'w', 'file_edit', session_id='x:task', occurred_at=LATER, text='written '*500, actor='agent', evidence='successful_tool_result', artifact='/synthetic/review.md')
        p = self.plan()
        req_record = next(r for pkt in p['packets'] for r in pkt if r['id'] == req)
        data = packet_input(p, [req_record], self.cfg)
        context_ids = {e['id'] for e in data['context']}
        self.assertIn(delivery, context_ids)
        self.assertIn(write, context_ids)
        for ctx in data['context']:
            if ctx['id'] == delivery:
                self.assertEqual(ctx['at'], p['evidence'][delivery]['occurred_at'])
                self.assertEqual(ctx['actor'], 'agent')
                self.assertEqual(ctx['scope'], 'today')
            if ctx['id'] == write:
                self.assertEqual(ctx['evidence'], 'successful_tool_result')

    def test_context_has_supported_bases_and_result_eligible(self):
        self.cfg.save({'analysis': {'chunk_chars': 3000}})
        self.store.session('x', 'task')
        req = self.store.event('x', 'req', 'user_message', session_id='x:task', occurred_at=AT, text='Write review '*500, actor='unknown')
        delivery = self.store.event('x', 'del', 'agent_message', session_id='x:task', occurred_at=LATER, text='Review done '*500, actor='agent')
        p = self.plan()
        req_packets = [pkt for pkt in p['packets'] if any(r['id'] == req for r in pkt)]
        first_req_pkt = req_packets[0]
        data = packet_input(p, first_req_pkt, self.cfg)
        for ctx in data['context']:
            self.assertIn('supported_bases', ctx)
            self.assertIn('result_eligible', ctx)
            self.assertIsInstance(ctx['supported_bases'], list)
            self.assertIsInstance(ctx['result_eligible'], bool)
        del_ctx = next(c for c in data['context'] if c['id'] == delivery)
        self.assertIn('agent_claim', del_ctx['supported_bases'])
        self.assertTrue(del_ctx['result_eligible'])
        delivery_packets = [pkt for pkt in p['packets'] if any(r['id'] == delivery for r in pkt)]
        last_del_pkt = delivery_packets[-1]
        data2 = packet_input(p, last_del_pkt, self.cfg)
        req_ctx = next(c for c in data2['context'] if c['id'] == req)
        self.assertIn('user_direction', req_ctx['supported_bases'])
        self.assertFalse(req_ctx['result_eligible'])

    def test_context_history_scope_preserved(self):
        self.cfg.save({'analysis': {'chunk_chars': 3000, 'history_events_per_task': 2}})
        self.store.session('x', 'root')
        for i in range(3):
            self.store.event('x', f'old{i}', 'agent_message', session_id='x:root', occurred_at=f'2026-10-05T09:00:0{i}Z', actor='agent', text='old')
        new_eid = self.store.event('x', 'new', 'user_message', session_id='x:root', occurred_at=AT, actor='unknown', text='today')
        p = self.plan()
        new_record = next(r for pkt in p['packets'] for r in pkt if r['id'] == new_eid)
        data = packet_input(p, [new_record], self.cfg)
        history_ctx = [c for c in data['context'] if c['scope'] == 'history']
        self.assertTrue(history_ctx)
        for c in history_ctx:
            self.assertEqual(c['scope'], 'history')
            self.assertIn('supported_bases', c)
            self.assertIn('result_eligible', c)
            self.assertFalse(c['result_eligible'])


class ToolCallTextDoesNotMakeToolObservedTests(Base):
    """Test that tool_call text claiming success doesn't make tool_observed."""

    def test_tool_call_claiming_success_not_tool_observed(self):
        self.store.session('x', 's')
        eid = self.store.event('x', 'tc', 'tool_call', session_id='x:s', actor='agent', occurred_at=AT,
                               text='apply_patch: successfully applied changes', metadata={})
        e = self.plan()['evidence'][eid]
        self.assertNotIn('tool_observed', supported_bases(e))
        self.assertIn('inference', supported_bases(e))

    def test_tool_result_success_is_tool_observed(self):
        self.store.session('x', 's')
        eid = self.store.event('x', 'tr', 'tool_result', session_id='x:s', actor='agent', occurred_at=AT,
                               text='apply_patch: done', metadata={'success': True})
        e = self.plan()['evidence'][eid]
        self.assertIn('tool_observed', supported_bases(e))

    def test_tool_result_failure_not_tool_observed(self):
        self.store.session('x', 's')
        eid = self.store.event('x', 'tr', 'tool_result', session_id='x:s', actor='agent', occurred_at=AT,
                               text='apply_patch: failed', metadata={'success': False})
        e = self.plan()['evidence'][eid]
        self.assertNotIn('tool_observed', supported_bases(e))

    def test_file_edit_without_successful_evidence_not_tool_observed(self):
        self.store.session('x', 's')
        eid = self.store.event('x', 'fe', 'file_edit', session_id='x:s', actor='agent', occurred_at=AT,
                               text='edited', artifact='/synthetic/x.py', evidence='')
        e = self.plan()['evidence'][eid]
        self.assertNotIn('tool_observed', supported_bases(e))
        if e['actor'] == 'unknown':
            self.assertIn('unverified_change', supported_bases(e))


class NativeRootTests(Base):
    """Test native_root preserves delegation chain, not fork/lineage."""

    def test_delegation_follows_parent(self):
        self.store.session('x', 'parent')
        self.store.session('x', 'child', parent='parent', relation='delegation')
        sessions = self.store.all_sessions()
        self.assertEqual(native_root('x:child', sessions), 'x:parent')

    def test_fork_is_not_delegation(self):
        self.store.session('x', 'a')
        self.store.session('x', 'b', parent='a', relation='fork')
        sessions = self.store.all_sessions()
        self.assertEqual(native_root('x:b', sessions), 'x:b')

    def test_lineage_is_not_delegation(self):
        self.store.session('x', 'a')
        self.store.session('x', 'b', parent='a', relation='lineage')
        sessions = self.store.all_sessions()
        self.assertEqual(native_root('x:b', sessions), 'x:b')


class ObservedFunctionTests(Base):
    """Test observed() matches schema.observed exactly."""

    def test_tool_result_success_true(self):
        self.store.session('x', 's')
        eid = self.store.event('x', 'ok', 'tool_result', session_id='x:s', actor='agent', occurred_at=AT, metadata={'success': True})
        e = self.plan()['evidence'][eid]
        self.assertTrue(observed(e))

    def test_tool_result_success_false(self):
        self.store.session('x', 's')
        eid = self.store.event('x', 'bad', 'tool_result', session_id='x:s', actor='agent', occurred_at=AT, metadata={'success': False})
        e = self.plan()['evidence'][eid]
        self.assertFalse(observed(e))

    def test_tool_result_no_success(self):
        self.store.session('x', 's')
        eid = self.store.event('x', 'nos', 'tool_result', session_id='x:s', actor='agent', occurred_at=AT, metadata={})
        e = self.plan()['evidence'][eid]
        self.assertFalse(observed(e))

    def test_file_edit_successful_tool_result(self):
        self.store.session('x', 's')
        eid = self.store.event('x', 'edit', 'file_edit', session_id='x:s', actor='agent', occurred_at=AT, evidence='successful_tool_result')
        e = self.plan()['evidence'][eid]
        self.assertTrue(observed(e))

    def test_file_edit_no_evidence(self):
        self.store.session('x', 's')
        eid = self.store.event('x', 'edit', 'file_edit', session_id='x:s', actor='agent', occurred_at=AT, evidence='')
        e = self.plan()['evidence'][eid]
        self.assertFalse(observed(e))


if __name__ == '__main__':
    unittest.main()