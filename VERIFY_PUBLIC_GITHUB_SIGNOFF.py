from pathlib import Path
import ast, re, sys
ROOT=Path(__file__).resolve().parent
checks=[]
def ck(cond,msg):
    checks.append((bool(cond),msg)); print(('PASS: ' if cond else 'FAIL: ')+msg)
# parse all python
bad=[]
for p in ROOT.rglob('*.py'):
    try: ast.parse(p.read_text(encoding='utf-8',errors='strict'), filename=str(p))
    except Exception as e: bad.append(f'{p.relative_to(ROOT)}: {e}')
ck(not bad,'all packaged Python parses cleanly')
lt=(ROOT/'services/live_trading.py').read_text(encoding='utf-8')
ck('PUBLIC_RELEASE_LIVE_STUB = True' in lt,'live execution hard-stub constant present')
ck(all(x in lt for x in ('return _public_live_block("preflight_live_buy"','return _public_live_block("execute_live_buy"','return _public_live_block("execute_live_sell"','raise RuntimeError("PUBLIC_RELEASE_LIVE_EXECUTION_DISABLED")','if PUBLIC_RELEASE_LIVE_STUB:\n        return False')),'signing, buy, sell, preflight and mode paths blocked')
launcher=(ROOT/'launch/Launch_Sentinuity_Public_Paper.bat').read_text(encoding='utf-8',errors='replace')
ck('FORCE_PAPER_SAFE_PRESTART_0707.py' in launcher,'public launcher force-stamps paper configuration')
ck('SENTINUITY_LIVE_EXECUTION_STUB=1' in launcher,'public launcher declares live stub')
world=(ROOT/'ui/sovereign_world_component.py').read_text(encoding='utf-8')
html=(ROOT/'ui/sovereign_world.html').read_text(encoding='utf-8')
ck('STATE_VERSION      = 4' in world,'Living Organism World bridge v4 present')
ck('read-only' in world.lower() and 'no fabrication' in world.lower(),'World remains read-only and evidence-linked')
ck('Courier Owl' in html or 'COURIER' in html.upper(),'Courier Owl world role present')
pm=(ROOT/'services/price_truth_mesh.py').read_text(encoding='utf-8')
ck('MARK_FIRST_PUMP_OPTIONAL' in pm,'mark-first price authority present')
clean=(ROOT/'services/active_pipeline_cleaner.py').read_text(encoding='utf-8')
ck('DEFER_HOT_PRICE_TABLE' in clean and 'DEFER_HOT_DB_CHECKPOINT' in clean,'hot price cleanup/checkpoint deferral present')
exe=(ROOT/'services/execution_engine.py').read_text(encoding='utf-8')
ck('4.0' in exe and 'HARD_STOP' in exe,'four-percent hard-stop enforcement present')
for forbidden in ('*.db','*.sqlite','*.sqlite3','*.pyc'):
    ck(not list(ROOT.rglob(forbidden)),f'no {forbidden} runtime artifacts packaged')
ck(not any('.bak.' in p.name or p.name.endswith('.bak') for p in ROOT.rglob('*') if p.is_file()),'no backup files packaged')
# obvious actual secret patterns
secret=[]
pat=re.compile(r'(?i)(?:api[_-]?key|private[_-]?key|secret[_-]?key)\s*=\s*["\'][A-Za-z0-9+/=_-]{20,}["\']')
for p in ROOT.rglob('*'):
    if p.is_file() and p.suffix.lower() in {'.py','.bat','.md','.txt','.json','.yaml','.yml'}:
        s=p.read_text(encoding='utf-8',errors='ignore')
        if pat.search(s): secret.append(str(p.relative_to(ROOT)))
ck(not secret,'no obvious embedded credential literals')
failed=[m for ok,m in checks if not ok]
print('\nPUBLIC GITHUB SIGN-OFF:', 'PASS' if not failed else 'FAIL')
if bad:
    print('\nParse failures:'); print('\n'.join(bad))
if secret:
    print('\nSecret candidates:'); print('\n'.join(secret))
sys.exit(1 if failed else 0)
