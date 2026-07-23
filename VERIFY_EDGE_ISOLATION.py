#!/usr/bin/env python3
from __future__ import annotations
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
EXPECTED = {'services/execution_engine.py': 'ed4e790e7746783af349b1d87ddf77b23b1b38f400ca656bda5a9fe1a130f0b6', 'services/ingest_pipeline.py': 'ec6348ca4c9032bafebac37a9a5401be70f1f9909b2bad132a56dc6a7c014878', 'services/system_guardian.py': 'bef116febd0b78c676f4453c308f6e47643555b23f687b9cbbda84ca184beb7f', 'services/ws_price_oracle.py': 'f66804e62aab702b5a1377580c6615004f55b7329340c7b5dc978dbc3fba184a', 'services/neural_supervisor.py': '1c8f0823d272e87df84f0a8a2afc7fe455205237da21111de4a5d7159b408df1', 'services/pattern_live_arming.py': '518f6fb355615f0db6dfe22fd68758a64ba62f70c3f3b75a30eb6ef81dec96c6', 'services/freshness_enforcer.py': '423515deb375a4daf1f1fd1ba8220c51620e807356a2c4659683f8148869cf8d'}
failures = []
passes = []

def check(ok: bool, message: str) -> None:
    (passes if ok else failures).append(message)
    print(("  + " if ok else "  - ") + message)

launch = (ROOT / "launch" / "Launch_Sentinuity.bat").read_text(encoding="utf-8", errors="replace")
shutdown = (ROOT / "launch" / "Shutdown_Sentinuity.bat").read_text(encoding="utf-8", errors="replace")
offline = (ROOT / "launch" / "Run_Council_Autobuilder_Offline.bat").read_text(encoding="utf-8", errors="replace")

check("services.council_autobuilder" not in launch, "main trading launcher does not start CouncilAutobuilder")
check("services.execution_engine" in launch, "main launcher still starts execution engine")
check("council_autobuilder" in shutdown.lower(), "shutdown explicitly targets CouncilAutobuilder")
check("services.council_autobuilder" in offline, "offline runner starts CouncilAutobuilder")
check("Trading/runtime processes are active" in offline, "offline runner blocks while trading is active")
check("already running" in offline, "offline runner blocks duplicate Council instances")

for rel, expected in EXPECTED.items():
    p = ROOT / rel
    if not p.exists():
        check(False, f"trading-core file exists: {rel}")
        continue
    actual = hashlib.sha256(p.read_bytes()).hexdigest()
    check(actual == expected, f"overnight trading-core hash preserved: {rel}")

print(f"\nRESULT: {len(passes)} passed, {len(failures)} failed")
if failures:
    print("EDGE ISOLATION VERIFICATION: FAIL")
    sys.exit(1)
print("EDGE ISOLATION VERIFICATION: PASS")
