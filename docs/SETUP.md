# Safe local setup

## Requirements

- Windows PowerShell or a compatible shell
- Python 3.11 or later
- SQLite
- Network credentials for only the data providers you choose to enable

Install the packages listed in `requirements.txt`, then copy `.env.example` to `.env`.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .\.env.example .\.env
python .\launch\preflight_verifier.py
```

Start with the public paper launcher. Do not put a funded wallet key into a first-time installation.
