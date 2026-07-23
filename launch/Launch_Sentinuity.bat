@echo off

REM SENTINUITY_FONT_RESTORE_JUNE24_V3: preserve the operator-saved console font and size.

REM No codepage or PowerShell font switching is performed by this launcher.

REM SIGNOFF_UTF8_ROOTCAUSE_20260714: PYTHONUTF8 was previously CLEARED here,

REM which forced every child Python service onto cp1252 for unqualified

REM open() calls and was the systemic source of on-screen mojibake

REM (????? / ?? / ???? sequences). UTF-8 mode is now enforced.

set "PYTHONUTF8=1"

set "PYTHONIOENCODING=utf-8:replace"

REM SIGNOFF_NO_SIDECAR_SPINE_REPAIR_20260609: proof-safe dual sizing persistence. No sidecar.

@echo off

set PYTHONIOENCODING=utf-8



setlocal EnableExtensions EnableDelayedExpansion



title SENTINUITY SIGN-OFF LAUNCH



rem =========================================================

rem  SENTINUITY LAUNCHER  (UI-FIRST ORDER + FRESHNESS ONESHOT FIX, 2026-06-02)

REM === SENTINUITY BOOT CLEAN HOTFIX START ===

echo [BOOT-CLEAN] Running startup freshness purge...

set "SENT_HOTFIX_DIR="

if exist "%~dp0launch\startup_freshness_purge.py" set "SENT_HOTFIX_DIR=%~dp0launch"

if exist "%~dp0startup_freshness_purge.py" set "SENT_HOTFIX_DIR=%~dp0"

if exist "%~dp0..\launch\startup_freshness_purge.py" set "SENT_HOTFIX_DIR=%~dp0..\launch"



if not defined SENT_HOTFIX_DIR (

    echo [FATAL] startup_freshness_purge.py not found from launcher path

    pause

    exit /b 1

)



python "%SENT_HOTFIX_DIR%\startup_freshness_purge.py"

if errorlevel 1 (

    echo [FATAL] startup_freshness_purge failed. Launch aborted.

    pause

    exit /b 1

)



echo [BOOT-CLEAN] Running restart stale position reset...

python "%SENT_HOTFIX_DIR%\startup_restart_position_reset.py"

if errorlevel 1 (

    echo [FATAL] startup_restart_position_reset failed. Launch aborted.

    pause

    exit /b 1

)



echo [BOOT-CLEAN] Startup purge/reset complete.

REM === SENTINUITY BOOT CLEAN HOTFIX END ===

rem  Regression fix: Sovereign Hub UI now boots EARLY and

rem  non-blocking. All sign-off checks are preserved but moved

rem  to run AFTER the UI is spawned so they can never block it.

rem  No services removed, no gating weakened.

rem

rem  ORDER:

rem    A. Resolve repo root

rem    B. Verify .env (root first, launch\.env fallback)

rem    C. Activate venv if present

rem    D. Fast schema ensure (UI needs tables to render)

rem    E. Start Sovereign Hub UI immediately (separate process)

rem    F. Short capped port wait, then open browser

rem    G. THEN: Mode B confirm, launch_config, taskkill,

rem       bounded freshness one-shot, prelaunch, audits, full service spine

rem =========================================================



rem ---------- A. ROOT RESOLUTION ----------

rem Works whether run from repo root or launch\. Prefers the

rem folder that actually contains .env; falls back to source-tree

rem detection, then to the hard repo path.

set "SCRIPT_DIR=%~dp0"

for %%I in ("%SCRIPT_DIR%.") do set "SCRIPT_DIR=%%~fI"

for %%I in ("%SCRIPT_DIR%\..") do set "PARENT_DIR=%%~fI"

for %%I in ("%CD%") do set "CWD_DIR=%%~fI"

for %%I in ("%CD%\..") do set "CWD_PARENT_DIR=%%~fI"



set "HARD_ROOT=C:\Users\Polar\.openclaw\workspace\trading-bot"



set "ROOT_PATH="

rem Prefer the folder that actually contains .env (repo root).

if exist "%SCRIPT_DIR%\.env" set "ROOT_PATH=%SCRIPT_DIR%"

if not defined ROOT_PATH if exist "%PARENT_DIR%\.env" set "ROOT_PATH=%PARENT_DIR%"

if not defined ROOT_PATH if exist "%CWD_DIR%\.env" set "ROOT_PATH=%CWD_DIR%"

if not defined ROOT_PATH if exist "%CWD_PARENT_DIR%\.env" set "ROOT_PATH=%CWD_PARENT_DIR%"



rem Fallback to source-tree detection if .env not created yet.

if not defined ROOT_PATH if exist "%SCRIPT_DIR%\services\execution_engine.py" set "ROOT_PATH=%SCRIPT_DIR%"

if not defined ROOT_PATH if exist "%PARENT_DIR%\services\execution_engine.py" set "ROOT_PATH=%PARENT_DIR%"

if not defined ROOT_PATH if exist "%CWD_DIR%\services\execution_engine.py" set "ROOT_PATH=%CWD_DIR%"

if not defined ROOT_PATH if exist "%CWD_PARENT_DIR%\services\execution_engine.py" set "ROOT_PATH=%CWD_PARENT_DIR%"

rem Final fallback: the known repo root.

if not defined ROOT_PATH if exist "%HARD_ROOT%\services\execution_engine.py" set "ROOT_PATH=%HARD_ROOT%"

if not defined ROOT_PATH set "ROOT_PATH=%SCRIPT_DIR%"



cd /d "%ROOT_PATH%"



set "LOG_PATH=%ROOT_PATH%\logs"

set "RUNTIME_PATH=%ROOT_PATH%\runtime"

set "PYTHONPATH=%ROOT_PATH%"

if not exist "%LOG_PATH%" mkdir "%LOG_PATH%"

if not exist "%RUNTIME_PATH%" mkdir "%RUNTIME_PATH%"



rem ---------- B. ENV VERIFY (root first, launch\.env fallback) ----------

rem UI boot must NOT be blocked if root .env exists. We only warn.

set "ENV_FILE="

if exist "%ROOT_PATH%\.env" set "ENV_FILE=%ROOT_PATH%\.env"

if not defined ENV_FILE if exist "%ROOT_PATH%\launch\.env" set "ENV_FILE=%ROOT_PATH%\launch\.env"

if not defined ENV_FILE (

  echo   [WARN] No .env found at "%ROOT_PATH%\.env" or "%ROOT_PATH%\launch\.env".

  echo          UI will still start; live mode will be blocked until .env exists.

) else (

  echo   .env: "!ENV_FILE!"

)



rem ---------- C. VENV ----------

if exist "%ROOT_PATH%\.venv\Scripts\python.exe" (

  set "PY=%ROOT_PATH%\.venv\Scripts\python.exe"

) else (

  set "PY=python"

)



rem -- Resolve launch helper scripts from launch\ first, then root --

rem -- (these moved into launch\; bat finds them either place) --

set "ENV_CHECK_PY=%ROOT_PATH%\check_env_integrity.py"

if exist "%ROOT_PATH%\launch\check_env_integrity.py" set "ENV_CHECK_PY=%ROOT_PATH%\launch\check_env_integrity.py"

set "SETLIVE_PY=%ROOT_PATH%\set_live_mode.py"

if exist "%ROOT_PATH%\launch\set_live_mode.py" set "SETLIVE_PY=%ROOT_PATH%\launch\set_live_mode.py"

set "FORGE_SEED_PY=%ROOT_PATH%\forge_genesis_seed.py"

if exist "%ROOT_PATH%\launch\forge_genesis_seed.py" set "FORGE_SEED_PY=%ROOT_PATH%\launch\forge_genesis_seed.py"

set "WALCK_PY=%ROOT_PATH%\wal_checkpoint.py"

if exist "%ROOT_PATH%\launch\wal_checkpoint.py" set "WALCK_PY=%ROOT_PATH%\launch\wal_checkpoint.py"

set "LAUNCH_CONFIG_PY=%ROOT_PATH%\launch_config.py"

if exist "%ROOT_PATH%\launch\launch_config.py" set "LAUNCH_CONFIG_PY=%ROOT_PATH%\launch\launch_config.py"



set "PS_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"

if not exist "%PS_EXE%" set "PS_EXE="



cls

echo.

echo  =========================================================

echo   SENTINUITY SIGN-OFF LAUNCH

echo  =========================================================

echo   Root: %ROOT_PATH%

echo.



rem ---------- D. FAST SCHEMA ENSURE (UI needs tables) ----------

rem Lightweight, idempotent. This is the ONLY preflight kept ahead of

rem the UI because the dashboard reads these tables on first paint.

rem The heavy cleanup/migration still runs later in section G.

echo  Ensuring schema (fast, idempotent)...

"%PY%" -c "from services.schema import init_db, ensure_hub_compat_schema; init_db(); ensure_hub_compat_schema(); print('schema ready')" >> "%LOG_PATH%\schema_startup.log" 2>&1

if errorlevel 1 (

  echo   [WARN] Fast schema ensure reported an error; UI will still start. See logs\schema_startup.log

)



rem ---------- E. START SOVEREIGN HUB UI IMMEDIATELY ----------

rem Separate process via start (not call) so the launcher continues.

rem Started from repo root so relative paths in sovereign_hub resolve.



REM =========================================================

REM SIGNOFF_OBSERVER_SERVICES_20260611 / 20260621 ORDER FIX

REM Truth/accounting + copytrade observation services.

REM These are intentionally NOT started before the launch interview now.

REM PaperWalletRefresher can overwrite wallet/equity displays, so it must

REM start only AFTER any operator-requested Solana wallet reset has applied.

REM =========================================================

if not exist logs mkdir logs



echo Observer services queued until after wallet/config reset...



REM END SIGNOFF_OBSERVER_SERVICES_20260611





echo  Starting Sovereign Hub UI on http://localhost:8501 ...

start "SovHub" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m streamlit run services\sovereign_hub.py --server.headless true --server.address 127.0.0.1 --server.port 8501 >> ""%LOG_PATH%\sovereign_hub.log"" 2^>^&1"



rem ---------- F. CAPPED PORT WAIT, THEN OPEN BROWSER ----------

rem Poll the port up to ~15s; open browser as soon as it answers.

rem Never hangs forever - the loop is bounded.

echo  Waiting for dashboard port (max ~15s)...

set "PORT_UP="

if defined PS_EXE (

  for /l %%N in (1,1,15) do (

    if not defined PORT_UP (

      "%PS_EXE%" -NoProfile -ExecutionPolicy Bypass -Command "try { (New-Object Net.Sockets.TcpClient).Connect('127.0.0.1',8501); exit 0 } catch { exit 1 }" >nul 2>&1

      if not errorlevel 1 set "PORT_UP=1"

      if not defined PORT_UP timeout /t 1 /nobreak >nul

    )

  )

) else (

  timeout /t 5 /nobreak >nul

)



rem SIGNOFF_LANDING_BOOT_20260715:

rem Open the canonical local HOME immediately. Cloudflare starts later in the interview,

rem so opening the public hostname here races the tunnel and can show a stale/blank page.

if not defined PUBLIC_URL set "PUBLIC_URL=https://sentinuity.online"

if defined PORT_UP echo   Dashboard is up - opening http://localhost:8501/? ...

if defined PORT_UP start "" "http://localhost:8501/?"

if not defined PORT_UP echo   [WARN] Port 8501 not confirmed within timeout; open http://localhost:8501/? manually.



rem =========================================================

rem  G. DEEPER SIGN-OFF CHECKS + FULL SERVICE SPINE

rem  Everything below was previously ABOVE the UI start and was

rem  blocking it. It is unchanged in substance - only moved here.

rem =========================================================



echo.

echo  =========================================================

echo   SENTINUITY LAUNCH INTERVIEW

echo   Answer each section. Press ENTER to accept the default.

echo  =========================================================



set "PAPER_SIZE=25"

set "LIVE_SIZE=3"

set "PAPER_MAX=3"

set "LIVE_MAX=1"

set "PAPER_CONF=0.50"

set "LIVE_CONF=0.80"

set "EXCEPTIONAL=1"

set "START_GATEWAY=1"

set "START_CLOUDFLARE=1"

set "CFG_MODE=paper"

set "LIVE_OK=NO"

set "SUBSTRATE_MODE=paper"

set "SUBSTRATE_PAPER_USD=250"

set "SUBSTRATE_POS_SIZE=25"

set "SUBSTRATE_LIVE_USD=0"

set "SOLANA_BAL_RESET=0"



rem -- Solana paper wallet: current balance is read from DB below and shown in

rem    the interview so you can set-and-leave it (same as the substrate balance).

set "SOLANA_PAPER_BAL=100.00"



rem -- RESTORE LAST-SESSION VALUES FROM DB (so operator doesn't retype each launch) --

rem Reads PAPER_POSITION_SIZE_USD, SUBSTRATE_PAPER_BALANCE_USD, SUBSTRATE_POSITION_SIZE_USD,

rem and the current Solana paper wallet balance from system_state.

"%PY%" -c "import sqlite3; c=sqlite3.connect('sentinuity_matrix.db',timeout=5); g=lambda k,d:(lambda r:r[0] if r else d)(c.execute('SELECT value FROM system_config WHERE key=?',(k,)).fetchone()); _has_state=c.execute('SELECT name FROM sqlite_master WHERE type=? AND name=?',('table','system_state')).fetchone(); _pwt=c.execute('SELECT name FROM sqlite_master WHERE type=? AND name=?',('table','paper_wallet')).fetchone(); _pw=c.execute('SELECT equity FROM paper_wallet WHERE wallet_name=?',('main',)).fetchone() if _pwt else None; _pe=g('PAPER_EQUITY_USD',None); _ps=c.execute('SELECT paper_equity FROM system_state WHERE id=1').fetchone() if _has_state else None; _src=(_pw[0] if _pw and _pw[0] not in (None,0) else (_pe if _pe not in (None,'') else (_ps[0] if _ps and _ps[0] not in (None,0) else None))); _bal=format(float(_src),'.2f') if _src not in (None,'') else '100.00'; print(str(g('PAPER_POSITION_SIZE_USD','25'))+'|'+str(g('SUBSTRATE_PAPER_BALANCE_USD','250'))+'|'+str(g('SUBSTRATE_POSITION_SIZE_USD','25'))+'|'+_bal); c.close()" > "%LOG_PATH%\last_session_vals.tmp" 2>nul

if exist "%LOG_PATH%\last_session_vals.tmp" (

  for /f "tokens=1,2,3,4 delims=|" %%A in (%LOG_PATH%\last_session_vals.tmp) do (

    if not "%%A"=="" set "PAPER_SIZE=%%A"

    if not "%%B"=="" set "SUBSTRATE_PAPER_USD=%%B"

    if not "%%C"=="" set "SUBSTRATE_POS_SIZE=%%C"

    if not "%%D"=="" set "SOLANA_PAPER_BAL=%%D"

  )

  del /f /q "%LOG_PATH%\last_session_vals.tmp" >nul 2>&1

  echo   [RESTORED] Last session: Solana wallet=$!SOLANA_PAPER_BAL! paper-size=$!PAPER_SIZE! Substrate=$!SUBSTRATE_PAPER_USD! SubPos=$!SUBSTRATE_POS_SIZE!

)



rem -- SET-AND-LEAVE GATE ---------------------------------------------------

rem Use last launch's settings as-is, or type C to change them. Default = keep.

echo.

echo  =========================================================

echo   SAVED CONFIG FROM LAST LAUNCH

echo  =========================================================

echo   Solana wallet balance:  $!SOLANA_PAPER_BAL!

echo   Solana paper size:      $!PAPER_SIZE! per trade

echo   Substrate paper bal:    $!SUBSTRATE_PAPER_USD!

echo   Substrate trade size:   $!SUBSTRATE_POS_SIZE! per trade

echo.

echo   Press ENTER to launch with these saved settings.

echo   Type C then ENTER to change any of them.

echo.

set "RECONFIG="

set /p RECONFIG="  Keep saved config? [ENTER=keep / C=change]: "

if /i "!RECONFIG!"=="C" goto :RUN_INTERVIEW



rem Keep-path: default Solana to paper unless last launch was live and you re-confirm.

set "CFG_MODE=paper"

echo   [CONFIG] Using saved settings. Solana=PAPER  Paper=$!PAPER_SIZE!  Substrate=$!SUBSTRATE_PAPER_USD!  SubPos=$!SUBSTRATE_POS_SIZE!

goto :CONFIG_DONE



:RUN_INTERVIEW



rem =========================================================

rem  SECTION 1 - SOLANA PUMP.FUN LANE

rem =========================================================

echo.

echo  ---------------------------------------------------------

echo   SECTION 1: SOLANA PUMP.FUN LANE

echo   The primary meme-coin discovery and execution pipeline.

echo  ---------------------------------------------------------

echo.

echo   Paper mode runs continuously and always learns.

echo   Dual mode also arms the live Mode B gate (requires .env).

echo.

echo   1  Paper only   (recommended - safe, always learning)

echo   2  Dual mode    (paper + live Mode B gate)

echo.

set /p MODE_CHOICE="  Solana lane mode [1=paper / 2=dual, enter for 1]: "

if "!MODE_CHOICE!"=="2" (set "CFG_MODE=live") else (set "CFG_MODE=paper")



rem ---- Solana paper sizing + wallet bankroll ----

echo.

set /p SOL_PAPER_IN="  Solana PAPER position size USD per trade [enter to keep $!PAPER_SIZE!]: "

if not "!SOL_PAPER_IN!"=="" set "PAPER_SIZE=!SOL_PAPER_IN!"

set "_NUM_CHECK=!PAPER_SIZE!"

for /f "delims=0123456789." %%X in ("!_NUM_CHECK!") do (echo   [WARN] not a valid number, using 25. & set "PAPER_SIZE=25")

echo   [CONFIG] Solana paper size: $!PAPER_SIZE! per trade.



echo.

echo   Solana paper WALLET BALANCE controls bankroll + ROI baseline.

echo   Press ENTER to keep current $!SOLANA_PAPER_BAL!, or type a new amount to RESET it.

echo   ^(Resetting sets balance AND initial_capital together so ROI%% stays honest.^)

set /p SOL_BAL_IN="  Solana paper wallet balance USD [enter to keep $!SOLANA_PAPER_BAL!]: "

if not "!SOL_BAL_IN!"=="" (

  set "_BAL_OK=1"

  for /f "delims=0123456789." %%X in ("!SOL_BAL_IN!") do (echo   [WARN] not a valid number, keeping $!SOLANA_PAPER_BAL!. & set "_BAL_OK=0")

  if "!_BAL_OK!"=="1" (

    set "SOLANA_PAPER_BAL=!SOL_BAL_IN!"

    set "SOLANA_BAL_RESET=1"

    echo   [CONFIG] Solana wallet balance will be RESET to $!SOL_BAL_IN! ^(ROI baseline reset^).

  )

)



rem ---- Solana live / dual arming ----

if not "!CFG_MODE!"=="live" goto :AFTER_SOLANA_LIVE_ARM



echo.

echo  ---------------------------------------------------------

echo   SOLANA LIVE MODE B ARMING

echo   Paper stays ON. Live only fires when Mode B clears.

echo  ---------------------------------------------------------

if exist "!ENV_CHECK_PY!" (

  "%PY%" "!ENV_CHECK_PY!" > "%LOG_PATH%\env_integrity.log" 2^>^&1

  if errorlevel 1 (

    echo   [BLOCKED] .env integrity failed - see logs\env_integrity.log

    call :SHOW_LOG_TAIL "%LOG_PATH%\env_integrity.log"

    echo   Solana live mode cancelled. Falling back to PAPER.

    set "CFG_MODE=paper"

  )

) else (

  if not defined ENV_FILE (

    echo   [BLOCKED] .env not found - live mode cancelled.

    set "CFG_MODE=paper"

  )

)

if not "!CFG_MODE!"=="live" goto :AFTER_SOLANA_LIVE_ARM



echo.

set /p LIVE_SIZE_IN="  Solana LIVE position size USD per trade [enter to keep $!LIVE_SIZE!]: "

if not "!LIVE_SIZE_IN!"=="" set "LIVE_SIZE=!LIVE_SIZE_IN!"

set "_NUM_CHECK=!LIVE_SIZE!"

for /f "delims=0123456789." %%X in ("!_NUM_CHECK!") do (echo   [WARN] not a valid number, using 3. & set "LIVE_SIZE=3")

echo   [CONFIG] Solana live size: $!LIVE_SIZE! per trade.



echo.

set "LIVE_OK=NO"

set /p LIVE_OK="  Type YES to ARM Solana live Mode B at $!LIVE_SIZE! per trade [YES required]: "

if /i not "!LIVE_OK!"=="YES" (

  echo   Cancelled. Falling back to Solana PAPER.

  set "CFG_MODE=paper"

)



:AFTER_SOLANA_LIVE_ARM

rem =========================================================

rem  SECTION 2 - SUBSTRATE DESK (Alts and Natives)

rem =========================================================

echo.

echo  ---------------------------------------------------------

echo   SECTION 2: SUBSTRATE DESK - ALTS + NATIVES

echo   BTC / ETH / SOL / XRP / SUI / BNB

echo   Hybrid algo-council filter. Paper always learns.

echo   Live substrate requires separate sign-off (not yet available).

echo  ---------------------------------------------------------

echo.

echo   1  Paper only   (recommended - council + algo, no live risk)

echo   2  Dual mode    (reserved - live substrate not yet enabled)

echo.

set /p SUB_MODE_IN="  Substrate mode [1=paper / 2=dual, enter for 1]: "

if "!SUB_MODE_IN!"=="2" (

  echo   [INFO] Substrate live is not yet unlocked. Defaulting to paper.

  set "SUBSTRATE_MODE=paper"

) else (

  set "SUBSTRATE_MODE=paper"

)

echo.

set /p SUB_PAPER_IN="  Substrate PAPER balance USD [enter to keep $!SUBSTRATE_PAPER_USD!]: "

if not "!SUB_PAPER_IN!"=="" set "SUBSTRATE_PAPER_USD=!SUB_PAPER_IN!"

set "_NUM_CHECK=!SUBSTRATE_PAPER_USD!"

for /f "delims=0123456789." %%X in ("!_NUM_CHECK!") do (echo   [WARN] not a valid number, using 250. & set "SUBSTRATE_PAPER_USD=250")

echo   [CONFIG] Substrate paper balance: $!SUBSTRATE_PAPER_USD!



echo.

set /p SUB_POS_IN="  Substrate position size per trade [enter to keep $!SUBSTRATE_POS_SIZE!]: "

if not "!SUB_POS_IN!"=="" set "SUBSTRATE_POS_SIZE=!SUB_POS_IN!"

set "_NUM_CHECK=!SUBSTRATE_POS_SIZE!"

for /f "delims=0123456789." %%X in ("!_NUM_CHECK!") do (echo   [WARN] not a valid number, using 25. & set "SUBSTRATE_POS_SIZE=25")

echo   [CONFIG] Substrate position size: $!SUBSTRATE_POS_SIZE! per trade.



:CONFIG_DONE



rem -- SOLANA PAPER WALLET BALANCE RESET (only when operator entered a new amount) --

rem Single-file / no permanent sidecar. The bat extracts the embedded Python payload

rem below to %%TEMP%%, runs it, then deletes it. This avoids fragile python -c quoting.

if not "!SOLANA_BAL_RESET!"=="1" goto :AFTER_SOLANA_WALLET_RESET



echo.

echo   [WALLET] Resetting Solana paper wallet to $!SOLANA_PAPER_BAL! ^(canonical wallet/equity stores^) ...

set "SENT_LAUNCH_BAT=%~f0"

set "WALLET_RESET_PY=%TEMP%\sentinuity_reset_solana_wallet_%RANDOM%%RANDOM%.py"

if not defined PS_EXE (

  echo   [ERROR] PowerShell not available; cannot extract embedded wallet reset payload.

  goto :AFTER_SOLANA_WALLET_RESET

)

"%PS_EXE%" -NoProfile -ExecutionPolicy Bypass -Command "$bat=$env:SENT_LAUNCH_BAT; $out=$env:WALLET_RESET_PY; $lines=Get-Content -LiteralPath $bat; $s=[Array]::IndexOf($lines,'::BEGIN_SOLANA_WALLET_RESET_PY'); $e=[Array]::IndexOf($lines,'::END_SOLANA_WALLET_RESET_PY'); if($s -lt 0 -or $e -le $s){ throw 'embedded wallet reset payload markers not found' }; $lines[($s+1)..($e-1)] | Set-Content -LiteralPath $out -Encoding UTF8" >nul 2>&1

if errorlevel 1 (

  echo   [WARN] Could not extract embedded wallet reset payload.

  goto :AFTER_SOLANA_WALLET_RESET

)

"%PY%" "!WALLET_RESET_PY!" "!SOLANA_PAPER_BAL!" >> "%LOG_PATH%\solana_wallet_reset.log" 2^>^&1

set "WALLET_RESET_RC=!ERRORLEVEL!"

del /f /q "!WALLET_RESET_PY!" >nul 2>&1

if not "!WALLET_RESET_RC!"=="0" (

  echo   [WARN] Wallet reset issue - see logs\solana_wallet_reset.log

  call :SHOW_LOG_TAIL "%LOG_PATH%\solana_wallet_reset.log"

) else (

  type "%LOG_PATH%\solana_wallet_reset.log"

)

:AFTER_SOLANA_WALLET_RESET

rem =========================================================

rem  SECTION 3 - SECURITY: OPENCLAW + SENTINEL SHIELD

rem =========================================================

echo.

echo  ---------------------------------------------------------

echo   SECTION 3: SECURITY

echo   OpenClaw gateway and Sentinel Shield protect the organism.

echo  ---------------------------------------------------------

echo.



set "SENTINEL_LIVE_ARMED=0"

"%PY%" -c "import sqlite3,os,sys; c=sqlite3.connect('sentinuity_matrix.db',timeout=5); g=lambda k:(lambda r:(r[0] if r else ''))(c.execute('SELECT value FROM system_config WHERE key=?',(k,)).fetchone()); keys=['TRADING_MODE','DUAL_MODE_ENABLED','LIVE_TRADING_ENABLED','LIVE_ARMED']; v={k:str(g(k)).lower() for k in keys}; envk=any(os.getenv(e) for e in ('PRIVATE_KEY','SOLANA_PRIVATE_KEY','WALLET_PRIVATE_KEY')); armed=(v.get('TRADING_MODE')=='live' or v.get('DUAL_MODE_ENABLED')=='1' or v.get('LIVE_TRADING_ENABLED')=='1' or v.get('LIVE_ARMED')=='1' or envk); c.close(); sys.exit(7 if armed else 0)" 2>nul

if errorlevel 7 set "SENTINEL_LIVE_ARMED=1"

if /i "!LIVE_OK!"=="YES" set "SENTINEL_LIVE_ARMED=1"



if "!SENTINEL_LIVE_ARMED!"=="1" (

  echo   Live or dual money is armed.

  echo   Sentinel Shield is STRONGLY recommended when live funds are active.

  echo.

  choice /C YN /T 8 /D Y /M "  Start OpenClaw gateway + Sentinel Shield? [Y/n, 8s default=Y]: "

  if errorlevel 2 (

    echo.

    echo   *************************************************************

    echo   [SECURITY WARNING] LIVE MONEY ARMED - SENTINEL OFF.

    echo   You declined the Sentinel Shield while the live lane is armed.

    echo   Continuing per operator choice - UNPROTECTED.

    echo   *************************************************************

    echo.

    set "START_SENTINEL=0"

  ) else (

    set "START_SENTINEL=1"

  )

) else (

  echo   Paper / test mode. Sentinel is optional.

  echo.

  choice /C YN /T 8 /D N /M "  Start OpenClaw gateway + Sentinel Shield? [y/N, 8s default=N]: "

  if errorlevel 2 (set "START_SENTINEL=0") else (set "START_SENTINEL=1")

)

echo.



:APPLY_CONFIG

cd /d "%ROOT_PATH%"



echo.

echo  Applying configuration...



rem -- Stamp paper size for Solana lane (must run before launch_config.py

rem    so prelaunch does not wipe the operator's choice).

"%PY%" -c "import sqlite3; c=sqlite3.connect('sentinuity_matrix.db',timeout=10); v=str(float('!PAPER_SIZE!')); c.execute('INSERT INTO system_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',('POSITION_SIZE_USD',v)); c.execute('INSERT INTO system_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',('PAPER_POSITION_SIZE_USD',v)); c.commit(); print('SOL_PAPER_SIZE_STAMPED','$'+v); c.close()" >> "%LOG_PATH%\operator_size_restamp.log" 2^>^&1

echo   [CONFIG] Solana paper size $!PAPER_SIZE! stamped.



rem -- Stamp substrate desk config from operator interview ---------------------

"%PY%" -c "import sqlite3; c=sqlite3.connect('sentinuity_matrix.db',timeout=10); now=__import__('time').time(); _ex=c.execute('SELECT value FROM system_config WHERE key=?',('SUBSTRATE_PAPER_CASH_USD',)).fetchone(); _cash_set=bool(_ex and str(_ex[0]).strip() not in ('','0','0.0')); pairs=[('SUBSTRATE_PAPER_BALANCE_USD','!SUBSTRATE_PAPER_USD!'),('SUBSTRATE_POSITION_SIZE_USD','!SUBSTRATE_POS_SIZE!'),('SUBSTRATE_AUTO_DEPLOY_PAPER','1'),('SUBSTRATE_COUNCIL_AUTO_APPROVE','1'),('SUBSTRATE_MIN_COUNCIL_CONVICTION','0.60'),('SUBSTRATE_MAX_OPEN','3'),('SUBSTRATE_TP_PCT','20'),('SUBSTRATE_SL_PCT','8'),('SUBSTRATE_MAX_HOLD_HOURS','72'),('SUBSTRATE_LIVE_ENABLED','0')]; [c.execute('INSERT INTO system_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(k,v)) for k,v in pairs]; (c.execute('INSERT INTO system_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',('SUBSTRATE_PAPER_CASH_USD','!SUBSTRATE_PAPER_USD!')) if not _cash_set else None); c.commit(); print('SUBSTRATE_CONFIG_STAMPED balance_ref=$!SUBSTRATE_PAPER_USD! pos=$!SUBSTRATE_POS_SIZE! cash_initialized=' + ('no_preserved' if _cash_set else 'yes_first_time')); c.close()" >> "%LOG_PATH%\substrate_config_stamp.log" 2^>^&1

if errorlevel 1 (

  echo   [WARN] Substrate config stamp failed - see logs\substrate_config_stamp.log. Defaults apply.

) else (

  echo   [CONFIG] Substrate paper balance $!SUBSTRATE_PAPER_USD! stamped, position size $!SUBSTRATE_POS_SIZE!.

)



rem -- Init substrate desk schema if not yet created ----------------------------

if exist "tools\init_substrate_desk.py" (

  "%PY%" tools\init_substrate_desk.py >> "%LOG_PATH%\init_substrate_desk.log" 2^>^&1

  if errorlevel 1 (echo   [WARN] Substrate desk init had warnings - see logs\init_substrate_desk.log) else (echo   [CONFIG] Substrate desk schema ready.)

)



echo.

echo  Applying validation-safe configuration...

if "!CFG_MODE!"=="live" (

  "%PY%" "!LAUNCH_CONFIG_PY!" dual "!LIVE_SIZE!" "%PAPER_MAX%" "%LIVE_CONF%" "%EXCEPTIONAL%" >> "%LOG_PATH%\launch_config.log" 2^>^&1

) else (

  "%PY%" "!LAUNCH_CONFIG_PY!" paper "!PAPER_SIZE!" "%PAPER_MAX%" "%PAPER_CONF%" "%EXCEPTIONAL%" >> "%LOG_PATH%\launch_config.log" 2^>^&1

)

if errorlevel 1 (

  echo   [ERROR] launch_config.py failed.

  call :SHOW_LOG_TAIL "%LOG_PATH%\launch_config.log"

  echo   Continuing - UI remains up. Review launch_config.log before trusting trades.

)



if not exist "tools\apply_launch_safety_pins.py" (
  echo   [ERROR] Missing canonical safety contract: tools\apply_launch_safety_pins.py
  echo   Launch aborted before trading services start.
  exit /b 1
)
"%PY%" tools\apply_launch_safety_pins.py >> "%LOG_PATH%\launch_config_safety_pins.log" 2^>^&1
if errorlevel 1 (
  echo   [ERROR] Canonical safety pins failed.
  call :SHOW_LOG_TAIL "%LOG_PATH%\launch_config_safety_pins.log"
  exit /b 1
)

REM SIGNOFF_FINAL_GATE_20260611 - pin momentum gate to shadow posture before the

REM executor starts. DB-only keys; stale hard-mode flags must not survive relaunch.

"%PY%" tools\apply_momentum_launch_guard.py >> "%LOG_PATH%\momentum_launch_guard.log" 2^>^&1

if errorlevel 1 echo   [WARN] momentum launch guard failed - executor will still demote unsanctioned hard mode at runtime. Check momentum_launch_guard.log.

if "!CFG_MODE!"=="live" (

  if exist "!SETLIVE_PY!" "%PY%" "!SETLIVE_PY!" >> "%LOG_PATH%\set_live_mode.log" 2^>^&1

)



rem -- GOLDEN TIMING RESTORE (SIGNOFF_GOLDEN_GATES_20260620) --------------------

rem Restores the proven May/June golden pipeline timing gates on every launch.

rem These values were confirmed drifted (240/300 vs golden 900) by config-diff audit.

rem With a ~250s signal-to-open pipeline latency, any gate below 300s starves entries.

rem This runs AFTER launch_config.py so it always wins over any config-file regression.

rem Non-fatal: a failure here logs a warning but never blocks launch.

%PY% -c "import sqlite3; c=sqlite3.connect(r'sentinuity_matrix.db',timeout=10); pairs=[('EXECUTOR_MAX_SIGNAL_AGE_SEC','900'),('EXECUTOR_MAX_SIGNAL_AGE','900'),('EXECUTOR_PHASE_A_MAX_SIGNAL_AGE','900'),('SUPERVISOR_MAX_SIGNAL_AGE_SEC','900'),('SUPERVISOR_MAX_SIGNAL_AGE','900'),('SUPERVISOR_PHASE_A_SIGNAL_AGE_SEC','900'),('PREENTRY_MAX_EXEC_READY_AGE_SECONDS','900'),('MIN_PRICE_MOMENTUM_5M','-999'),('SUPERVISOR_REQUIRE_POSITIVE_MTM','0'),('MOMENTUM_GATE_ENABLED','1'),('MOMENTUM_GATE_SHADOW_ONLY','1'),('MOMENTUM_GATE_HARD_APPLIES_TO_PAPER','0'),('SUPERVISOR_MIN_MINT_CONFIDENCE','0.65'),('SUPERVISOR_FRESHNESS_FLOOR','0.60')]; c.executemany('INSERT INTO system_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',pairs); c.commit(); c.close()" >> "%LOG_PATH%\golden_gate_restore.log" 2>&1

if errorlevel 1 (

  echo   [WARN] Golden gate restore failed - see logs\golden_gate_restore.log

) else (

  echo   [OK] Golden timing gates restored: age=900s momentum=shadow confidence=0.65

)

echo.



echo.



if /i "!CFG_MODE!"=="live" (

  echo [DUAL SAFE] Preserving operator-confirmed dual mode; paper remains active alongside gated live Mode B.

) else (

  echo [PAPER SAFE] Reasserting safe paper mode before launch...

  "%PY%" "%~dp0FORCE_PAPER_SAFE_PRESTART_0707.py"

  if errorlevel 1 (

    echo [ERROR] Paper-safe prestart clamp failed. Aborting launch.

    pause

    exit /b 1

  )

)



echo  Stopping previous Sentinuity service windows...

rem NOTE: SovHub is intentionally NOT in this kill list - the UI we just

rem started must survive. Only trading/aux service windows are recycled.

for %%T in (

  "Gateway" "Tunnel" "SENTINUITY SOVEREIGN CONSOLE" "SENTINUITY GUARDIAN"

  "PumpMon" "Ingest" "MktIntel" "WsOracle" "Supervisor" "Executor" "Freshness" "RollEvict"

  "SysGuardian" "SovGovernor" "Polaris" "SPE" "Replay" "CouncilBuild" "ForgeOrchestrator"

  "ForgeWriter" "WalletScout" "TelegramScout" "XScout" "SymbioticRouter" "Reconciler" "SettleRecovery" "Vault"

  "PaperWalletRefresher" "SmartWalletTradeIngester" "OpenClaw Security Sentinel"

  "ActivePipelineCleaner10m" "PriceEnricher" "PeriodicRefresh" "WinnerArchiver" "ShadowTracker"

  "MacroPriceFeed" "MacroChannel" "GithubScout" "SecuritySentinel" "AutoSweep"

) do taskkill /F /FI "WINDOWTITLE eq %%~T" /T >nul 2^>^&1



echo.

echo  Running prelaunch schema and freshness cleanup...

rem -- BACKUP HYGIENE PRELAUNCH GUARD - SIGNOFF_BACKUP_HYGIENE_20260613 ----------

if exist tools\backup_hygiene.py (

  "%PY%" tools\backup_hygiene.py --prelaunch >> "%LOG_PATH%\backup_hygiene_prelaunch.log" 2>&1

  if errorlevel 1 (

    echo.

    echo   [ERROR] LAUNCH BLOCKED by backup_hygiene: disk critically low.

    echo   Free disk space, then re-run Launch_Sentinuity.bat.

    echo   See logs\backup_hygiene_prelaunch.log for details.

    echo.

    pause

    exit /b 1

  )

)

rem -- END BACKUP HYGIENE -------------------------------------------------------



if exist launch\startup_freshness_purge.py (

  "%PY%" launch\startup_freshness_purge.py > "%LOG_PATH%\startup_freshness_purge_console.log" 2^>^&1

  if errorlevel 1 (

    echo   [ERROR] startup_freshness_purge.py failed. See logs\startup_freshness_purge_console.log

    call :SHOW_LOG_TAIL "%LOG_PATH%\startup_freshness_purge_console.log"

    echo   Continuing - UI remains up. Review startup_freshness_purge_console.log.

  )

)



rem SIGN-OFF FIX 2026-06-02:

rem Never invoke "python -m services.freshness_enforcer prelaunch" synchronously here.

rem Current evidence shows that CLI path can complete cleanup and then fall through into

rem the daemon/service loop, blocking the BAT before the trading spine starts.

rem This launcher calls the proven one-shot functions directly instead.

"%PY%" -c "from pathlib import Path; import json; from services.freshness_enforcer import ensure_freshness_config, run_prelaunch_freshness_cleanup; db=Path('sentinuity_matrix.db'); ensure_freshness_config(db); r=run_prelaunch_freshness_cleanup(db, dry_run=False); print('PRELAUNCH_ONESHOT_COMPLETE'); print(json.dumps(r, sort_keys=True))" >> "%LOG_PATH%\freshness_prelaunch.log" 2^>^&1

if errorlevel 1 (

  echo   [ERROR] freshness one-shot prelaunch failed. See logs\freshness_prelaunch.log

  call :SHOW_LOG_TAIL "%LOG_PATH%\freshness_prelaunch.log"

  echo   Continuing - UI remains up. Review freshness_prelaunch.log.

)



"%PY%" launch\prelaunch.py >> "%LOG_PATH%\prelaunch.log" 2^>^&1

if errorlevel 1 (

  echo   [ERROR] prelaunch failed. See logs\prelaunch.log

  call :SHOW_LOG_TAIL "%LOG_PATH%\prelaunch.log"

  echo   Continuing - UI remains up. Review prelaunch.log.

)



rem -- LIVING_FOREST_PHASE5: seed the council so it BUILDS, not idles. --

"%PY%" tools\seed_build_tasks.py >> "%LOG_PATH%\seed_build_tasks.log" 2^>^&1

if errorlevel 1 (echo   [WARN] Build-task seeding failed - council may sit idle. See logs\seed_build_tasks.log)



rem -- WORLD_OS_20260612: seed the council world board (writes ONLY

rem    council_world_tasks + world_command_log - never trading tables). --

"%PY%" -c "from ui.world_tasks import ensure_schema, seed_standing; ensure_schema(); print('world_tasks_seeded={}'.format(seed_standing()))" >> "%LOG_PATH%\world_os_seed.log" 2^>^&1

if errorlevel 1 (echo   [WARN] World OS seeding failed - see logs\world_os_seed.log) else (echo   [WORLD OS] council world board seeded.)



rem -- EDGE_RESTORE_20260612: OPERATOR SIZE RESTAMP - runs AFTER prelaunch. --

rem prelaunch.py force-stamps LIVE_POSITION_SIZE_USD / POSITION_SIZE_USD back

rem to defaults every boot, silently overwriting the operator's launch choice.

rem Fix is two layers: (1) pin OPERATOR_LIVE_POSITION_SIZE_USD so the new

rem prelaunch override pass re-applies it on every future boot; (2) directly

rem restamp the keys the executor verifiably reads (execution_engine.py

rem _cfg_float: LIVE_POSITION_SIZE_USD -> POSITION_SIZE_USD) so the fix holds

rem even before the new prelaunch.py is deployed.

if not "!CFG_MODE!"=="live" goto :AFTER_LIVE_SIZE_RESTAMP

"%PY%" -c "import sqlite3; c=sqlite3.connect('sentinuity_matrix.db',timeout=10); v=str(float('!LIVE_SIZE!')); ks=['OPERATOR_LIVE_POSITION_SIZE_USD','LIVE_POSITION_SIZE_USD','LIVE_TRADE_AMOUNT_USD','LIVE_MAX_TOTAL_EXPOSURE_USD']; [c.execute('INSERT INTO system_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(k,v)) for k in ks]; c.commit(); print('OPERATOR_SIZE_RESTAMP_OK keys={} value={}'.format(ks,v)); c.close()" >> "%LOG_PATH%\operator_size_restamp.log" 2^>^&1

if errorlevel 1 (

  echo   [ERROR] Operator size restamp FAILED - live size may be the prelaunch default, NOT your choice.

  call :SHOW_LOG_TAIL "%LOG_PATH%\operator_size_restamp.log"

) else (

  echo   [CONFIG] Live size $!LIVE_SIZE! pinned AFTER prelaunch ^(OPERATOR_LIVE_POSITION_SIZE_USD + executor keys^).

)

:AFTER_LIVE_SIZE_RESTAMP

rem Full schema pass incl. startup_cleanup (the heavy one, kept here).

"%PY%" -c "from services.schema import init_db, ensure_hub_compat_schema, startup_cleanup; init_db(); ensure_hub_compat_schema(); startup_cleanup(); print('schema ready')" >> "%LOG_PATH%\schema_startup.log" 2^>^&1

if exist "!FORGE_SEED_PY!" "%PY%" "!FORGE_SEED_PY!" >> "%LOG_PATH%\forge_genesis_seed.log" 2^>^&1

"%PY%" -m services.smart_wallet_conviction migrate sentinuity_matrix.db >> "%LOG_PATH%\smart_wallet_conviction.log" 2^>^&1

if exist "!WALCK_PY!" "%PY%" "!WALCK_PY!" >> "%LOG_PATH%\wal_checkpoint.log" 2^>^&1



echo.

echo  Starting observer services after wallet/config reset...

start "PaperWalletRefresher" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.paper_wallet_refresher >> ""%LOG_PATH%\paper_wallet_refresher.log"" 2^>^&1"

start "SmartWalletTradeIngester" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.smart_wallet_trade_ingester >> ""%LOG_PATH%\smart_wallet_trade_ingester.log"" 2^>^&1"

REM GMGN roster intake is handled by services.wallet_scout using the real rank endpoint and optional operator CF clearance.


echo.

echo  Starting OpenClaw gateway and trading spine...

if "%START_GATEWAY%"=="1" (

  where openclaw >nul 2^>^&1

  if not errorlevel 1 start "Gateway" cmd /k "cd /d ""%ROOT_PATH%"" && openclaw gateway"

)



rem [SECURITY] Sentinel decision recorded in Section 3 of launch interview above.

if "!START_SENTINEL!"=="1" (

  set "SENTINEL_START_DELAY_SEC=90"

  set "SENTINEL_INTERVAL_SEC=120"

  start "OpenClaw Security Sentinel" /min cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.openclaw_security_sentinel >> ""%LOG_PATH%\openclaw_security_sentinel.log"" 2^>^&1"

)

if not "!START_SENTINEL!"=="1" echo   [SECURITY] Sentinel NOT started.



if "%START_CLOUDFLARE%"=="1" (

  if exist "C:\cloudflared\cloudflared.exe" start "Tunnel" /b cmd /c ""C:\cloudflared\cloudflared.exe" tunnel run sentinuity-uplink >> ""%LOG_PATH%\tunnel.log"" 2^>^&1"

)



start "PumpMon" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.pump_monitor >> ""%LOG_PATH%\pump_monitor.log"" 2^>^&1"

REM Authoritative MARKET_TIDE_STATE writer; distinct from pump_monitor.
start "MarketTide" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.pump_activity_monitor >> ""%LOG_PATH%\pump_activity_monitor.log"" 2^>^&1"
timeout /t 1 /nobreak >nul

start "Ingest" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.ingest_pipeline >> ""%LOG_PATH%\ingest_pipeline.log"" 2^>^&1"

start "MktIntel" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.market_intelligence >> ""%LOG_PATH%\market_intelligence.log"" 2^>^&1"

REM OPEN_BLOCKER_FIX_20260611: calibration writer was never launcher-owned; it died

REM Confidence harmonisation is performed in services.market_intelligence
REM when qualified rows are written. The former ConfidenceBackfill launch target
REM referenced a module absent from the signed codebase and produced a guaranteed
REM startup error, so it is intentionally not launched here.

start "WsOracle" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.ws_price_oracle >> ""%LOG_PATH%\ws_price_oracle.log"" 2^>^&1"

timeout /t 1 /nobreak >nul

start "Supervisor" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.neural_supervisor >> ""%LOG_PATH%\neural_supervisor.log"" 2^>^&1"

start "Executor" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.execution_engine >> ""%LOG_PATH%\execution_engine.log"" 2^>^&1"

start "Freshness" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.freshness_enforcer service >> ""%LOG_PATH%\freshness_enforcer.log"" 2^>^&1"
start "SignalGate" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.signal_gate_sensor --renew --interval 30 >> ""%LOG_PATH%\signal_gate_sensor.log"" 2^>^&1"

start "RollEvict" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.rolling_eviction >> ""%LOG_PATH%\rolling_eviction.log"" 2^>^&1"

start "CopytradeScanner" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.copytrade_shadow_scanner >> ""%LOG_PATH%\copytrade_shadow_scanner.log"" 2^>^&1"

start "SysGuardian" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.system_guardian >> ""%LOG_PATH%\system_guardian.log"" 2^>^&1"



start "ActivePipelineCleaner10m" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.active_pipeline_cleaner --loop --interval 600 --cutoff 600 >> ""%LOG_PATH%\active_pipeline_cleaner_loop.log"" 2^>^&1"

start "PriceEnricher" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.price_enricher >> ""%LOG_PATH%\price_enricher.log"" 2^>^&1"

start "PeriodicRefresh" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.periodic_refresh >> ""%LOG_PATH%\periodic_refresh.log"" 2^>^&1"

start "WinnerArchiver" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.winner_snapshot_archiver >> ""%LOG_PATH%\winner_snapshot_archiver.log"" 2^>^&1"

start "ShadowTracker" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.shadow_runner_tracker >> ""%LOG_PATH%\shadow_runner_tracker.log"" 2^>^&1"

start "MacroPriceFeed" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.macro_price_feed >> ""%LOG_PATH%\macro_price_feed.log"" 2^>^&1"

REM SIGNOFF_SUBSTRATE_PAPER_PIPELINE_20260718
REM Scanner creates PAPER research opportunities only; supervisor opens PAPER positions.
REM SUBSTRATE_LIVE_ENABLED remains 0 and no private-key execution path is introduced.
start "SubstrateScanner" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.substrate_opportunity_scanner >> ""%LOG_PATH%\substrate_opportunity_scanner.log"" 2^>^&1"
start "SubstrateSupervisor" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.substrate_portfolio_supervisor >> ""%LOG_PATH%\substrate_portfolio_supervisor.log"" 2^>^&1"
start "SubstrateCopyBridge" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.substrate_copytrade_bridge_loop >> ""%LOG_PATH%\substrate_copytrade_bridge_loop.log"" 2^>^&1"

start "SubstrateTrader" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.substrate_paper_trader >> ""%LOG_PATH%\substrate_paper_trader.log"" 2^>^&1"

start "MacroChannel" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.macro_channel >> ""%LOG_PATH%\macro_channel.log"" 2^>^&1"

start "WalletScout" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.wallet_scout >> ""%LOG_PATH%\wallet_scout.log"" 2^>^&1"

start "TelegramScout" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.telegram_scout >> ""%LOG_PATH%\telegram_scout.log"" 2^>^&1"

start "XScout" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.x_scout >> ""%LOG_PATH%\x_scout.log"" 2^>^&1"

start "SymbioticRouter" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.symbiotic_router >> ""%LOG_PATH%\symbiotic_router.log"" 2^>^&1"

start "Reconciler" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.reconciliation_engine >> ""%LOG_PATH%\reconciliation_engine.log"" 2^>^&1"

rem SIGNOFF_LIVE_LEDGER_20260716 - MANDATORY for any live/dual launch.

rem Re-resolves BUY_SUBMITTED/SELL_SUBMITTED/*_CONFIRMED_UNRESOLVED transactions

rem from chain truth after local timeouts or crashes. Inert (heartbeats DEGRADED)

rem in paper mode with no keypair. Started in the same phase as the reconciler,

rem after prelaunch schema init, so recovery never runs before schema exists.

start "SettleRecovery" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.live_settlement_recovery >> ""%LOG_PATH%\live_settlement_recovery.log"" 2^>^&1"

start "CouncilChamber" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.council_chamber_bridge >> ""%LOG_PATH%\council_chamber_bridge.log"" 2^>^&1"

start "CouncilBuild" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.council_build_orchestrator >> ""%LOG_PATH%\council_build_orchestrator.log"" 2^>^&1"

start "ForgeOrchestrator" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.intelligence_orchestrator >> ""%LOG_PATH%\intelligence_orchestrator.log"" 2^>^&1"

rem Research/debate spine; force HITL so no proposal can auto-apply.

"%PY%" -c "import sqlite3,time; c=sqlite3.connect('sentinuity_matrix.db'); c.execute("INSERT INTO system_config(key,value,updated_at) VALUES('HITL_REQUIRED','1',?) ON CONFLICT(key) DO UPDATE SET value='1',updated_at=excluded.updated_at",(time.time(),)); c.commit(); c.close()" >> "%LOG_PATH%\hitl_guard.log" 2>&1

start "ForgeResearch" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.forge_research_bridge >> ""%LOG_PATH%\forge_research_bridge.log"" 2^>^&1"

start "Debate" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.debate_engine >> ""%LOG_PATH%\debate_engine.log"" 2^>^&1"

start "ForgeWriter" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.forge_code_writer >> ""%LOG_PATH%\forge_code_writer.log"" 2^>^&1"

start "GithubScout" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.github_scout >> ""%LOG_PATH%\github_scout.log"" 2^>^&1"



start "SovGovernor" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.sovereign_governor >> ""%LOG_PATH%\sovereign_governor.log"" 2^>^&1"

start "Polaris" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.polaris >> ""%LOG_PATH%\polaris.log"" 2^>^&1"

start "SPE" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.sovereign_parameter_engine >> ""%LOG_PATH%\sovereign_parameter_engine.log"" 2^>^&1"

start "Replay" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.replay_engine >> ""%LOG_PATH%\replay_engine.log"" 2^>^&1"

start "Vault" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.code_vault service >> ""%LOG_PATH%\code_vault.log"" 2^>^&1"

if exist auto_sweep.py start "AutoSweep" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" auto_sweep.py >> ""%LOG_PATH%\auto_sweep.log"" 2^>^&1"



start "SENTINUITY SOVEREIGN CONSOLE" cmd /k "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.master_console"

start "SENTINUITY GUARDIAN" cmd /k "cd /d ""%ROOT_PATH%"" && call launch\Watchdog_Sentinuity.bat"

start "SENTINUITY WATCH" cmd /k "cd /d ""%ROOT_PATH%"" && call launch\Sentinuity_Watch.bat"



echo.

echo  Waiting 8 seconds for first heartbeats...

timeout /t 8 /nobreak >nul

if exist tools\check_launch_health.py "%PY%" tools\check_launch_health.py



echo.

echo  =========================================================

echo   SENTINUITY ONLINE

echo  =========================================================

echo  -- SOLANA PUMP.FUN LANE --------------------------------

if "!CFG_MODE!"=="live" (

  echo   Mode:       DUAL MODE ^(paper + live Mode B gate^)

  echo   Paper size: $!PAPER_SIZE! per trade

  echo   Live size:  $!LIVE_SIZE! per trade ^(operator-chosen, pinned^)

  echo   Stamped:    OPERATOR_LIVE_POSITION_SIZE_USD, LIVE_POSITION_SIZE_USD, LIVE_MAX_TOTAL_EXPOSURE_USD = !LIVE_SIZE!

) else (

  echo   Mode:       PAPER ONLY

  echo   Paper size: $!PAPER_SIZE! per trade, max !PAPER_MAX! open

)

echo.

echo  -- SUBSTRATE DESK ^(ALTS + NATIVES^) --------------------

echo   Mode:        !SUBSTRATE_MODE!

echo   Paper bal:   $!SUBSTRATE_PAPER_USD! starting balance

echo   Position:    $!SUBSTRATE_POS_SIZE! per trade, max 3 open

echo   Assets:      BTC / ETH / SOL / XRP / SUI / BNB

echo   Filter:      Hybrid algo ^(BB+RSI+Breakout^) + council conviction

echo   Dashboard:  http://localhost:8501  ^(started early^)

echo   Logs:       %LOG_PATH%\

if "!START_SENTINEL!"=="1" (echo   Sentinel:   ON ^(operator-confirmed this launch^)) else (echo   Sentinel:   OFF ^(operator-declined this launch^))

echo   Momentum:   gate OFF / shadow-only ^(verified below from live DB^)

echo   Builder:    council_build_tasks seeded unless BUILD_DISABLED=1 ^(see seed_build_tasks.log^)

echo   World OS:   ENABLED - writes confined to council_world_tasks + world_command_log

echo   World route: http://localhost:8501/?sec=worldos

echo   Pocket:     gateway via services\pocket_command_router.py ^(requires POCKET_TOKEN; live-risk hard-refused^)

echo.

echo   Calibration status:

echo     SUPERVISOR_CALIBRATION_GATES_ENTRY = 0 ^(observe only^)

echo     Conf floor: meta-learning loop owns SUPERVISOR_MIN_MINT_CONFIDENCE at runtime ^(EDGE_RESTORE_20260612 - prelaunch no longer wipes it^)

echo     STOP_LOSS_PCT / HARD_STOP_LOSS_PCT = 4.0

echo.

echo   OpenClaw gateway: started if openclaw is installed and on PATH.

echo  =========================================================

echo.

echo  ---------------------------------------------------------

echo   EDGE VERIFICATION (live DB values AFTER all launch scripts)

echo  ---------------------------------------------------------

"%PY%" -c "import sqlite3; c=sqlite3.connect('sentinuity_matrix.db',timeout=10); g=lambda k:(lambda r:r[0] if r else '(missing)')(c.execute('SELECT value FROM system_config WHERE key=?',(k,)).fetchone()); ks=['MOMENTUM_GATE_ENABLED','MIN_PRICE_MOMENTUM_5M','SUPERVISOR_REQUIRE_POSITIVE_MTM','EXECUTOR_MAX_SIGNAL_AGE_SEC','SUPERVISOR_MAX_SIGNAL_AGE_SEC','STOP_LOSS_PCT','HARD_STOP_LOSS_PCT']; [print('   {:34s} = {}'.format(k,g(k))) for k in ks]; gate=g('MOMENTUM_GATE_ENABLED'); print('   >>> EDGE OK: momentum gate is OFF (0)' if str(gate)=='0' else '   >>> WARNING: MOMENTUM_GATE_ENABLED='+str(gate)+' - something re-stamped it ON. Late entries will return.'); c.close()"

echo  ---------------------------------------------------------

echo.

pause

exit /b 0



:SHOW_LOG_TAIL

if "%~1"=="" exit /b 0

if not exist "%~1" (

  echo  [WARN] Missing log: %~1

  exit /b 0

)

if defined PS_EXE (

  set "SENTINUITY_TAIL_FILE=%~1"

  "%PS_EXE%" -NoProfile -ExecutionPolicy Bypass -Command "Get-Content -LiteralPath $env:SENTINUITY_TAIL_FILE -Tail 80 -ErrorAction SilentlyContinue"

  set "SENTINUITY_TAIL_FILE="

) else (

  type "%~1"

)

exit /b 0



::BEGIN_SOLANA_WALLET_RESET_PY

import datetime

import sqlite3

import sys



amount = float(sys.argv[1])

now = datetime.datetime.now().isoformat(sep=" ", timespec="seconds")

conn = sqlite3.connect("sentinuity_matrix.db", timeout=20)

cur = conn.cursor()

changed = []





def qident(name: str) -> str:

    return '"' + str(name).replace('"', '""') + '"'





def table_cols(table: str) -> list[str]:

    return [row[1] for row in cur.execute(f"PRAGMA table_info({qident(table)})")]





def ensure_col(table: str, col: str, col_type: str) -> None:

    if col not in table_cols(table):

        cur.execute(f"ALTER TABLE {qident(table)} ADD COLUMN {qident(col)} {col_type}")





# Canonical console/state table.

cur.execute("CREATE TABLE IF NOT EXISTS system_state(id INTEGER PRIMARY KEY)")

for col, col_type in [

    ("wallet_balance", "REAL DEFAULT 0"),

    ("initial_capital", "REAL DEFAULT 0"),

    ("paper_equity", "REAL DEFAULT 0"),

    ("paper_cash", "REAL DEFAULT 0"),

    ("paper_reserved", "REAL DEFAULT 0"),

    ("paper_realized_pnl", "REAL DEFAULT 0"),

    ("paper_unrealized_pnl", "REAL DEFAULT 0"),

    ("updated_at", "TEXT"),

]:

    ensure_col("system_state", col, col_type)

cur.execute("INSERT OR IGNORE INTO system_state(id) VALUES(1)")

cur.execute(

    """

    UPDATE system_state

       SET wallet_balance=?, initial_capital=?, paper_equity=?, paper_cash=?,

           paper_reserved=0, paper_realized_pnl=0, paper_unrealized_pnl=0,

           updated_at=?

     WHERE id=1

    """,

    (amount, amount, amount, amount, now),

)

changed.append("system_state")



# Config aliases used by UI/refresher variants.

cur.execute("CREATE TABLE IF NOT EXISTS system_config(key TEXT PRIMARY KEY, value TEXT)")

amount_keys = """

PAPER_WALLET_BALANCE_USD PAPER_WALLET_EQUITY_USD PAPER_WALLET_CASH_USD

PAPER_INITIAL_CAPITAL_USD PAPER_STARTING_BALANCE_USD PAPER_EQUITY_BASELINE_USD

PAPER_TRADING_BALANCE_USD SOLANA_PAPER_WALLET_BALANCE_USD

SOLANA_PAPER_WALLET_EQUITY_USD SOLANA_PAPER_BALANCE_USD SOLANA_PAPER_CASH_USD

SOLANA_PAPER_INITIAL_CAPITAL_USD SOLANA_PAPER_STARTING_BALANCE_USD

PUMP_PAPER_WALLET_BALANCE_USD PUMP_PAPER_EQUITY_USD PAPER_EQUITY_USD

""".split()

zero_keys = """

PAPER_WALLET_RESERVED_USD PAPER_RESERVED_USD SOLANA_PAPER_RESERVED_USD

PAPER_REALIZED_PNL_USD PAPER_UNREALIZED_PNL_USD SOLANA_PAPER_REALIZED_PNL_USD

SOLANA_PAPER_UNREALIZED_PNL_USD

""".split()

for key in amount_keys:

    cur.execute(

        "INSERT INTO system_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",

        (key, f"{amount:.2f}"),

    )

for key in zero_keys:

    cur.execute(

        "INSERT INTO system_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",

        (key, "0"),

    )

for key in ["SOLANA_PAPER_WALLET_RESET_AT", "PAPER_WALLET_RESET_AT"]:

    cur.execute(

        "INSERT INTO system_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",

        (key, now),

    )

changed.append("system_config")



# Best-effort safe paper wallet/account tables. Never touch live/key/private tables.

amount_cols = set(

    "wallet_balance paper_wallet_balance paper_wallet_balance_usd balance balance_usd "

    "paper_balance paper_balance_usd cash cash_usd paper_cash paper_cash_usd "

    "equity equity_usd paper_equity paper_equity_usd current_equity current_equity_usd "

    "current_balance current_balance_usd initial_capital initial_capital_usd "

    "paper_initial_capital paper_initial_capital_usd starting_balance starting_balance_usd "

    "start_balance start_balance_usd".split()

)

zero_cols = set(

    "reserved reserved_usd paper_reserved paper_reserved_usd realized_pnl realized_pnl_usd "

    "paper_realized_pnl paper_realized_pnl_usd unrealized_pnl unrealized_pnl_usd "

    "paper_unrealized_pnl paper_unrealized_pnl_usd pnl pnl_usd profit_loss profit_loss_usd".split()

)

stamp_cols = set("updated_at last_updated modified_at reset_at paper_wallet_reset_at".split())



all_tables = [row[0] for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")]

for table in all_tables:

    low_table = table.lower()

    if table == "system_state":

        continue

    if any(block in low_table for block in ["smart_wallet", "wallet_trade", "live", "key", "secret", "private"]):

        continue

    is_safe_paper_table = (

        "paper" in low_table and any(x in low_table for x in ["wallet", "equity", "account", "state", "balance"])

    ) or low_table in {"paper_wallet", "paper_wallet_state", "paper_account", "paper_state"}

    if not is_safe_paper_table:

        continue



    sets = []

    params = []

    for col in table_cols(table):

        low_col = col.lower()

        if low_col in amount_cols:

            sets.append(f"{qident(col)}=?")

            params.append(amount)

        elif low_col in zero_cols:

            sets.append(f"{qident(col)}=?")

            params.append(0.0)

        elif low_col in stamp_cols:

            sets.append(f"{qident(col)}=?")

            params.append(now)

    if sets:

        try:

            cur.execute(f"UPDATE {qident(table)} SET " + ", ".join(sets), params)

            changed.append(table)

        except Exception as exc:

            changed.append(f"{table}:SKIP:{exc.__class__.__name__}")



conn.commit()

print(f"SOLANA_PAPER_WALLET_RESET_OK amount={amount:.2f} updated={','.join(changed)}")

print("If console was already open, close/reopen it so it rereads the reset balance.")

conn.close()

::END_SOLANA_WALLET_RESET_PY
start "CouncilAutobuilder" /b cmd /c "cd /d ""%ROOT_PATH%"" && ""%PY%"" -m services.council_autobuilder >> ""%LOG_PATH%\council_autobuilder.log"" 2>&1"

