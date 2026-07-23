from pathlib import Path
import sys, py_compile
root=Path(__file__).resolve().parent
checks=[]
def ck(ok,msg):
 print(("  + " if ok else "  - ")+msg); checks.append(bool(ok))
ing=(root/"services/ingest_pipeline.py").read_text(errors="replace")
exe=(root/"services/execution_engine.py").read_text(errors="replace")
ck("RESOLVER_LANE_RESILIENCE_20260723" not in ing,"resolver thread-leak patch removed")
ck("with ThreadPoolExecutor(max_workers=max_workers) as pool:" in ing,"profitable bounded resolver lifecycle restored")
ck("pool.shutdown(wait=False" not in ing,"no non-waiting executor leak")
ck("PAPER_LAST_TRUSTED_MARK" not in exe,"stale paper marks cannot drive exits")
ck("price stale (%.1fs) - skipping paper TP/SL" in exe,"paper exits fail closed on stale router price")
ck("max(0.5, min(20.0" in exe,"hard-stop configuration bounded")
for f in (root/"services/ingest_pipeline.py",root/"services/execution_engine.py"):
 try: py_compile.compile(str(f),doraise=True); ck(True,f"compile clean: {f.name}")
 except Exception as x: ck(False,f"compile failed: {f.name}: {x}")
print(f"RESULT: {sum(checks)} passed, {len(checks)-sum(checks)} failed")
if not all(checks): sys.exit(1)
print("VAULT TRIANGULATION EDGE RESTORE: PASS")
