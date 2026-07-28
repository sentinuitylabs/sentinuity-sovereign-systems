r"""
launch/wire_launch_security_alignment.py
========================================
Safe patcher for Sentinuity launch files.

It does NOT replace your BAT files wholesale. It only:
1. Replaces paranoid_scan calls with launch\sovereign_security_preflight.py.
2. Makes launch\preflight_verifier.py the preferred preflight verifier.
3. Adds SecuritySentinel service start line if missing.
4. Creates .bak_launch_alignment backups before editing.

Run from trading-bot root:
    python launch\wire_launch_security_alignment.py
"""
from __future__ import annotations

import re
import time
from pathlib import Path

ROOT = Path.cwd()
TARGETS = [
    ROOT / "Launch_Sentinuity.bat",
    ROOT / "Restart_Sentinuity.bat",
    ROOT / "launch" / "Launch_Sentinuity.bat",
    ROOT / "launch" / "Restart_Sentinuity.bat",
]

SENTINEL_LINE_ROOT_PATH = 'start "SecuritySentinel" /b cmd /c "cd /d %ROOT_PATH% && python -m services.openclaw_security_sentinel >> %LOG_PATH%\\openclaw_security_sentinel.log 2>&1"'
SENTINEL_LINE_ROOT = 'start "SecuritySentinel" /b cmd /c "cd /d %ROOT% && python -m services.openclaw_security_sentinel >> %LOG%\\openclaw_security_sentinel.log 2>&1"'


def _uses_root_path(txt: str) -> bool:
    return "%ROOT_PATH%" in txt or "%LOG_PATH%" in txt


def _sentinel_line(txt: str) -> str:
    return SENTINEL_LINE_ROOT_PATH if _uses_root_path(txt) else SENTINEL_LINE_ROOT


def patch_text(txt: str) -> tuple[str, list[str]]:
    changes: list[str] = []
    out = txt

    for pat, repl in [
        (r"(?i)python\s+paranoid_scan\.py", "python launch\\sovereign_security_preflight.py"),
        (r"(?i)python\s+\.\\paranoid_scan\.py", "python launch\\sovereign_security_preflight.py"),
        (r"(?i)python\s+\./paranoid_scan\.py", "python launch\\sovereign_security_preflight.py"),
        (r"(?i)python\s+sovereign_security_preflight\.py", "python launch\\sovereign_security_preflight.py"),
    ]:
        new = re.sub(pat, lambda _m, r=repl: r, out)
        if new != out:
            out = new
            changes.append("aligned security preflight call to launch\\sovereign_security_preflight.py")

    old_block = r'''set "PREFLIGHT_SCRIPT="
if exist "services\preflight_verifier.py" set "PREFLIGHT_SCRIPT=services\preflight_verifier.py"
if not defined PREFLIGHT_SCRIPT if exist "launch\preflight_verifier.py" set "PREFLIGHT_SCRIPT=launch\preflight_verifier.py"
if not defined PREFLIGHT_SCRIPT if exist "preflight_verifier.py" set "PREFLIGHT_SCRIPT=preflight_verifier.py"'''
    new_block = r'''set "PREFLIGHT_SCRIPT="
if exist "launch\preflight_verifier.py" set "PREFLIGHT_SCRIPT=launch\preflight_verifier.py"
if not defined PREFLIGHT_SCRIPT if exist "services\preflight_verifier.py" set "PREFLIGHT_SCRIPT=services\preflight_verifier.py"
if not defined PREFLIGHT_SCRIPT if exist "preflight_verifier.py" set "PREFLIGHT_SCRIPT=preflight_verifier.py"'''
    if old_block in out:
        out = out.replace(old_block, new_block)
        changes.append("preferred launch\\preflight_verifier.py in dynamic preflight selector")

    new = re.sub(r"(?i)echo\s+Running\s+services\\preflight_verifier\.py\.\.\.", lambda _m: "echo  Running launch\\preflight_verifier.py...", out)
    new = re.sub(r"(?i)python\s+services\\preflight_verifier\.py", lambda _m: "python launch\\preflight_verifier.py", new)
    new = re.sub(r"(?i)\[ERROR\] services\\preflight_verifier\.py failed", lambda _m: "[ERROR] launch\\preflight_verifier.py failed", new)
    if new != out:
        out = new
        changes.append("rewired direct services\\preflight_verifier.py call to launch\\preflight_verifier.py")

    if "openclaw_security_sentinel" not in out and "SecuritySentinel" not in out:
        lines = out.splitlines()
        insert_idx = None
        for i, line in enumerate(lines):
            if "services.system_guardian" in line or "SysGuardian" in line:
                insert_idx = i + 1
                break
        if insert_idx is None:
            for i, line in enumerate(lines):
                if "sovereign_hub" in line or "SovHub" in line:
                    insert_idx = i
                    break
        if insert_idx is None:
            insert_idx = len(lines)
        lines[insert_idx:insert_idx] = [
            "",
            "REM Security sentinel: OpenClaw/.env/Telegram/network observe/lockdown monitor",
            _sentinel_line(out),
            "",
        ]
        out = "\n".join(lines) + ("\n" if txt.endswith("\n") else "")
        changes.append("added SecuritySentinel start line")

    return out, changes


def main() -> int:
    print("=== Sentinuity Launch/Security Alignment Patcher ===")
    patched_any = False
    for path in TARGETS:
        if not path.exists():
            print(f"[SKIP] missing: {path}")
            continue
        txt = path.read_text(encoding="utf-8", errors="replace")
        patched, changes = patch_text(txt)
        if not changes:
            print(f"[OK] already aligned: {path}")
            continue
        backup = path.with_suffix(path.suffix + f".bak_launch_alignment_{int(time.time())}")
        backup.write_text(txt, encoding="utf-8")
        path.write_text(patched, encoding="utf-8")
        patched_any = True
        print(f"[PATCHED] {path}")
        print(f"         backup: {backup.name}")
        for c in dict.fromkeys(changes):
            print(f"         - {c}")
    print("=== Done ===")
    if not patched_any:
        print("No edits were needed, or launch files were not present in this folder.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
