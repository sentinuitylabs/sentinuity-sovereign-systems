"""
service_watchdog.py v4 — FORMALLY ARCHIVED (FABLE5 DIRECTIVE ITEM 6)
====================================================================
ARCHIVED: services.system_guardian (launched via launch/Watchdog_Sentinuity.bat)
is the ONLY restart authority. This module restarts services WITHOUT the
service_heartbeats lease protocol and must never run alongside the guardian.

Running this module now exits immediately with an error. The legacy
implementation is preserved below, unreachable, for reference only.
To run it anyway (never in production), set SERVICE_WATCHDOG_ARCHIVED_OVERRIDE=1.
"""
import os as _os, sys as _sys

_ARCHIVED = True

def _refuse_archived() -> None:
    if _os.getenv("SERVICE_WATCHDOG_ARCHIVED_OVERRIDE", "0") != "1":
        _sys.stderr.write(
            "[ARCHIVED] services.service_watchdog is retired. "
            "services.system_guardian (launch/Watchdog_Sentinuity.bat) is the sole "
            "restart authority. Refusing to start.\n"
        )
        raise SystemExit(2)

import sqlite3, time, subprocess, os, logging, sys

logging.basicConfig(level=logging.INFO, format='%(asctime)s [WATCHDOG2] %(message)s', handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger('watchdog2')

ROOT = os.path.dirname(os.path.abspath(__file__))
DB   = os.path.join(ROOT, 'sentinuity_matrix.db')
LOGS = os.path.join(ROOT, 'logs')

SERVICES = {
    # ── INGEST ORIGIN — must be alive for any trading ──
    'pump_monitor':             {'module': 'services.pump_monitor',             'stale_sec': 60,  'critical': True},
    'ingest_pipeline':          {'module': 'services.ingest_pipeline',          'stale_sec': 120, 'critical': True},
    'signal_engine':            {'module': 'services.signal_engine',            'stale_sec': 120, 'critical': True},
    # ── QUALIFICATION + EXECUTION ──
    'market_intelligence':      {'module': 'services.market_intelligence',      'stale_sec': 120, 'critical': True},
    'neural_supervisor':        {'module': 'services.neural_supervisor',        'stale_sec': 120, 'critical': True},
    'execution_engine':         {'module': 'services.execution_engine',         'stale_sec': 180, 'critical': True},
    'ws_price_oracle':          {'module': 'services.ws_price_oracle',          'stale_sec': 120, 'critical': True},
    # ── GOVERNANCE ──
    'sovereign_governor':       {'module': 'services.sovereign_governor',       'stale_sec': 300, 'critical': False},
    # ── ENRICHMENT ──
    'price_enricher':           {'module': 'services.price_enricher',           'stale_sec': 300, 'critical': False},
    # ── BUILD ──
    'intelligence_orchestrator':{'module': 'services.intelligence_orchestrator','stale_sec': 300, 'critical': False},
    'forge_code_writer':        {'module': 'services.forge_code_writer',        'stale_sec': 300, 'critical': False},
    'forge_research_bridge':    {'module': 'services.forge_research_bridge',    'stale_sec': 300, 'critical': False},
}

_last_restart: dict = {}

def get_pulse(name):
    try:
        c = sqlite3.connect(DB, timeout=3)
        r = c.execute("SELECT last_pulse FROM system_heartbeat WHERE service_name=?", (name,)).fetchone()
        c.close()
        return float(r[0]) if r and r[0] else 0.0
    except Exception:
        return 0.0

def restart_service(name, module):
    now = time.time()
    if now - _last_restart.get(name, 0) < 90:
        return False
    log_file = os.path.join(LOGS, f'{name}.log')
    cmd = f'cd /d "{ROOT}" && python -m {module} >> "{log_file}" 2>&1'
    try:
        subprocess.Popen(['cmd', '/c', cmd], creationflags=subprocess.CREATE_NEW_CONSOLE, cwd=ROOT)
        _last_restart[name] = now
        log.info('RESTARTED %s', name)
        return True
    except Exception as e:
        log.error('Failed to restart %s: %s', name, e)
        return False

def clear_stuck_pipeline():
    try:
        now = time.time()
        c = sqlite3.connect(DB, timeout=3)
        stuck = c.execute(
            "UPDATE market_snapshots SET candidate_state='pending', claim_until=0 "
            "WHERE candidate_state='processing' AND COALESCE(price_updated_at,created_at,timestamp,0) < ?",
            (now-120,)
        ).rowcount
        if stuck > 0:
            c.commit()
            log.info('Cleared %d stuck processing rows', stuck)
        c.close()
    except Exception as e:
        log.debug('Pipeline clear error: %s', e)

def main():
    os.makedirs(LOGS, exist_ok=True)
    log.info('Service watchdog v4 — monitoring %d services', len(SERVICES))
    log.info('Critical: pump_monitor, ingest_pipeline, signal_engine, market_intelligence, neural_supervisor, execution_engine, ws_price_oracle')
    last_pipeline_clear = 0

    while True:
        try:
            now = time.time()
            for name, cfg in SERVICES.items():
                pulse = get_pulse(name)
                age = now - pulse if pulse > 0 else 9999
                if age > cfg['stale_sec']:
                    sev = 'CRITICAL' if cfg['critical'] else 'INFO'
                    log.warning('[%s] %s stale %.0fs — restarting', sev, name, age)
                    restart_service(name, cfg['module'])

            if now - last_pipeline_clear > 300:
                clear_stuck_pipeline()
                last_pipeline_clear = now

        except Exception as e:
            log.error('Watchdog loop error: %s', e)
        time.sleep(30)

if __name__ == '__main__':
    _refuse_archived()
    main()
