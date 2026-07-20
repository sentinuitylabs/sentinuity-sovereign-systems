import hashlib
from pathlib import Path

env_file = Path('.env')
hash_file = Path('.env.hash')

if not env_file.exists():
    print('ERROR: .env not found')
    exit(1)

current_hash = hashlib.sha256(env_file.read_bytes()).hexdigest()

if not hash_file.exists():
    hash_file.write_text(current_hash)
    print('OK: hash created')
    exit(0)

stored_hash = hash_file.read_text().strip()
if current_hash == stored_hash:
    print('OK: .env integrity verified')
    exit(0)
else:
    print('MISMATCH: .env may have changed')
    exit(1)
