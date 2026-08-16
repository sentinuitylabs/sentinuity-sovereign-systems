#!/usr/bin/env python3
from pathlib import Path
import hashlib, shutil, sys
ROOT=Path(__file__).resolve().parent.parent
backs=sorted((ROOT/'backups').glob('live_signoff_final_*/execution_engine.py'),key=lambda p:p.stat().st_mtime,reverse=True)
if not backs:
    print('FAIL no live_signoff_final backup found'); sys.exit(1)
target=ROOT/'services'/'execution_engine.py'; src=backs[0]
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
print('restoring',src)
shutil.copy2(src,target)
for name in ('stop_realisability.py','paper_live_parity.py','canary_governor.py'):
    p=ROOT/'services'/name
    if p.exists(): p.unlink(); print('removed',p)
print('execution_engine.py restored',sha(target)[:16])
