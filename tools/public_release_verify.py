from pathlib import Path
import hashlib
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_EXCLUDES = {
    "MANIFEST_SHA256.txt",
    "PUBLIC_RELEASE_MANIFEST.json",
}

def fail(message):
    print(f"[FAIL] {message}")
    raise SystemExit(1)

def git_bytes(path):
    try:
        return subprocess.check_output(
            ["git", "show", f":{path}"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        fail(f"unable to read tracked/index file: {path}")

def tracked_files():
    try:
        raw = subprocess.check_output(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
        )
    except subprocess.CalledProcessError:
        fail("git ls-files failed")

    return sorted(
        p.decode("utf-8")
        for p in raw.split(b"\0")
        if p
    )


# ---------------------------------------------------------
# Basic public hygiene
# ---------------------------------------------------------

forbidden = []

for p in ROOT.rglob("*"):
    if not p.is_file() or ".git" in p.parts:
        continue

    rel = p.relative_to(ROOT)
    low = str(rel).lower().replace("\\", "/")

    if (
        p.suffix.lower() in {".pyc", ".db", ".sqlite", ".zip"}
        or "__pycache__" in p.parts
        or ".before_" in p.name.lower()
    ):
        forbidden.append(str(rel))

    if low.startswith(("logs/", "audits/", "backups/")):
        forbidden.append(str(rel))

if forbidden:
    print("[FAIL] forbidden public artefacts:")
    for item in sorted(set(forbidden)):
        print(" -", item)
    raise SystemExit(1)

required = [
    "README.md",
    ".gitignore",
    ".env.example",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "PUBLIC_RELEASE_AUDIT.md",
    "PUBLIC_RELEASE_MANIFEST.json",
    "MANIFEST_SHA256.txt",
    "assets/brand/sentinuity-hero.svg",
]

missing = [x for x in required if not (ROOT / x).exists()]

if missing:
    fail(f"missing required files: {missing}")


# ---------------------------------------------------------
# Licence consistency
# ---------------------------------------------------------

licence = (ROOT / "LICENSE").read_text(
    encoding="utf-8",
    errors="replace",
)

if "Apache License" not in licence or "Version 2.0" not in licence:
    fail("root LICENSE is not recognisable as Apache License 2.0")

readme = (ROOT / "README.md").read_text(
    encoding="utf-8",
    errors="replace",
)

audit = (ROOT / "PUBLIC_RELEASE_AUDIT.md").read_text(
    encoding="utf-8",
    errors="replace",
)

bad_phrases = [
    "not yet been selected",
    "No licence was silently selected",
    "add the chosen root `LICENSE`",
]

for phrase in bad_phrases:
    if phrase.lower() in readme.lower():
        fail(f"README contains stale licence wording: {phrase!r}")

    if phrase.lower() in audit.lower():
        fail(f"PUBLIC_RELEASE_AUDIT contains stale licence wording: {phrase!r}")

if "Apache License 2.0" not in readme:
    fail("README does not identify Apache License 2.0")

if "Apache License 2.0" not in audit:
    fail("PUBLIC_RELEASE_AUDIT does not identify Apache License 2.0")

print("[PASS] Apache-2.0 metadata is internally consistent")


# ---------------------------------------------------------
# Verify MANIFEST_SHA256.txt
# ---------------------------------------------------------

tracked = [
    p for p in tracked_files()
    if p not in MANIFEST_EXCLUDES
]

expected = {}

for path in tracked:
    data = git_bytes(path)
    expected[path] = hashlib.sha256(data).hexdigest()

manifest_path = ROOT / "MANIFEST_SHA256.txt"

actual = {}

for raw_line in manifest_path.read_text(
    encoding="utf-8",
    errors="replace",
).splitlines():

    line = raw_line.strip()

    if not line:
        continue

    parts = line.split(None, 1)

    if len(parts) != 2:
        fail(f"invalid MANIFEST_SHA256 line: {raw_line!r}")

    digest, path = parts
    path = path.removeprefix("./")

    actual[path] = digest.lower()

if set(actual) != set(expected):
    missing_manifest = sorted(set(expected) - set(actual))
    extra_manifest = sorted(set(actual) - set(expected))

    fail(
        "MANIFEST_SHA256 file set mismatch; "
        f"missing={missing_manifest}, extra={extra_manifest}"
    )

for path, digest in expected.items():
    if actual[path] != digest:
        fail(f"MANIFEST_SHA256 digest mismatch: {path}")

print(
    f"[PASS] MANIFEST_SHA256 verifies "
    f"{len(expected)} tracked files"
)


# ---------------------------------------------------------
# Verify PUBLIC_RELEASE_MANIFEST.json
# ---------------------------------------------------------

release_manifest = json.loads(
    (ROOT / "PUBLIC_RELEASE_MANIFEST.json").read_text(
        encoding="utf-8"
    )
)

if release_manifest.get("schema_version") != 2:
    fail("PUBLIC_RELEASE_MANIFEST schema_version must be 2")

if set(release_manifest.get("excluded", [])) != MANIFEST_EXCLUDES:
    fail("PUBLIC_RELEASE_MANIFEST excluded-file declaration is wrong")

entries = release_manifest.get("files", [])

if release_manifest.get("file_count") != len(entries):
    fail("PUBLIC_RELEASE_MANIFEST file_count does not equal entries")

by_path = {
    entry["path"]: entry
    for entry in entries
}

if set(by_path) != set(expected):
    missing_json = sorted(set(expected) - set(by_path))
    extra_json = sorted(set(by_path) - set(expected))

    fail(
        "PUBLIC_RELEASE_MANIFEST file set mismatch; "
        f"missing={missing_json}, extra={extra_json}"
    )

for path in tracked:
    data = git_bytes(path)
    digest = hashlib.sha256(data).hexdigest()

    entry = by_path[path]

    if entry.get("sha256") != digest:
        fail(f"PUBLIC_RELEASE_MANIFEST SHA256 mismatch: {path}")

    if entry.get("bytes") != len(data):
        fail(f"PUBLIC_RELEASE_MANIFEST byte-count mismatch: {path}")

print(
    f"[PASS] PUBLIC_RELEASE_MANIFEST verifies "
    f"{len(entries)} tracked files"
)

print("PUBLIC RELEASE VERIFY: PASS")
