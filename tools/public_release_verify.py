from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
forbidden=[]
for p in ROOT.rglob("*"):
    if not p.is_file() or ".git" in p.parts:
        continue
    rel=p.relative_to(ROOT)
    low=str(rel).lower().replace("\\","/")
    if p.suffix.lower() in {".pyc",".db",".sqlite",".zip"} or "__pycache__" in p.parts or ".before_" in p.name.lower():
        forbidden.append(str(rel))
    if low.startswith(("logs/","audits/","backups/")):
        forbidden.append(str(rel))
if forbidden:
    print("[FAIL] forbidden public artefacts:")
    for item in sorted(set(forbidden)):
        print(" -", item)
    raise SystemExit(1)
required=["README.md",".gitignore",".env.example","SECURITY.md","CONTRIBUTING.md","assets/brand/sentinuity-hero.svg"]
missing=[x for x in required if not (ROOT/x).exists()]
if missing:
    print("[FAIL] missing:",missing)
    raise SystemExit(1)
print("PUBLIC RELEASE VERIFY: PASS")
