"""Synthetic expected-response replay, NOT a production backend or live LLM test."""
import copy
import json
from collections import defaultdict
from pathlib import Path
from workledger.analysis.evidence import task_id
from workledger.util import atomic_write

FIXTURE = Path(__file__).parent / "fixtures/analysis_day.json"


def load_fixture(config, store):
    fixture = json.loads(FIXTURE.read_text())
    for s in fixture["sessions"]:
        values = dict(s)
        source = values.pop("source"); native = values.pop("native_id")
        store.session(source, native, **values)
    aliases = {}
    for event in fixture["events"]:
        e = dict(event); key=e.pop("key"); source=e.pop("source")
        aliases[key] = store.event(source, key, **e)
    sessions = store.all_sessions()
    tasks = {"task:"+k: task_id(store.get_event(eid), sessions) for k, eid in aliases.items()}
    root = config.home/"analysis/runs"; root.mkdir(parents=True, exist_ok=True)
    atomic_write(root/(fixture["run_id"]+".json"), json.dumps({"origin":"workledger_analysis","run_id":fixture["run_id"],"title":"WorkLedger analysis "+fixture["run_id"],"session_ids":["analysis-self"]}))
    store.conn.commit()
    return fixture, aliases, tasks


class ExpectedResponseReplay:
    """Plays prewritten expected JSON; does not infer topics from string keywords."""
    def __init__(self, fixture, aliases, tasks):
        self.fixture=fixture; self.aliases=aliases; self.tasks=tasks
        self.calls=0; self.hits=0; self.actual_models=set(); self.theme_index=0
        self.stages=[]
        # Precompute map items by fixture task key (e.g., "task:resources")
        self.map_items_by_fixture_task = defaultdict(list)
        for item in fixture['map']['items']:
            for tid in item.get('task_ids', []):
                self.map_items_by_fixture_task[tid].append(item)
        # Reverse mapping: actual task_id -> list of fixture task keys
        self.task_id_to_fixture_keys = defaultdict(list)
        for fixture_key, actual_tid in tasks.items():
            self.task_id_to_fixture_keys[actual_tid].append(fixture_key)

    def expand(self, value):
        if isinstance(value, str):
            return self.aliases.get(value, self.tasks.get(value, value))
        if isinstance(value, list):
            return [self.expand(v) for v in value]
        if isinstance(value, dict):
            return {k:self.expand(v) for k,v in value.items()}
        return value

    def request(self, stage, data, instruction, validator):
        self.calls+=1; self.stages.append(stage)
        if stage == 'map':
            # Task-coherent packets: only include items for task_ids present in this packet's records
            packet_task_ids = {r['task_id'] for r in data['records']}
            items = []
            for actual_tid in packet_task_ids:
                for fixture_key in self.task_id_to_fixture_keys.get(actual_tid, []):
                    items.extend(self.map_items_by_fixture_task.get(fixture_key, []))
            available = {r["id"] for r in data["records"]+data["context"]}
            ignored = []
            for row in self.expand(copy.deepcopy(self.fixture["map"]["ignored"])):
                refs = [eid for eid in row["evidence_ids"] if eid in available]
                if refs:
                    ignored.append({**row, "evidence_ids":refs})
            value = {"items": items, "accounted_ids": list(dict.fromkeys(r['id'] for r in data['records'])), "ignored": ignored}
            value = self.expand(value)
        elif stage == 'route':
            # Explicit fixture titles identify expected items independently of packet order.
            value={'groups':[{'title':t['title'],'item_ids':[v['id'] for v in data['catalog'] if v['title'] in {self.fixture['map']['items'][i]['title'] for i in group}]} for group,t in zip(self.fixture['route_groups'],self.fixture['themes'])]}
        elif stage == 'theme':
            value=self.expand(copy.deepcopy(self.fixture['themes'][self.theme_index]));self.theme_index+=1
            value['covered_item_ids']=[v['id'] for v in data['items']]
        elif stage == 'day':
            value=self.expand(copy.deepcopy(self.fixture['day']))
        else:
            raise AssertionError('Unexpected replay stage: '+stage)
        return validator(value)
