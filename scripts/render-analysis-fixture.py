#!/usr/bin/env python3
"""Render the synthetic analysis acceptance case using expected-response replay.

Never selects a production model, never imports personal logs, and writes to an
explicit output directory. This is not evidence of live OpenCode compatibility.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tests')]
from workledger.config import Config
from workledger.store import Store
from workledger.report import build_report
from workledger.analysis.publish import publish
from support_analysis import load_fixture, ExpectedResponseReplay


def render(output):
    cfg=Config(output/'data');cfg.save({'timezone':'UTC','llm':{'mode':'opencode'},'projects':[{'name':'WorkLedger','paths':['/synthetic/workledger']}]})
    with Store(cfg.db_path) as store:
        fixture,aliases,tasks=load_fixture(cfg,store)
        replay=ExpectedResponseReplay(fixture,aliases,tasks)
        report=build_report(cfg,store,'2026-10-06',analysis_client=replay)
        report['demo']=True;report['demo_notice']=fixture['notice']
        report['analysis']['transport']['data_flow']='合成测试响应回放：没有启动 OpenCode，也没有向模型发送数据'
        report['analysis']['transport']['test_double']=True
        path=publish(cfg,report)
    # Portable files, not a symlink-dependent demo export.
    output.mkdir(parents=True,exist_ok=True)
    for name in ('report.html','report.md','report.json'):
        (output/name).write_bytes((path.parent/name).read_bytes())
    return output/'report.html'


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True,type=Path)
    print(render(parser.parse_args().output))
