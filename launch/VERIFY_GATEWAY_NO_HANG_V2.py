from pathlib import Path
import sys
p=Path(sys.argv[1] if len(sys.argv)>1 else r"launch\Launch_Sentinuity.bat")
t=p.read_text(encoding="utf-8",errors="replace")
checks=[
("absolute Windows PowerShell path", r'%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe' in t),
("no bare powershell command", '\n    powershell ' not in t.lower()),
("bounded gateway wait", 'AddSeconds(30)' in t),
("gateway start retained", 'start "OpenClaw Gateway"' in t),
("launcher continues after gateway dispatch", 'launch dispatched in persistent console' in t),
]
for n,v in checks: print(f"[{'PASS' if v else 'FAIL'}] {n}")
f=sum(not v for _,v in checks)
print(f"\n{len(checks)} checks, {len(checks)-f} passed, {f} failed")
raise SystemExit(1 if f else 0)
