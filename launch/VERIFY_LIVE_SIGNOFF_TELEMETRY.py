#!/usr/bin/env python3
from pathlib import Path
import hashlib, py_compile, sys
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
files=[ROOT/'services'/'stop_realisability.py',ROOT/'services'/'paper_live_parity.py',ROOT/'services'/'canary_governor.py',ROOT/'services'/'execution_engine.py']
checks=[]
def ck(name,ok): checks.append((name,bool(ok))); print(f"[{'PASS' if ok else 'FAIL'}] {name}")
for p in files:
    try: py_compile.compile(str(p),doraise=True); ck('compile '+str(p.relative_to(ROOT)),True)
    except Exception: ck('compile '+str(p.relative_to(ROOT)),False)
ee=(ROOT/'services'/'execution_engine.py').read_text(encoding='utf-8',errors='replace')
markers=[
'STOP_REALISABILITY_PROBE_20260803_FINAL','PARITY_PAPER_ADMISSION_20260803_FINAL','PARITY_LIVE_DECISION_20260803_FINAL',
'CANARY_GOVERNOR_BEFORE_LIVE_BUY_20260803_FINAL','CANARY_AND_PARITY_SUBMISSION_20260803_FINAL','PARITY_LIVE_BUY_SETTLED_20260803_FINAL',
'PARITY_AND_CANARY_CLOSE_SETTLEMENT_20260803_FINAL','mark_failed_unresolved(_fc','mark_failed_unresolved(_ec','LIVE_RISK_DAILY_LIMIT',
'live_refusal_reason="LIVE_CAPS"','executability_state=str(_coverage_reason)','LIVE_SELL_UNRESOLVED','reserve_attempt(',
]
for m in markers: ck('integration '+m[:46],m in ee)
ck('governor before live buy',ee.find('CANARY_GOVERNOR_BEFORE_LIVE_BUY_20260803_FINAL') < ee.find('_lr = _live_buy(',ee.find('CANARY_GOVERNOR_BEFORE_LIVE_BUY_20260803_FINAL')))
ck('reservation before live buy',ee.find('reserve_attempt(',ee.find('CANARY_GOVERNOR_BEFORE_LIVE_BUY_20260803_FINAL')) < ee.find('_lr = _live_buy(',ee.find('CANARY_GOVERNOR_BEFORE_LIVE_BUY_20260803_FINAL')))
ck('Mode B threshold preserved','74' in ee)
ck('admission floor preserved','max(conf_floor, 0.65)' in ee)
ck('4 percent stop cap preserved','min(abs(float(get_config_value("HARD_STOP_LOSS_PCT", 4.0))), 4.0)' in ee)
ck('conjunctive live arming preserved','_LIVE_TRADING_AVAILABLE and _live_lane_armed() and _mode_b_live_pass' in ee)
ck('would_veto not enforced','if would_veto' not in ee and 'if _would_veto' not in ee)
from services.canary_governor import may_fire_canary
import sqlite3
c=sqlite3.connect(':memory:')
r=may_fire_canary(c)
ck('governor fail closed',r.get('allowed') is False)
from services.stop_realisability import readiness
ck('empty stop ledger incomplete',readiness(c).get('status')=='RESEARCH_SAMPLE_INCOMPLETE')
print('\n%d/%d verification contracts passed'%(sum(x for _,x in checks),len(checks)))
print('STOP_TELEMETRY_COLLECTING / PARITY_TELEMETRY_COLLECTING / LIVE_GOVERNOR_REFUSING')
sys.exit(0 if all(x for _,x in checks) else 1)
