#!/usr/bin/env python3
"""Merge WorkLedger's optional hook fragment without replacing existing hooks."""
import argparse,json,os,sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workledger.config import Config
from workledger.hooks import hook_settings_fragment
from workledger.util import atomic_write
p=argparse.ArgumentParser();p.add_argument('--apply',action='store_true');p.add_argument('--home',type=Path);p.add_argument('--settings',type=Path,default=Path(os.environ.get('CLAUDE_CONFIG_DIR',str(Path.home()/'.claude')))/'settings.json');a=p.parse_args()
cfg=Config(a.home);fragment=hook_settings_fragment(str(Path.home()/'.local/bin/workledger'),cfg.home)
if not a.apply:print(json.dumps(fragment,ensure_ascii=False,indent=2));sys.exit(0)
data=json.loads(a.settings.read_text()) if a.settings.exists() else {}
if a.settings.exists():atomic_write(a.settings.with_name(a.settings.name+'.workledger-backup-'+datetime.now().strftime('%Y%m%d%H%M%S')),a.settings.read_text())
for event,rules in fragment['hooks'].items():
    bucket=data.setdefault('hooks',{}).setdefault(event,[])
    for rule in rules:
        if rule not in bucket:bucket.append(rule)
atomic_write(a.settings,json.dumps(data,ensure_ascii=False,indent=2)+'\n');print('已保留原 hooks 并合并至',a.settings)
