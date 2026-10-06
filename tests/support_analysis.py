"""Synthetic expected-response replay, NOT a production backend or live LLM test."""
import copy
import json
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
            value=self.expand(copy.deepcopy(self.fixture['map']))
            value['accounted_ids']=list(dict.fromkeys(r['id'] for r in data['records']))
        elif stage == 'route':
            value={'groups':[{'title':t['title'],'item_ids':[data['catalog'][i]['id'] for i in group]} for group,t in zip(self.fixture['route_groups'],self.fixture['themes'])]}
        elif stage == 'theme':
            value=self.expand(copy.deepcopy(self.fixture['themes'][self.theme_index]));self.theme_index+=1
            value['covered_item_ids']=[v['id'] for v in data['items']]
        elif stage == 'day':
            value=self.expand(copy.deepcopy(self.fixture['day']))
        else:
            raise AssertionError('Unexpected replay stage: '+stage)
        return validator(value)
