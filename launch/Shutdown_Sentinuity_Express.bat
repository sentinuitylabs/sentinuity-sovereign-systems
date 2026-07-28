@echo off
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
setlocal EnableExtensions EnableDelayedExpansion

title SENTINUITY EXPRESS SHUTDOWN

cd /d "%~dp0\.."

echo ==========================================================
echo   SENTINUITY EXPRESS SHUTDOWN
echo ==========================================================
echo   Root: %CD%
echo   Mode: fast audit/restart loop
echo ==========================================================

if not exist logs mkdir logs

echo.
echo [1] Stopping Sentinuity service windows fast...

for %%P in (
  streamlit.exe
  python.exe
) do (
  rem Keep this broad but still log it. Used only during local audit loops.
  taskkill /F /FI "WINDOWTITLE eq Sentinuity*" /T >nul 2>&1
  taskkill /F /FI "WINDOWTITLE eq SENTINUITY*" /T >nul 2>&1
)

echo [OK] Sentinuity titled windows requested to stop.

echo.
echo [2] Soft DB checkpoint...

python - <<PY
import sqlite3, pathlib, time
for name in ["sentinuity_matrix.db", "sentinuity_intelligence.db"]:
    p = pathlib.Path(name)
    if not p.exists():
        print(f"[WARN] {name} missing")
        continue
    try:
        con = sqlite3.connect(str(p), timeout=10)
        print(f"[DB] {name}")
        print("  wal_checkpoint:", con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall())
        print("  integrity:", con.execute("PRAGMA integrity_check").fetchone()[0])
        con.close()
    except Exception as e:
        print(f"[WARN] {name} checkpoint skipped: {e}")
PY

echo.
echo [3] Marking service heartbeats as stopped/restarting-safe...

python - <<PY
import sqlite3, pathlib, time
p = pathlib.Path("sentinuity_matrix.db")
if p.exists():
    try:
        con = sqlite3.connect(str(p), timeout=10)
        cur = con.cursor()
        tabs = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "service_heartbeats" in tabs:
            cols = {r[1] for r in cur.execute("PRAGMA table_info(service_heartbeats)")}
            if "status" in cols:
                cur.execute("UPDATE service_heartbeats SET status='STOPPED_EXPRESS'")
            if "message" in cols:
                cur.execute("UPDATE service_heartbeats SET message='Express shutdown clean stop'")
            elif "details" in cols:
                cur.execute("UPDATE service_heartbeats SET details='Express shutdown clean stop'")
            if "updated_at" in cols:
                cur.execute("UPDATE service_heartbeats SET updated_at=?", (time.time(),))
            elif "last_seen" in cols:
                cur.execute("UPDATE service_heartbeats SET last_seen=?", (time.time(),))
            con.commit()
            print("[OK] service_heartbeats marked STOPPED_EXPRESS")
        else:
            print("[WARN] service_heartbeats missing")
        con.close()
    except Exception as e:
        print("[WARN] heartbeat stop mark skipped:", e)
PY

echo.
echo ==========================================================
echo   EXPRESS SHUTDOWN COMPLETE
echo   Safe for quick audit patch/relaunch loop.
echo ==========================================================

endlocal
exit /b 0
