#!/usr/bin/env python3
from pathlib import Path
import ast, hashlib
ROOT=Path(__file__).resolve().parents[1]
checks=[]
def ck(n,o): checks.append(bool(o)); print(f"[{'PASS' if o else 'FAIL'}] {n}")
for f in ['tools/apply_launch_safety_pins.py','services/live_trading.py']:
    ast.parse((ROOT/f).read_text(encoding='utf-8')); ck(f+' parses',True)
p=(ROOT/'tools/apply_launch_safety_pins.py').read_text(encoding='utf-8')
l=(ROOT/'services/live_trading.py').read_text(encoding='utf-8')
b=(ROOT/'launch/Launch_Sentinuity.bat').read_text(encoding='utf-8')
ck('public dual stub profile', 'PUBLIC_DUAL_STUB' in p and "'LIVE_SUBMISSION_BACKEND':'stub'" in p)
ck('family live profile', 'FAMILY_LIVE_CANARY' in p and "'LIVE_SUBMISSION_BACKEND':'real'" in p)
ck('private marker required', 'family_live.enable' in p and 'family_live.enable' in l)
ck('private acknowledgement required', 'I_ACCEPT_REAL_LOSS' in p and 'I_ACCEPT_REAL_LOSS' in l)
ck('buy hard guard', '[LIVE_BUY_STUBBED]' in l)
ck('sell hard guard', '[LIVE_SELL_STUBBED]' in l)
ck('final posture before services', b.index('FINAL_DISTRIBUTION_POSTURE_AUTHORITY_20260804') < b.index('Starting observer services after wallet/config reset'))
ck('dual requested at final authority', '--requested dual' in b)
ck('stop basis v2 included', 'BASIS_VERSION = 2' in (ROOT/'services/stop_realisability.py').read_text(encoding='utf-8'))
print(f"\n{sum(checks)}/{len(checks)} checks passed")
raise SystemExit(0 if all(checks) else 1)
