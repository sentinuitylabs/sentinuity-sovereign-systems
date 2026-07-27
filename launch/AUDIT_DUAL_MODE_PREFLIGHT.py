from __future__ import annotations
import os, re, sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "sentinuity_matrix.db"
ENV = ROOT / ".env"

def env_values():
    vals = dict(os.environ)
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
            if m:
                vals.setdefault(m.group(1), m.group(2).strip().strip('"').strip("'"))
    return vals

def main():
    failures = []
    required = [
        ROOT/"services"/"execution_engine.py",
        ROOT/"services"/"neural_supervisor.py",
        ROOT/"launch"/"Launch_Sentinuity.bat",
        ROOT/"launch"/"launch_config.py",
    ]
    for p in required:
        if not p.exists():
            failures.append("missing file: " + str(p.relative_to(ROOT)))

    vals = env_values()
    if not any(vals.get(k,"").strip() for k in ("SOLANA_PRIVATE_KEY","PRIVATE_KEY","WALLET_PRIVATE_KEY")):
        failures.append("missing private key env")
    if not any(vals.get(k,"").strip() for k in ("HELIUS_RPC_URL","CHAINSTACK_RPC","QUICKNODE_RPC","SOLANA_RPC_URL")):
        failures.append("missing RPC env")

    if not DB.exists():
        failures.append("sentinuity_matrix.db missing")
    else:
        con = sqlite3.connect(DB, timeout=20)
        qc = con.execute("PRAGMA quick_check").fetchone()[0]
        if qc != "ok":
            failures.append("quick_check=" + str(qc))
        con.close()

    bat_text = (ROOT/"launch"/"Launch_Sentinuity.bat").read_text(encoding="utf-8", errors="replace")
    if "Preserving operator-confirmed dual mode" not in bat_text:
        failures.append("dual-safe conditional launcher patch not detected")
    cfg_text = (ROOT/"launch"/"launch_config.py").read_text(encoding="utf-8", errors="replace")
    if "# hard force paper only" in cfg_text or "mode='paper'" in cfg_text.replace(" ",""):
        failures.append("launch_config.py still hard-forces paper")

    print("="*72)
    print("SENTINUITY DUAL MODE STATIC PREFLIGHT")
    print("="*72)
    if failures:
        for f in failures:
            print("[FAIL]", f)
        print("NOT READY TO SELECT DUAL")
        return 1
    print("[PASS] required files present")
    print("[PASS] private key and RPC env present (values hidden)")
    print("[PASS] database quick_check ok")
    print("[PASS] launcher no longer overwrites dual with paper-safe clamp")
    print("[PASS] launch_config is mode-aware")
    print("STATIC PREFLIGHT PASS")
    print("This does not prove RPC reachability, swap simulation, or transaction delivery.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
