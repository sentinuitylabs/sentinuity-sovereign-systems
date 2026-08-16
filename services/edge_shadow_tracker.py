#!/usr/bin/env python3
"""
SENTINUITY — EDGE SHADOW TRACKER (read-only outcome observation)

Why this exists
---------------
If you only measure the candidates that were admitted, you cannot tell whether
the confidence floor is selecting winners or merely selecting. The rejected
population is the control group, and without it every confidence-vs-outcome
number is selection-biased and unfalsifiable.

This service observes the forward price trajectory of BOTH admitted and
rejected candidates and records peak / adverse / terminal outcomes.

Hard contracts (enforced by tests):
  * Writes to exactly one table: edge_confidence_ledger (shadow_* columns).
  * Never inserts, updates or deletes any row in paper_positions, positions,
    market_snapshots, or any trading/capital table.
  * Never consumes position capacity and never creates a proposal or latch.
  * Records a HYPOTHETICAL entry at the canonical evaluation price only.
  * Distinguishes "price unavailable" from "price fell". A sample with
    insufficient coverage is marked incomplete, not marked a loss.

Provider load
-------------
Prefers prices already collected by the oracle (mtm_ticks). Only mints with no
oracle coverage are fetched directly, batched 30-per-request against
DexScreener, at a conservative cadence, on a budget entirely separate from the
execution path. Set EDGE_SHADOW_FETCH=0 to disable outbound fetching entirely
(shadow coverage then depends on oracle overlap alone).
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from services.edge_ledger import (  # noqa: E402
    LEDGER_TABLE, ensure_schema, _connect as _ledger_connect,
)
from core.schema import update_heartbeat  # noqa: E402

SERVICE = "edge_shadow_tracker"

POLL_SEC = float(os.environ.get("EDGE_SHADOW_POLL_SEC", "20"))
OBSERVE_SEC = float(os.environ.get("EDGE_SHADOW_OBSERVE_SEC", "1800"))   # 30 min
FETCH_ENABLED = os.environ.get("EDGE_SHADOW_FETCH", "1").strip() != "0"
FETCH_BATCH = 30
FETCH_MIN_INTERVAL = float(os.environ.get("EDGE_SHADOW_FETCH_INTERVAL", "6"))
MAX_BATCHES_PER_PASS = int(os.environ.get("EDGE_SHADOW_MAX_BATCHES", "4"))

# A sample is usable for calibration only if we saw at least this many ticks
# spread over at least this fraction of the observation window.
MIN_TICKS_FOR_COMPLETE = int(os.environ.get("EDGE_SHADOW_MIN_TICKS", "8"))
MIN_SPAN_FRACTION = float(os.environ.get("EDGE_SHADOW_MIN_SPAN_FRAC", "0.5"))

INTEL_DB = os.environ.get("SENTINUITY_INTEL_DB", "sentinuity_intelligence.db")

_last_fetch = 0.0


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] [{SERVICE}] {msg}", flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# Price sources (read-only)
# ─────────────────────────────────────────────────────────────────────────────
def _oracle_prices(mints: List[str], since: float) -> Dict[str, Tuple[float, float]]:
    """
    Latest oracle tick per mint from mtm_ticks. Read-only, separate DB.
    Returns {mint: (price_usd, ts_epoch_seconds)}.
    """
    out: Dict[str, Tuple[float, float]] = {}
    if not mints:
        return out
    try:
        con = sqlite3.connect(f"file:{INTEL_DB}?mode=ro", uri=True, timeout=4.0)
    except Exception:
        return out
    try:
        con.row_factory = sqlite3.Row
        exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='mtm_ticks'"
        ).fetchone()
        if not exists:
            return out
        for i in range(0, len(mints), 200):
            chunk = mints[i:i + 200]
            qs = ",".join("?" for _ in chunk)
            rows = con.execute(
                f"""SELECT mint_address, price_usd, MAX(ts_ms) ts
                    FROM mtm_ticks
                    WHERE mint_address IN ({qs}) AND ts_ms >= ?
                    GROUP BY mint_address""",
                (*chunk, int(since * 1000)),
            ).fetchall()
            for r in rows:
                p = r["price_usd"]
                if p and float(p) > 0:
                    out[str(r["mint_address"])] = (float(p), float(r["ts"]) / 1000.0)
    except Exception:
        pass
    finally:
        try:
            con.close()
        except Exception:
            pass
    return out


def _fetch_prices(mints: List[str]) -> Dict[str, Tuple[float, float]]:
    """Batched DexScreener lookup for mints with no oracle coverage."""
    global _last_fetch
    out: Dict[str, Tuple[float, float]] = {}
    if not FETCH_ENABLED or not mints:
        return out
    try:
        import requests  # noqa: F401
    except Exception:
        return out

    batches = 0
    for i in range(0, len(mints), FETCH_BATCH):
        if batches >= MAX_BATCHES_PER_PASS:
            break
        wait = FETCH_MIN_INTERVAL - (time.time() - _last_fetch)
        if wait > 0:
            time.sleep(wait)
        chunk = mints[i:i + FETCH_BATCH]
        try:
            import requests
            resp = requests.get(
                "https://api.dexscreener.com/latest/dex/tokens/" + ",".join(chunk),
                timeout=10,
                headers={"User-Agent": "sentinuity-shadow/1.0"},
            )
            _last_fetch = time.time()
            batches += 1
            if resp.status_code != 200:
                continue
            data = resp.json() or {}
            now = time.time()
            for pair in (data.get("pairs") or []):
                base = ((pair or {}).get("baseToken") or {}).get("address")
                pu = (pair or {}).get("priceUsd")
                if not base or not pu:
                    continue
                try:
                    p = float(pu)
                except (TypeError, ValueError):
                    continue
                if p <= 0:
                    continue
                # Keep the deepest pair per mint.
                liq = float(((pair or {}).get("liquidity") or {}).get("usd") or 0)
                prev = out.get(base)
                if prev is None or liq > prev[1]:
                    out[base] = (p, liq)
            out = {k: (v[0], now) for k, v in out.items()}
        except Exception:
            _last_fetch = time.time()
            batches += 1
            continue
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Shadow update pass
# ─────────────────────────────────────────────────────────────────────────────
def _active_rows(con: sqlite3.Connection, now: float) -> List[sqlite3.Row]:
    return con.execute(
        f"""SELECT id, mint_address, shadow_ref_price, shadow_ref_at,
                   shadow_peak_price, shadow_trough_price, shadow_tick_count,
                   evaluated_at
            FROM {LEDGER_TABLE}
            WHERE shadow_state IN ('pending','tracking')
              AND evaluated_at >= ?
            ORDER BY evaluated_at DESC
            LIMIT 800""",
        (now - OBSERVE_SEC - 300,),
    ).fetchall()


def _finalise(con: sqlite3.Connection, now: float) -> int:
    """Close out rows whose observation window has elapsed."""
    rows = con.execute(
        f"""SELECT id, shadow_ref_price, shadow_peak_price, shadow_trough_price,
                   shadow_last_price, shadow_tick_count, shadow_ref_at,
                   shadow_last_at
            FROM {LEDGER_TABLE}
            WHERE shadow_state IN ('pending','tracking')
              AND evaluated_at < ?""",
        (now - OBSERVE_SEC,),
    ).fetchall()
    n = 0
    for r in rows:
        ref = r["shadow_ref_price"]
        ticks = int(r["shadow_tick_count"] or 0)
        span = float(r["shadow_last_at"] or 0) - float(r["shadow_ref_at"] or 0)
        completeness = 0.0
        if OBSERVE_SEC > 0:
            completeness = max(0.0, min(1.0, span / OBSERVE_SEC))

        complete = (
            ref is not None and float(ref) > 0
            and ticks >= MIN_TICKS_FOR_COMPLETE
            and completeness >= MIN_SPAN_FRACTION
        )

        if not complete:
            # Insufficient observation. This is NOT a loss -- it is an
            # unmeasurable sample and must be excluded from calibration.
            con.execute(
                f"""UPDATE {LEDGER_TABLE}
                    SET shadow_state='incomplete', shadow_complete=0,
                        price_completeness=?, mtm_health='insufficient_coverage'
                    WHERE id=?""",
                (completeness, r["id"]),
            )
            n += 1
            continue

        ref_f = float(ref)
        peak = float(r["shadow_peak_price"] or ref_f)
        trough = float(r["shadow_trough_price"] or ref_f)
        last = float(r["shadow_last_price"] or ref_f)
        peak_pct = (peak - ref_f) / ref_f * 100.0
        adv_pct = (trough - ref_f) / ref_f * 100.0
        real_pct = (last - ref_f) / ref_f * 100.0
        con.execute(
            f"""UPDATE {LEDGER_TABLE}
                SET shadow_state='complete', shadow_complete=1,
                    price_completeness=?, mtm_health='ok',
                    peak_return_pct=?, adverse_return_pct=?, realised_return_pct=?,
                    hold_seconds=?,
                    runner_10=?, runner_25=?, runner_50=?, runner_100=?
                WHERE id=?""",
            (completeness, peak_pct, adv_pct, real_pct, span,
             1 if peak_pct >= 10 else 0,
             1 if peak_pct >= 25 else 0,
             1 if peak_pct >= 50 else 0,
             1 if peak_pct >= 100 else 0,
             r["id"]),
        )
        n += 1
    if n:
        con.commit()
    return n


def run_pass() -> Dict[str, Any]:
    now = time.time()
    stats = {"tracked": 0, "updated": 0, "finalised": 0, "oracle": 0, "fetched": 0}
    con = _ledger_connect()
    try:
        ensure_schema(con)
        rows = _active_rows(con, now)
        stats["tracked"] = len(rows)
        if not rows:
            stats["finalised"] = _finalise(con, now)
            return stats

        by_mint: Dict[str, List[sqlite3.Row]] = {}
        for r in rows:
            by_mint.setdefault(str(r["mint_address"]), []).append(r)
        mints = list(by_mint.keys())

        prices = _oracle_prices(mints, now - OBSERVE_SEC - 300)
        stats["oracle"] = len(prices)
        missing = [m for m in mints if m not in prices]
        if missing:
            fetched = _fetch_prices(missing)
            stats["fetched"] = len(fetched)
            prices.update(fetched)

        for mint, rs in by_mint.items():
            got = prices.get(mint)
            if not got:
                continue
            price, ts = got
            if price <= 0:
                continue
            for r in rs:
                ref = r["shadow_ref_price"]
                if ref is None or float(ref or 0) <= 0:
                    # Establish the hypothetical entry reference on first sight.
                    con.execute(
                        f"""UPDATE {LEDGER_TABLE}
                            SET shadow_ref_price=?, shadow_ref_at=?,
                                shadow_last_price=?, shadow_last_at=?,
                                shadow_peak_price=?, shadow_trough_price=?,
                                shadow_tick_count=1, shadow_state='tracking',
                                shadow_tracked=1
                            WHERE id=?""",
                        (price, ts, price, ts, price, price, r["id"]),
                    )
                else:
                    peak = max(float(r["shadow_peak_price"] or price), price)
                    trough = min(float(r["shadow_trough_price"] or price), price)
                    con.execute(
                        f"""UPDATE {LEDGER_TABLE}
                            SET shadow_last_price=?, shadow_last_at=?,
                                shadow_peak_price=?, shadow_peak_at=
                                    CASE WHEN ? > COALESCE(shadow_peak_price,0)
                                         THEN ? ELSE shadow_peak_at END,
                                shadow_trough_price=?,
                                shadow_tick_count=COALESCE(shadow_tick_count,0)+1,
                                shadow_state='tracking', shadow_tracked=1
                            WHERE id=?""",
                        (price, ts, peak, price, ts, trough, r["id"]),
                    )
                stats["updated"] += 1
        con.commit()
        stats["finalised"] = _finalise(con, now)
    finally:
        try:
            con.close()
        except Exception:
            pass
    return stats


def main() -> None:
    _log(f"online db={os.environ.get('SENTINUITY_DB','sentinuity_matrix.db')} "
         f"observe={OBSERVE_SEC:.0f}s fetch={'on' if FETCH_ENABLED else 'off'}")
    ensure_schema()
    try:
        update_heartbeat(SERVICE, "starting", "edge shadow tracker online")
    except Exception:
        pass
    while True:
        try:
            s = run_pass()
            note = (f"tracked={s['tracked']} updated={s['updated']} "
                    f"final={s['finalised']} oracle={s['oracle']} fetch={s['fetched']}")
            if s["tracked"] or s["finalised"]:
                _log(note)
            try:
                update_heartbeat(SERVICE, "alive", note, work_processed=int(s["updated"] + s["finalised"]))
            except Exception:
                pass
        except KeyboardInterrupt:
            _log("shutdown")
            return
        except Exception as e:
            _log(f"pass error: {type(e).__name__}: {e}")
            try:
                update_heartbeat(SERVICE, "error", str(e)[:120])
            except Exception:
                pass
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    if "--once" in sys.argv:
        print(json.dumps(run_pass(), indent=2))
    else:
        main()
