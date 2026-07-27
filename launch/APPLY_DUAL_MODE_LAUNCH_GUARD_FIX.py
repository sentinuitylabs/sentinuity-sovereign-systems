from pathlib import Path
import shutil, time

ROOT = Path(__file__).resolve().parent.parent
BAT = ROOT / "launch" / "Launch_Sentinuity.bat"

old = 'echo [PAPER SAFE] Reasserting safe paper mode before launch...\npython "%~dp0FORCE_PAPER_SAFE_PRESTART_0707.py"\nif errorlevel 1 (\n  echo [ERROR] Paper-safe prestart clamp failed. Aborting launch.\n  pause\n  exit /b 1\n)'

new = 'if /i "!CFG_MODE!"=="live" (\n  echo [DUAL SAFE] Preserving operator-confirmed dual mode; paper remains active alongside gated live Mode B.\n) else (\n  echo [PAPER SAFE] Reasserting safe paper mode before launch...\n  "%PY%" "%~dp0FORCE_PAPER_SAFE_PRESTART_0707.py"\n  if errorlevel 1 (\n    echo [ERROR] Paper-safe prestart clamp failed. Aborting launch.\n    pause\n    exit /b 1\n  )\n)'

def main():
    if not BAT.exists():
        print("[FAIL] missing", BAT)
        return 1
    text = BAT.read_text(encoding="utf-8", errors="replace")
    if new in text:
        print("[OK] dual-safe launcher block already installed")
        return 0
    if old not in text:
        print("[FAIL] exact paper-safe block not found; launcher not modified")
        return 2
    backup = BAT.with_name(BAT.name + ".bak_dual_guard_" + time.strftime("%Y%m%d_%H%M%S"))
    shutil.copy2(BAT, backup)
    BAT.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\r\n")
    print("[OK] launcher patched")
    print("[OK] backup:", backup)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
