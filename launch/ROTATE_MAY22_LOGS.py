import shutil, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / 'logs'
LOGS.mkdir(exist_ok=True)
old = [p for p in LOGS.glob('*.log') if p.is_file()]
if old:
    dest = LOGS / ('archive_' + time.strftime('%Y%m%d_%H%M%S'))
    dest.mkdir(parents=True, exist_ok=True)
    for p in old:
        try:
            shutil.move(str(p), str(dest / p.name))
        except Exception:
            pass
    print(f'rotated_logs={len(old)} to {dest}')
else:
    print('rotated_logs=0')
