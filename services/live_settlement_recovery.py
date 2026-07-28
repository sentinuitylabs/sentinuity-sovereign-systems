"""
live_settlement_recovery.py
───────────────────────────────────────────────────────────────────────────────
SENTINUITY LIVE SETTLEMENT RECOVERY SERVICE  (SIGNOFF_LIVE_LEDGER_20260716)

Closes the four capital-loss windows proven by the NTO regression audit:

  1. BUY confirms after the local confirmation timeout        (directive test 2)
  2. SELL confirms after the local confirmation timeout       (directive test 3)
  3. Process crash during BUY_SUBMITTED                       (directive test 4)
  4. Process crash during SELL_SUBMITTED                      (directive test 5)

Before this service existed, a transaction signature only lived in the
in-memory result dict of execute_live_buy/execute_live_sell. A crash or local
timeout orphaned the on-chain fill: real tokens (or real SOL) moved on chain
with no corresponding database truth. live_trading.py now persists every
signature into live_tx_ledger at submit time; this service re-resolves those
signatures from chain truth.

DESIGN CONTRACT:
  - IDEMPOTENT. Every transition is guarded by the current state; running the
    pass twice — or receiving a duplicate reconciliation callback (test 6) —
    cannot duplicate a fill, a position, or a settlement.
  - RESTART-SAFE. All state lives in the database; nothing is held in memory.
  - REAL-ONLY. SIM rows are never read or written (test 16).
  - NO THEORETICAL FALLBACK. Positions are only advanced by confirmed chain
    truth (blockTime + resolved wallet deltas). Never by marks.
  - FAIL TOWARD MANUAL_INTERVENTION. Contradictory or stale-unresolved states
    escalate to live_state='MANUAL_INTERVENTION' + CRITICAL heartbeat instead
    of being guessed at.
  - OUT OF THE SCANNING THREAD. Runs as its own process (directive Part 2:
    confirmation and reconciliation outside the main scanning thread).

STATE TRANSITIONS PERFORMED (all keyed on chain truth):

  ledger BUY  SUBMITTED            + chain err        → FAILED_ON_CHAIN (no position)
  ledger BUY  SUBMITTED/CONF_UNRES + chain fill       → RESOLVED + OPEN_REAL row created
                                                        (only if no row already holds
                                                        that buy_tx_sig — idempotent)
  ledger SELL SUBMITTED            + chain err        → FAILED_ON_CHAIN; position
                                                        reverted to OPEN_REAL (tokens
                                                        were never sold; exit can retrigger)
  ledger SELL SUBMITTED/CONF_UNRES + chain fill       → RESOLVED + position SETTLED with
                                                        settlement PnL overwritten from
                                                        confirmed SOL deltas
  any unresolved state older than LIVE_RECOVERY_MANUAL_AFTER_SEC
                                                      → MANUAL_INTERVENTION + CRITICAL

DEPLOY:
  services/live_settlement_recovery.py
  Launch alongside the reconciler:
    start "" "SettlementRecovery" python services\\live_settlement_recovery.py
"""

from __future__ import annotations

import json
import logging
import os
import time
from decimal import Decimal
from typing import Any, Optional

log = logging.getLogger("live_settlement_recovery")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SETTLE_RECOVERY] %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

SERVICE_NAME = "live_settlement_recovery"
LOCK_KEY = "LIVE_RECOVERY_LOCK"
LOCK_TTL_SEC = 120.0
CYCLE_SEC = float(os.getenv("LIVE_RECOVERY_CYCLE_SEC", "20"))
# Give the chain a moment before first re-resolution attempt.
MIN_AGE_BEFORE_RECOVERY_SEC = float(os.getenv("LIVE_RECOVERY_MIN_AGE_SEC", "45"))
# After this long unresolved, stop retrying silently and demand the operator.
MANUAL_AFTER_SEC = float(os.getenv("LIVE_RECOVERY_MANUAL_AFTER_SEC", "900"))


# ── DB / heartbeat ────────────────────────────────────────────────────────────

def _get_conn():
    from core.schema import get_connection
    return get_connection()


def _heartbeat(status: str, note: str, work: int = 0) -> None:
    """
    SCHEMA AUDIT FINDING (SIGNOFF_LIVE_LEDGER_20260716):
    system_heartbeat has NO `last_seen_at` column. The real schema is
    (service_name, status, note, last_pulse, work_processed, last_success_at,
    restart_claimed_until) — core/schema.py:457. Writing `last_seen_at` raises
    "no such column" inside a swallowed try/except, so the service silently
    NEVER heartbeats and is invisible in Diagnostics. core.schema.update_heartbeat
    is the canonical column-safe writer; it is used here instead of hand-rolled
    SQL so this service can never drift from the live schema again.
    NOTE: services/reconciliation_engine.py:70 and services/symbiotic_router.py:542
    still carry this exact bug — reported in the manifest, not fixed here.
    """
    try:
        from core.schema import update_heartbeat
        update_heartbeat(SERVICE_NAME, status, note[:200],
                         work_processed=work, last_success_at=time.time())
    except Exception:
        pass


def _retry_on_lock(fn, *, attempts: int = 5, base_delay: float = 0.4):
    """
    SQLite lock backoff. core.schema.get_connection already sets
    busy_timeout=5000, but a WAL writer contending with the executor can still
    surface 'database is locked'. Recovery must yield to the executor rather
    than crash: it retries, then defers the row to the next cycle. Deferring is
    always safe — the ledger row persists and the pass is idempotent.
    """
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:
            last = exc
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            time.sleep(min(base_delay * (2 ** i), 4.0))
    raise last  # type: ignore[misc]


def _acquire_singleton_lock() -> bool:
    """
    Advisory single-instance lock in system_config, TTL-bounded so a crashed
    holder cannot wedge recovery forever.

    Two recovery processes cannot double-handle rows even WITHOUT this lock —
    every transition is guarded on the prior state (see _ledger_set_state and
    the state-guarded UPDATE ... WHERE live_state IN (...) clauses), so a losing
    racer matches zero rows and writes nothing. This lock is therefore defence
    in depth: it prevents duplicate RPC spend and duplicate log noise, not
    duplicate fills.
    """
    now = time.time()
    try:
        with _get_conn() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS system_config "
                "(key TEXT PRIMARY KEY, value TEXT)"
            )
            row = conn.execute(
                "SELECT value FROM system_config WHERE key=?", (LOCK_KEY,)
            ).fetchone()
            if row and row[0]:
                try:
                    holder_pid, holder_ts = str(row[0]).split(":", 1)
                    if (now - float(holder_ts)) < LOCK_TTL_SEC \
                            and int(holder_pid) != os.getpid():
                        log.warning("another recovery instance holds the lock "
                                    "(pid=%s); standing down this cycle", holder_pid)
                        return False
                except Exception:
                    pass  # malformed lock value — reclaim it
            conn.execute(
                "INSERT INTO system_config(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (LOCK_KEY, f"{os.getpid()}:{now}"),
            )
            conn.commit()
        return True
    except Exception as exc:
        log.warning("singleton lock unavailable (%s); proceeding — state guards "
                    "still prevent double-handling", exc)
        return True


REQUIRED_PP_COLUMNS = (
    "id", "token_name", "mint_address", "status", "entry_price", "quantity",
    "position_size_usd", "take_profit_pct", "stop_loss_pct", "realized_pnl_usd",
    "unrealized_pnl_usd", "opened_at", "closed_at", "last_price",
    "last_marked_at", "entry_price_source", "entry_price_ts", "strategy_version",
    "funding_mode", "money_source", "execution_source", "mode", "source_note",
    "live_state", "buy_tx_sig", "sell_tx_sig", "chain_confirmed_at",
    "reconciled_at", "actual_entry_price", "actual_quantity", "entry_sol_spent",
    "entry_fee_sol", "exit_sol_received", "exit_fee_sol", "settlement_pnl_sol",
    "fill_meta_json", "sim_parent_position_id", "highest_price_seen",
    "exit_price", "exit_reason", "win_loss",
)


def _verify_schema_preconditions(conn) -> tuple[bool, str]:
    """
    FAIL-CLOSED startup gate. Every paper_positions column this service writes
    must already exist. execution_engine.py:326–361 adds the live lifecycle
    columns at its own startup; the launcher starts this service in the same
    controlled phase as the reconciler — after prelaunch schema init — so the
    columns are present. If they are not, recovery must NOT run: a half-schema
    write could corrupt REAL rows. It heartbeats the exact missing columns
    instead and retries next cycle.
    """
    try:
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(paper_positions)").fetchall()}
    except Exception as exc:
        return False, f"paper_positions unreadable: {exc}"
    if not cols:
        return False, "paper_positions does not exist yet (schema init pending)"
    missing = [c for c in REQUIRED_PP_COLUMNS if c not in cols]
    if missing:
        return False, "missing paper_positions columns: " + ",".join(missing[:8])
    return True, "schema ok"


# ── Chain helpers (delegated to live_trading — one RPC truth path) ───────────

def _wallet_pubkey() -> Optional[str]:
    try:
        from services.live_trading import _load_keypair
        kp = _load_keypair()
        return str(kp.pubkey()) if kp else None
    except Exception as exc:
        log.error("wallet pubkey unavailable: %s", exc)
        return None


def _signature_status(sig: str) -> dict:
    """
    Returns {"found": bool, "err": Any|None, "confirmed": bool}.
    Fail-closed: RPC failure reports found=False so nothing is advanced.
    """
    out = {"found": False, "err": None, "confirmed": False}
    try:
        from services.live_trading import _rpc_call
        res = _rpc_call(
            "getSignatureStatuses",
            [[sig], {"searchTransactionHistory": True}],
            timeout=10.0,
        )
        status = ((res or {}).get("value") or [None])[0]
        if status is None:
            return out
        out["found"] = True
        out["err"] = status.get("err")
        out["confirmed"] = status.get("confirmationStatus") in ("confirmed", "finalized")
        return out
    except Exception as exc:
        log.warning("getSignatureStatuses failed sig=%s: %s", sig[:20], exc)
        return out


def _resolve_fill(sig: str, wallet: str, mint: str) -> Optional[dict]:
    try:
        from services.live_trading import resolve_confirmed_fill
        return resolve_confirmed_fill(sig, wallet, mint, attempts=3)
    except Exception as exc:
        log.warning("resolve fill failed sig=%s: %s", sig[:20], exc)
        return None


def _cached_sol_usd() -> float:
    try:
        from services.live_trading import _get_cached_sol_price
        return float(_get_cached_sol_price() or 0.0)
    except Exception:
        return 0.0


# ── Ledger access ─────────────────────────────────────────────────────────────

def _ensure_schema(conn) -> None:
    try:
        from services.live_trading import _ensure_live_ledger_schema
        _ensure_live_ledger_schema(conn)
    except Exception:
        pass
    # STRUCTURAL DUPLICATE GUARD: one REAL position per buy signature, enforced
    # by the database rather than by application logic. Even if two recovery
    # processes raced past the state guards, the second INSERT would violate
    # this index and fail — a duplicate REAL row is structurally impossible.
    # Partial index (SQLite 3.8+) so SIM rows and NULL sigs are unaffected.
    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_pp_real_buy_sig "
            "ON paper_positions(buy_tx_sig) "
            "WHERE buy_tx_sig IS NOT NULL AND UPPER(COALESCE(funding_mode,'SIM'))='REAL'"
        )
    except Exception as exc:
        # Pre-existing duplicates would make this fail — that is itself a
        # finding worth surfacing rather than swallowing silently.
        log.warning("[SCHEMA] could not create ux_pp_real_buy_sig (%s); "
                    "application-level guards remain in force", exc)


def _ledger_set_state(conn, sig: str, state: str, *, error: Optional[str] = None,
                      block_time: Optional[float] = None,
                      reconciled_at: Optional[float] = None,
                      fill_meta_json: Optional[str] = None,
                      expected_states: Optional[tuple] = None) -> bool:
    """
    Guarded state transition. Returns True only if a row actually moved —
    the WHERE clause on the prior state is what makes duplicate callbacks
    (directive test 6) no-ops.
    """
    sets = ["state=?", "updated_at=?"]
    params: list[Any] = [state, time.time()]
    if error is not None:
        sets.append("error=?"); params.append(error[:300])
    if block_time is not None:
        sets.append("block_time=?"); params.append(block_time)
    if reconciled_at is not None:
        sets.append("reconciled_at=?"); params.append(reconciled_at)
    if fill_meta_json is not None:
        sets.append("fill_meta_json=?"); params.append(fill_meta_json)
    where = "tx_sig=?"
    params.append(sig)
    if expected_states:
        where += f" AND state IN ({','.join('?' * len(expected_states))})"
        params.extend(expected_states)
    cur = conn.execute(f"UPDATE live_tx_ledger SET {', '.join(sets)} WHERE {where}", params)
    return cur.rowcount > 0


# ── BUY recovery ──────────────────────────────────────────────────────────────

def _recover_buy(conn, row, wallet: str) -> str:
    sig = row["tx_sig"]
    mint = row["mint_address"]
    sim_parent = row["position_id"]

    status = _signature_status(sig)
    if not status["found"]:
        age = time.time() - float(row["submitted_at"] or row["created_at"] or 0)
        if age > MANUAL_AFTER_SEC:
            _ledger_set_state(conn, sig, "MANUAL_INTERVENTION",
                              error=f"signature_not_found_after_{age:.0f}s",
                              expected_states=("SUBMITTED", "CONFIRMED_UNRESOLVED"))
            return "manual"
        return "pending"

    if status["err"]:
        # Definitive chain failure — no fill occurred, no position exists.
        _ledger_set_state(conn, sig, "FAILED_ON_CHAIN",
                          error=f"chain_err:{json.dumps(status['err'])[:200]}",
                          expected_states=("SUBMITTED", "CONFIRMED_UNRESOLVED"))
        # If an UNRESOLVED placeholder row was written for this sig, that is a
        # contradiction (row implies chain success). Escalate, never guess.
        conn.execute(
            "UPDATE paper_positions SET live_state='MANUAL_INTERVENTION', "
            "source_note=COALESCE(source_note,'')||'|recovery_chain_err_contradiction' "
            "WHERE buy_tx_sig=? AND UPPER(COALESCE(funding_mode,'SIM'))='REAL' "
            "AND COALESCE(live_state,'') IN ('BUY_SUBMITTED','BUY_CONFIRMED_UNRESOLVED')",
            (sig,),
        )
        log.warning("[BUY_RECOVERY] sig=%s failed on chain — ledger closed, no position",
                    sig[:20])
        return "failed_on_chain"

    if not status["confirmed"]:
        return "pending"

    fill = _resolve_fill(sig, wallet, mint)
    if not fill or int(fill.get("token_delta_raw") or 0) <= 0 \
            or int(fill.get("native_delta_lamports") or 0) >= 0:
        return "unresolved"

    block_time = float(fill.get("block_time") or 0.0)
    if block_time <= 0:
        # blockTime is the canonical ownership boundary — without it we do not
        # open REAL state (directive Part 1).
        return "unresolved"

    # Idempotency: a REAL row already carrying this buy signature means the
    # fill was already recorded (by the executor or a previous pass).
    existing = conn.execute(
        "SELECT id FROM paper_positions WHERE buy_tx_sig=? "
        "AND UPPER(COALESCE(funding_mode,'SIM'))='REAL'",
        (sig,),
    ).fetchone()

    qty = Decimal(str(fill["token_delta"]))
    net_spent_sol = -Decimal(str(fill["native_delta_sol"]))
    sol_usd = Decimal(str(_cached_sol_usd() or 0.0))
    cost_usd = net_spent_sol * sol_usd
    entry_price = (cost_usd / qty) if qty > 0 and sol_usd > 0 else Decimal(0)
    fill_json = json.dumps(fill, sort_keys=True, default=str)
    now = time.time()

    if existing:
        # Promote a placeholder BUY_CONFIRMED_UNRESOLVED row to OPEN_REAL with
        # chain truth. Guarded on the unresolved states so a duplicate pass
        # (test 6) matches zero rows and changes nothing.
        cur = conn.execute(
            "UPDATE paper_positions SET status='OPEN', live_state='OPEN_REAL', "
            "entry_price=?, actual_entry_price=?, quantity=?, actual_quantity=?, "
            "position_size_usd=?, opened_at=?, entry_price_ts=?, last_price=?, "
            "last_marked_at=?, chain_confirmed_at=?, reconciled_at=?, "
            "entry_sol_spent=?, entry_fee_sol=?, fill_meta_json=?, "
            "highest_price_seen=?, "
            "source_note=COALESCE(source_note,'')||'|recovered_by_settlement_recovery' "
            "WHERE id=? AND COALESCE(live_state,'') IN "
            "('BUY_SUBMITTED','BUY_CONFIRMED_UNRESOLVED')",
            (float(entry_price), float(entry_price), float(qty), float(qty),
             float(cost_usd), block_time, block_time, float(entry_price), now,
             block_time, now, float(net_spent_sol),
             float(Decimal(str(fill.get("fee_sol") or 0))), fill_json,
             float(entry_price), existing["id"]),
        )
        if cur.rowcount:
            log.critical("[BUY_RECOVERY] promoted pos=%d to OPEN_REAL from chain "
                         "truth sig=%s qty=%s spent_sol=%s",
                         existing["id"], sig[:20], qty, net_spent_sol)
    else:
        # No row exists (crash during BUY_SUBMITTED / timeout path). Create the
        # REAL position from chain truth only. Entry baseline is the confirmed
        # fill — NEVER a SIM price (NTO phantom-winner regression).
        conn.execute(
            """INSERT INTO paper_positions (
                   token_name, mint_address, status, entry_price, quantity,
                   position_size_usd, take_profit_pct, stop_loss_pct,
                   realized_pnl_usd, unrealized_pnl_usd, opened_at, last_price,
                   last_marked_at, entry_price_source, entry_price_ts,
                   strategy_version, funding_mode, money_source, execution_source,
                   mode, source_note, live_state, buy_tx_sig, chain_confirmed_at,
                   reconciled_at, actual_entry_price, actual_quantity,
                   entry_sol_spent, entry_fee_sol, fill_meta_json,
                   sim_parent_position_id, highest_price_seen
               ) VALUES (?,?,'OPEN',?,?,?,?,?,0.0,0.0,?,?,?,?,?,?,
                         'REAL','REAL_WALLET','REAL_TX','live',?,?,?,?,?,?,?,?,?,?,?,?)""",
            (mint[:12], mint, float(entry_price), float(qty), float(cost_usd),
             float(os.getenv("LIVE_DEFAULT_TP_PCT", "35")),
             float(os.getenv("LIVE_DEFAULT_SL_PCT", "-12")),
             block_time, float(entry_price), now, f"live_tx:{sig[:20]}", block_time,
             "RECOVERED",
             "recovered_orphaned_buy_by_settlement_recovery", "OPEN_REAL", sig,
             block_time, now, float(entry_price), float(qty),
             float(net_spent_sol), float(Decimal(str(fill.get("fee_sol") or 0))),
             fill_json, sim_parent, float(entry_price)),
        )
        log.critical("[BUY_RECOVERY] created OPEN_REAL row from orphaned chain fill "
                     "sig=%s mint=%s qty=%s spent_sol=%s",
                     sig[:20], mint[:16], qty, net_spent_sol)

    _ledger_set_state(conn, sig, "RESOLVED", block_time=block_time,
                      reconciled_at=now, fill_meta_json=fill_json,
                      expected_states=("SUBMITTED", "CONFIRMED_UNRESOLVED"))
    return "resolved"


# ── SELL recovery ─────────────────────────────────────────────────────────────

def _recover_sell(conn, row, wallet: str) -> str:
    sig = row["tx_sig"]
    mint = row["mint_address"]
    position_id = row["position_id"]

    status = _signature_status(sig)
    if not status["found"]:
        age = time.time() - float(row["submitted_at"] or row["created_at"] or 0)
        if age > MANUAL_AFTER_SEC:
            _ledger_set_state(conn, sig, "MANUAL_INTERVENTION",
                              error=f"signature_not_found_after_{age:.0f}s",
                              expected_states=("SUBMITTED", "CONFIRMED_UNRESOLVED"))
            conn.execute(
                "UPDATE paper_positions SET live_state='MANUAL_INTERVENTION' "
                "WHERE id=? AND UPPER(COALESCE(funding_mode,'SIM'))='REAL' "
                "AND COALESCE(live_state,'') IN ('SELL_SUBMITTED','SELL_CONFIRMED_UNRESOLVED')",
                (position_id,),
            )
            return "manual"
        return "pending"

    if status["err"]:
        # Sell failed on chain — tokens never left. Revert to OPEN_REAL so the
        # exit engine can trigger again. Clear the dead signature.
        _ledger_set_state(conn, sig, "FAILED_ON_CHAIN",
                          error=f"chain_err:{json.dumps(status['err'])[:200]}",
                          expected_states=("SUBMITTED", "CONFIRMED_UNRESOLVED"))
        cur = conn.execute(
            "UPDATE paper_positions SET live_state='OPEN_REAL', sell_tx_sig=NULL, "
            "source_note=COALESCE(source_note,'')||'|sell_failed_on_chain_reverted' "
            "WHERE id=? AND UPPER(COALESCE(funding_mode,'SIM'))='REAL' "
            "AND status='OPEN' "
            "AND COALESCE(live_state,'') IN ('SELL_SUBMITTED','SELL_CONFIRMED_UNRESOLVED')",
            (position_id,),
        )
        if cur.rowcount:
            log.warning("[SELL_RECOVERY] pos=%s sell sig=%s errored on chain — "
                        "position reverted to OPEN_REAL", position_id, sig[:20])
        return "failed_on_chain"

    if not status["confirmed"]:
        return "pending"

    fill = _resolve_fill(sig, wallet, mint)
    if not fill or int(fill.get("token_delta_raw") or 0) >= 0 \
            or int(fill.get("native_delta_lamports") or 0) <= 0:
        return "unresolved"
    block_time = float(fill.get("block_time") or 0.0)
    if block_time <= 0:
        return "unresolved"

    pos = conn.execute(
        "SELECT id, status, live_state, entry_sol_spent, entry_price, "
        "position_size_usd, quantity, actual_quantity, mint_address, sell_tx_sig "
        "FROM paper_positions WHERE id=? AND UPPER(COALESCE(funding_mode,'SIM'))='REAL'",
        (position_id,),
    ).fetchone()
    if not pos:
        _ledger_set_state(conn, sig, "MANUAL_INTERVENTION",
                          error="sell_fill_resolved_but_position_missing",
                          expected_states=("SUBMITTED", "CONFIRMED_UNRESOLVED"))
        log.critical("[SELL_RECOVERY] resolved sell sig=%s has no REAL position id=%s",
                     sig[:20], position_id)
        return "manual"

    # WRONG-ROW GUARD: the position must be the one that actually submitted this
    # signature, for this mint. position_id alone is not sufficient identity —
    # SIM and REAL rows can share a mint (test 16), ids can be reused after a
    # restore, and a stale ledger row must never settle an unrelated position.
    if str(pos["sell_tx_sig"] or "") != sig:
        _ledger_set_state(conn, sig, "MANUAL_INTERVENTION",
                          error=f"position_sell_sig_mismatch:{str(pos['sell_tx_sig'])[:20]}",
                          expected_states=("SUBMITTED", "CONFIRMED_UNRESOLVED"))
        log.critical("[SELL_RECOVERY] REFUSING to settle pos=%s — its sell_tx_sig=%s "
                     "does not match ledger sig=%s", position_id,
                     str(pos["sell_tx_sig"])[:20], sig[:20])
        return "manual"
    if str(pos["mint_address"] or "") != mint:
        _ledger_set_state(conn, sig, "MANUAL_INTERVENTION",
                          error="position_mint_mismatch",
                          expected_states=("SUBMITTED", "CONFIRMED_UNRESOLVED"))
        log.critical("[SELL_RECOVERY] REFUSING to settle pos=%s — mint mismatch "
                     "(row=%s ledger=%s)", position_id,
                     str(pos["mint_address"])[:16], mint[:16])
        return "manual"

    if str(pos["live_state"] or "") == "SETTLED":
        # Duplicate callback / second pass — already settled. No-op.
        _ledger_set_state(conn, sig, "RESOLVED", block_time=block_time,
                          reconciled_at=time.time(),
                          expected_states=("SUBMITTED", "CONFIRMED_UNRESOLVED"))
        return "already_settled"

    qty_sold = -Decimal(str(fill["token_delta"]))
    net_received_sol = Decimal(str(fill["native_delta_sol"]))
    entry_spent_sol = Decimal(str(pos["entry_sol_spent"] or 0))
    if entry_spent_sol <= 0 or net_received_sol <= 0:
        conn.execute(
            "UPDATE paper_positions SET live_state='MANUAL_INTERVENTION' WHERE id=?",
            (position_id,),
        )
        _ledger_set_state(conn, sig, "MANUAL_INTERVENTION",
                          error="settlement_inputs_unresolved",
                          expected_states=("SUBMITTED", "CONFIRMED_UNRESOLVED"))
        return "manual"

    settlement_pnl_sol = net_received_sol - entry_spent_sol
    pnl_pct = float(settlement_pnl_sol / entry_spent_sol * 100)
    pos_size_usd = float(pos["position_size_usd"] or 0.0)
    pnl_usd = pos_size_usd * pnl_pct / 100.0
    entry_price = float(pos["entry_price"] or 0.0)
    sol_usd = Decimal(str(_cached_sol_usd() or 0.0))
    exit_price = float(net_received_sol * sol_usd / qty_sold) \
        if qty_sold > 0 and sol_usd > 0 else entry_price * (1.0 + pnl_pct / 100.0)
    outcome = "WIN" if pnl_usd > 0 else ("LOSS" if pnl_usd < 0 else "SCRATCH")
    fill_json = json.dumps(fill, sort_keys=True, default=str)
    now = time.time()

    # Honest residue: report unsold remainder rather than pretending flat.
    held_qty = float(pos["actual_quantity"] or pos["quantity"] or 0.0)
    residue = max(0.0, held_qty - float(qty_sold))
    residue_note = f"|token_residue={residue:.6f}" if residue > 1e-9 else ""

    cur = conn.execute(
        "UPDATE paper_positions SET status='CLOSED', live_state='SETTLED', "
        "exit_price=?, realized_pnl_usd=?, unrealized_pnl_usd=0.0, closed_at=?, "
        "last_price=?, last_marked_at=?, exit_reason=?, win_loss=?, "
        "sell_tx_sig=?, chain_confirmed_at=?, reconciled_at=?, "
        "exit_sol_received=?, exit_fee_sol=?, settlement_pnl_sol=?, "
        "fill_meta_json=?, "
        "source_note=COALESCE(source_note,'')||'|settled_by_settlement_recovery'||? "
        "WHERE id=? AND status='OPEN' "
        "AND COALESCE(live_state,'') IN ('SELL_SUBMITTED','SELL_CONFIRMED_UNRESOLVED')",
        (exit_price, pnl_usd, block_time, exit_price, now,
         f"LIVE_RECOVERED:{sig[:12]}", outcome, sig, block_time, now,
         float(net_received_sol), float(Decimal(str(fill.get("fee_sol") or 0))),
         float(settlement_pnl_sol), fill_json, residue_note, position_id),
    )
    if cur.rowcount:
        log.critical("[SELL_RECOVERY] SETTLED pos=%s from chain truth sig=%s "
                     "pnl_sol=%s (%.2f%%) residue=%.6f",
                     position_id, sig[:20], settlement_pnl_sol, pnl_pct, residue)
    _ledger_set_state(conn, sig, "RESOLVED", block_time=block_time,
                      reconciled_at=now, fill_meta_json=fill_json,
                      expected_states=("SUBMITTED", "CONFIRMED_UNRESOLVED"))
    return "settled"


# ── Position-side sweep (rows that carry a sig but whose ledger row is gone) ──

def _sweep_position_states(conn, wallet: str) -> int:
    """
    Safety net: REAL rows stuck in submitted/unresolved live_state whose
    signature is missing from the ledger (pre-upgrade rows, pruned ledger).
    Seeds a ledger row so the normal recovery paths take over next cycle.
    """
    seeded = 0
    rows = conn.execute(
        "SELECT id, mint_address, live_state, buy_tx_sig, sell_tx_sig "
        "FROM paper_positions WHERE UPPER(COALESCE(funding_mode,'SIM'))='REAL' "
        "AND COALESCE(live_state,'') IN ('BUY_SUBMITTED','BUY_CONFIRMED_UNRESOLVED',"
        "'SELL_SUBMITTED','SELL_CONFIRMED_UNRESOLVED')",
    ).fetchall()
    for r in rows:
        state = str(r["live_state"])
        side = "BUY" if state.startswith("BUY") else "SELL"
        sig = r["buy_tx_sig"] if side == "BUY" else r["sell_tx_sig"]
        if not sig:
            conn.execute(
                "UPDATE paper_positions SET live_state='MANUAL_INTERVENTION', "
                "source_note=COALESCE(source_note,'')||'|unresolved_state_without_signature' "
                "WHERE id=?", (r["id"],),
            )
            log.critical("[SWEEP] pos=%d in %s with NO signature — MANUAL_INTERVENTION",
                         r["id"], state)
            continue
        exists = conn.execute(
            "SELECT 1 FROM live_tx_ledger WHERE tx_sig=?", (sig,)
        ).fetchone()
        if not exists:
            now = time.time()
            conn.execute(
                "INSERT INTO live_tx_ledger (tx_sig,side,mint_address,position_id,"
                "state,submitted_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(tx_sig) DO NOTHING",
                (sig, side, r["mint_address"], r["id"], "SUBMITTED", now, now, now),
            )
            seeded += 1
    return seeded


def get_recovery_diagnostics() -> dict:
    """
    Operational visibility surface for Diagnostics / forensic disclosure.
    READ-ONLY. Returns pending / unresolved / manual counts plus per-transaction
    signature, side, mint, age and state. This reports LEDGER TRUTH ONLY — it
    never computes, infers, or claims executor readiness.
    """
    out = {"pending": 0, "confirmed_unresolved": 0, "manual_intervention": 0,
           "resolved": 0, "failed_on_chain": 0, "positions_manual": 0,
           "rows": [], "heartbeat": None, "available": False, "error": None}
    try:
        with _get_conn() as conn:
            _ensure_schema(conn)
            now = time.time()
            for r in conn.execute(
                "SELECT state, COUNT(*) n FROM live_tx_ledger GROUP BY state"
            ).fetchall():
                key = {"SUBMITTED": "pending",
                       "CONFIRMED_UNRESOLVED": "confirmed_unresolved",
                       "MANUAL_INTERVENTION": "manual_intervention",
                       "RESOLVED": "resolved",
                       "FAILED_ON_CHAIN": "failed_on_chain"}.get(r[0])
                if key:
                    out[key] = int(r[1])
            for r in conn.execute(
                "SELECT tx_sig, side, mint_address, position_id, state, "
                "COALESCE(submitted_at, created_at) ts, error "
                "FROM live_tx_ledger WHERE state IN "
                "('SUBMITTED','CONFIRMED_UNRESOLVED','MANUAL_INTERVENTION') "
                "ORDER BY created_at DESC LIMIT 25"
            ).fetchall():
                out["rows"].append({
                    "tx_sig": r[0], "side": r[1], "mint": r[2],
                    "position_id": r[3], "state": r[4],
                    "age_sec": round(now - float(r[5] or now), 1),
                    "error": r[6],
                })
            try:
                out["positions_manual"] = int(conn.execute(
                    "SELECT COUNT(*) FROM paper_positions "
                    "WHERE UPPER(COALESCE(funding_mode,'SIM'))='REAL' "
                    "AND COALESCE(live_state,'')='MANUAL_INTERVENTION'"
                ).fetchone()[0] or 0)
            except Exception:
                pass
            hb = conn.execute(
                "SELECT status, note, last_pulse FROM system_heartbeat "
                "WHERE service_name=?", (SERVICE_NAME,)
            ).fetchone()
            if hb:
                out["heartbeat"] = {
                    "status": hb[0], "note": hb[1],
                    "age_sec": round(now - float(hb[2] or now), 1),
                }
            out["available"] = True
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}:{exc}"
    return out


# ── Main pass ────────────────────────────────────────────────────────────────

def recover_once() -> dict:
    summary = {"resolved": 0, "settled": 0, "failed_on_chain": 0,
               "pending": 0, "manual": 0, "unresolved": 0,
               "already_settled": 0, "seeded": 0, "deferred": 0, "errors": 0}
    wallet = _wallet_pubkey()
    if not wallet:
        _heartbeat("DEGRADED", "no wallet keypair — recovery idle (correct in paper mode)")
        return summary
    if not _acquire_singleton_lock():
        _heartbeat("ALIVE", "standing down — another instance holds the lock")
        return summary
    try:
        with _get_conn() as conn:
            ok, why = _verify_schema_preconditions(conn)
            if not ok:
                _heartbeat("BLOCKED", f"schema precondition failed: {why}")
                log.critical("[SCHEMA_GATE] recovery refusing to run — %s", why)
                summary["errors"] += 1
                return summary
            _ensure_schema(conn)
            summary["seeded"] = _sweep_position_states(conn, wallet)
            cutoff = time.time() - MIN_AGE_BEFORE_RECOVERY_SEC
            rows = conn.execute(
                "SELECT * FROM live_tx_ledger WHERE state IN "
                "('SUBMITTED','CONFIRMED_UNRESOLVED') AND created_at <= ? "
                "ORDER BY created_at ASC LIMIT 25",
                (cutoff,),
            ).fetchall()
            for row in rows:
                try:
                    fn = _recover_buy if row["side"] == "BUY" else _recover_sell
                    outcome = _retry_on_lock(lambda: fn(conn, row, wallet))
                    summary[outcome] = summary.get(outcome, 0) + 1
                except Exception as exc:
                    if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                        # Yield to the executor; the row is durable and this
                        # pass is idempotent, so deferring costs nothing.
                        summary["deferred"] += 1
                        log.warning("deferring sig=%s to next cycle (db lock)",
                                    row["tx_sig"][:20])
                        continue
                    summary["errors"] += 1
                    log.exception("recovery error sig=%s: %s", row["tx_sig"][:20], exc)
            conn.commit()
    except Exception as exc:
        summary["errors"] += 1
        log.exception("recover_once failed: %s", exc)
    note = " ".join(f"{k}={v}" for k, v in summary.items() if v)
    _heartbeat("ALIVE" if summary["errors"] == 0 else "DEGRADED",
               note or "idle — no unresolved live transactions",
               work=summary["resolved"] + summary["settled"])
    if any(summary[k] for k in ("resolved", "settled", "failed_on_chain", "manual")):
        log.info("[RECOVERY] pass complete — %s", note)
    return summary


def run() -> None:
    log.info("live settlement recovery starting — cycle=%.0fs manual_after=%.0fs",
             CYCLE_SEC, MANUAL_AFTER_SEC)
    _heartbeat("ALIVE", "starting up")
    while True:
        try:
            recover_once()
        except Exception as exc:
            log.exception("unhandled: %s", exc)
            _heartbeat("ERROR", str(exc)[:150])
        time.sleep(CYCLE_SEC)


if __name__ == "__main__":
    run()
