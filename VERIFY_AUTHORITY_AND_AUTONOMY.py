from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent
checks: list[tuple[str, bool, str]] = []

def add(name: str, ok: bool, detail: str) -> None:
    checks.append((name, ok, detail))

launch = (ROOT / "launch" / "Launch_Sentinuity.bat").read_text(
    encoding="utf-8-sig", errors="replace"
)
policy = (ROOT / "services" / "autonomous_apply_policy.py").read_text(
    encoding="utf-8-sig", errors="replace"
)
forge = (ROOT / "services" / "forge_code_writer.py").read_text(
    encoding="utf-8-sig", errors="replace"
)
polaris_aux = (ROOT / "services" / "polaris_auxiliary.py").read_text(
    encoding="utf-8-sig", errors="replace"
)

add(
    "HITL remains enabled",
    "HITL_REQUIRED','1'" in launch or 'HITL_REQUIRED","1"' in launch,
    "launcher stamps HITL_REQUIRED=1",
)
add(
    "OpenClaw is gateway/security only",
    "Start OpenClaw gateway + Sentinel Shield" in launch
    and "services.openclaw_security_sentinel" in launch,
    "launcher starts gateway and Sentinel Shield separately from Polaris",
)
add(
    "Polaris remains an independent service",
    bool(re.search(r"-m\s+services\.polaris\b", launch)),
    "services.polaris is launched independently",
)
add(
    "Sovereign governor remains independent",
    bool(re.search(r"-m\s+services\.sovereign_governor\b", launch)),
    "Council/provider routing remains in sovereign_governor",
)
add(
    "Autonomous apply is default-deny",
    "default-deny" in policy and "human approval required" in policy,
    "unknown targets require human approval",
)
add(
    "Forge uses central policy",
    "can_autonomous_apply" in forge,
    "forge_code_writer imports and calls central policy",
)
add(
    "High-risk targets remain HITL",
    "HITL_REQUIRED_TARGETS" in forge,
    "capital-sensitive targets retain explicit human gate",
)
add(
    "Phantom Polaris lanes are honestly reported",
    "NOT_INSTALLED" in polaris_aux or "not_installed" in polaris_aux,
    "missing auxiliary modules are not reported as active",
)

failed = 0
print("=" * 78)
print("SENTINUITY V3 AUTHORITY / AUTONOMY STATIC VERIFIER")
print("=" * 78)
for name, ok, detail in checks:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    failed += not ok

print("")
print("Authority model:")
print("  OpenClaw = gateway + security integration")
print("  Polaris = planning/proposal service")
print("  Sovereign Governor = Council/provider orchestration")
print("  NIM/Anthropic = model providers; OpenClaw does not replace them")
print("  Safe allowlisted changes may auto-apply; high-risk changes stay HITL")

sys.exit(1 if failed else 0)
