#!/usr/bin/env python3
"""Fail-closed source and hygiene verifier for the Sentinuity V2 public paper release."""
from __future__ import annotations
import py_compile, re, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAILURES: list[str] = []

def fail(msg: str) -> None:
    FAILURES.append(msg)
    print(f"[FAIL] {msg}")

def ok(msg: str) -> None:
    print(f"[PASS] {msg}")

py_files = sorted(ROOT.rglob("*.py"))
with tempfile.TemporaryDirectory(prefix="sentinuity_compile_") as tmp:
    tmp_root = Path(tmp)
    for index, path in enumerate(py_files):
        try:
            py_compile.compile(str(path), cfile=str(tmp_root / f"{index}.pyc"), doraise=True)
        except Exception as exc:
            fail(f"compile {path.relative_to(ROOT)}: {exc}")
if not any(x.startswith("compile ") for x in FAILURES):
    ok(f"compiled {len(py_files)} Python files")

for path in ROOT.rglob("*"):
    if not path.is_file():
        continue
    rel = path.relative_to(ROOT).as_posix()
    if "__pycache__" in path.parts or path.suffix == ".pyc":
        fail(f"compiled cache included: {rel}")
    if path.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".zip"}:
        fail(f"runtime/archive debris included: {rel}")
    if re.search(r"(?:\.bak|\.backup|\.old|\.orig)$|before_", path.name, re.I):
        fail(f"backup file included: {rel}")

required = {
    "services/github_scout.py": ["record_inspiration", "quarantine_intake", "source_type=\"github_repo\""],
    "services/x_scout.py": ["record_inspiration", "quarantine_intake", "source_type=\"hashtag_channel\""],
    "services/council_execution_spine.py": ["select_task_fair", "record_progress"],
    "services/council_build_orchestrator.py": ["build_retrospective"],
    "services/sovereign_hub.py": ["render_paper_live_divergence"],
}
for rel, markers in required.items():
    text=(ROOT/rel).read_text(encoding="utf-8", errors="ignore")
    missing=[m for m in markers if m not in text]
    if missing: fail(f"{rel} missing integration markers: {missing}")
    else: ok(f"{rel} integration markers")

secret_patterns = {
    "OpenAI-style key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    "Anthropic key": re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
}
for path in ROOT.rglob("*"):
    if not path.is_file() or path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pyc"}:
        continue
    text=path.read_text(encoding="utf-8", errors="ignore")
    for label, pattern in secret_patterns.items():
        for match in pattern.finditer(text):
            value=match.group(0)
            if "xxx" not in value.lower() and "example" not in value.lower():
                fail(f"possible {label} in {path.relative_to(ROOT)}")
                break


# Public launch-folder hygiene and portability gates.
launch_files = sorted(p for p in (ROOT / "launch").iterdir() if p.is_file())
if len(launch_files) > 20:
    fail(f"launch folder contains {len(launch_files)} files; expected <=20 canonical files")
else:
    ok(f"launch folder canonical size: {len(launch_files)} files")

launch_text = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in launch_files)
if r"C:\Users\YourName" in launch_text:
    fail("private developer path remains in launch folder")
else:
    ok("launch folder is portable; no private developer path")
if re.search(r"taskkill\s+/F\s+/IM\s+python(?:w)?\.exe", launch_text, re.I):
    fail("public launch folder contains an unscoped all-Python kill")
else:
    ok("public shutdown does not kill unrelated Python processes")

obsolete_launch = {
    "Launch_MAY22_PAPER_ONLY.bat", "Restart_Sentinuity_Tight.bat",
    "Shutdown_Sentinuity_Express.bat", "Stop_All.bat",
    "APPLY_DUAL_MODE_LAUNCH_GUARD_FIX.py", "arm_dual_mode.py",
}
remaining_obsolete = sorted(x.name for x in launch_files if x.name in obsolete_launch)
if remaining_obsolete:
    fail(f"obsolete launch files remain: {remaining_obsolete}")
else:
    ok("obsolete launch variants removed")

if not (ROOT/"launch/Launch_Sentinuity_Public_Paper.bat").exists():
    fail("public paper-safe launcher missing")
else:
    ok("public paper-safe launcher present")

for required_file in ("README.md", "SECURITY.md", "LICENSE", "requirements.txt", ".env.example", ".gitignore"):
    if not (ROOT/required_file).exists():
        fail(f"release file missing: {required_file}")
    else:
        ok(f"release file present: {required_file}")

if FAILURES:
    print(f"\nRELEASE VERIFICATION FAILED: {len(FAILURES)} issue(s)")
    sys.exit(1)
print("\nRELEASE VERIFICATION PASSED")

