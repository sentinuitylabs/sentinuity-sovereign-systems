#!/usr/bin/env python3
"""
launch/live_canary_fixtures.py
===============================================================================
LIMITED LIVE CANARY VERIFIER (LIVE_CANARY_V3_20260721) — directive Phase 2

The offline verifier behind RUN_LIVE_CANARY_VERIFY.bat: the deployable check
the V2 release conditioned live sign-off on but never shipped (review
blocker 1). Runs from any installation path, submits NO transaction, touches
NO wallet, and produces one decisive line:

    PASS — LIMITED LIVE CANARY READY        (exit 0)
    FAIL — LIVE REMAINS BLOCKED             (exit non-zero)

THIRTEEN CHECKS (directive numbering):
   1  compile the five live-path files
   2  import smoke: execute_live_buy / execute_live_sell /
      resolve_confirmed_fill / preflight_live_buy / get_live_wallet_balance
   3  NTO BUY replay      (launch/replay_nto_case.py, 17 internal fixtures)
   4  NTO SELL replay     (same harness; -0.004832068 SOL truth)
   5  dynamic wallet-index fixture      (wallet deep in the key list)
   6  fee-payer-differs fixture         (fee never charged to non-payer)
   7  versioned loaded-address fixture  (wallet only in loadedAddresses)
   8  raw integer token truth           (2^53+1 survives exactly)
   9  blockTime ownership               (resolved fills carry chain time)
  10  meta.err rejection                (failed tx -> None, fail-closed)
  11  missing metadata fail-closed      (no meta / no blockTime -> None)
  12  sizing-gate fixtures A-F          (launch/verify_sizing_gate.py)
  13  effective live controls positive  (size, cap, max-open, gas reserve,
                                         daily loss limit)

Check 13 reads the REAL workspace configuration: on a machine where the
launcher interview has not stamped live sizing, it fails — which is the
correct, fail-closed answer. Live remains blocked until an operator has
armed it deliberately.
"""
from __future__ import annotations

import py_compile
import subprocess
import sys
from pathlib import Path
# Windows verifier console contract: force Unicode-safe output even when the
# parent console is cp1252. This changes presentation only, never test logic.
def _configure_verifier_console() -> None:
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

_configure_verifier_console()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


WALLET = "CanaryWallet111111111111111111111111111111"
MINT = "CanaryMint1111111111111111111111111111111111"


def _tx(*, block_time=1784161315.0, err=None, meta_present=True,
        static_keys=None, loaded_writable=None, wallet_combined_index=0,
        native_delta=-1_000_000, pre_raw=0, post_raw=1_000_000,
        fee=55_000) -> dict:
    """Mainnet-shaped getTransaction result with controllable defects."""
    static_keys = static_keys if static_keys is not None else [{"pubkey": WALLET}]
    loaded_writable = loaded_writable or []
    n = len(static_keys) + len(loaded_writable)
    pre = [500_000_000] * n
    post = [500_000_000] * n
    post[wallet_combined_index] = pre[wallet_combined_index] + native_delta
    tx = {
        "blockTime": block_time,
        "slot": 987654321,
        "transaction": {"message": {"accountKeys": static_keys}},
    }
    if meta_present:
        tx["meta"] = {
            "err": err,
            "fee": fee,
            "preBalances": pre,
            "postBalances": post,
            "loadedAddresses": {"writable": loaded_writable, "readonly": []},
            "preTokenBalances": [
                {"accountIndex": wallet_combined_index, "owner": WALLET, "mint": MINT,
                 "uiTokenAmount": {"amount": str(pre_raw), "decimals": 6}}],
            "postTokenBalances": [
                {"accountIndex": wallet_combined_index, "owner": WALLET, "mint": MINT,
                 "uiTokenAmount": {"amount": str(post_raw), "decimals": 6}}],
        }
    return tx


def main() -> int:
    print("═══ 1. compile live-path files ═════════════════════════════════")
    live_files = [
        "services/live_trading.py",
        "services/live_settlement_recovery.py",
        "services/live_decision_contract.py",
        "services/execution_engine.py",
        "services/pattern_live_arming.py",
    ]
    for rel in live_files:
        p = ROOT / rel
        try:
            py_compile.compile(str(p), doraise=True)
            check(f"compile {rel}", True)
        except Exception as exc:
            check(f"compile {rel}", False, str(exc)[:100])

    print("═══ 2. import smoke of live entry points ═══════════════════════")
    try:
        from services import live_trading as lt
        for fn in ("execute_live_buy", "execute_live_sell",
                   "resolve_confirmed_fill", "preflight_live_buy",
                   "get_live_wallet_balance"):
            check(f"import {fn}", callable(getattr(lt, fn, None)))
    except Exception as exc:
        check("import services.live_trading", False, str(exc)[:120])
        print("\nFAIL — LIVE REMAINS BLOCKED")
        return 1

    print("═══ 3-4. NTO BUY + SELL chain-truth replay ═════════════════════")
    replay = subprocess.run([sys.executable, str(ROOT / "launch" / "replay_nto_case.py")],
                            capture_output=True, text=True, cwd=str(ROOT))
    tail = (replay.stdout or "").strip().splitlines()[-1:] or ["<no output>"]
    check("NTO replay (17 fixtures incl. BUY/SELL settlement truth)",
          replay.returncode == 0, tail[0][:110])

    # The negative and structural fixtures drive the REAL resolver with an
    # injected RPC — the same mechanism the replay uses. No network, no wallet.
    print("═══ 5. dynamic wallet index ════════════════════════════════════")
    tx5 = _tx(static_keys=[{"pubkey": "Other1"}, {"pubkey": "Other2"},
                           {"pubkey": "FeePayerX"}, {"pubkey": WALLET}][::-1][::-1],
              wallet_combined_index=3, native_delta=-2_000_000)
    tx5["transaction"]["message"]["accountKeys"] = [
        {"pubkey": "FeePayerX"}, {"pubkey": "Other1"}, {"pubkey": "Other2"},
        {"pubkey": WALLET}]
    lt._rpc_call = lambda m, p, timeout=12.0: tx5
    r5 = lt.resolve_confirmed_fill("SIG5", WALLET, MINT, attempts=1)
    check("wallet found at index 3 (not assumed at 0)",
          bool(r5) and r5.get("wallet_account_indexes") == [3],
          f"wallet_indexes={r5.get('wallet_account_indexes') if r5 else None}")

    print("═══ 6. fee payer differs from wallet ═══════════════════════════")
    check("fee payer correctly identified as another account",
          bool(r5) and r5.get("wallet_is_fee_payer") is False
          and r5.get("fee_payer") == "FeePayerX")
    check("fee NOT charged to non-payer wallet economics",
          bool(r5) and r5.get("wallet_is_fee_payer") is False
          and int(r5.get("native_delta_lamports")) == -2_000_000)

    print("═══ 7. versioned loaded-address resolution ═════════════════════")
    tx7 = _tx(static_keys=[{"pubkey": "FeePayerX"}, {"pubkey": "ProgramY"}],
              loaded_writable=[WALLET], wallet_combined_index=2,
              native_delta=-3_000_000)
    lt._rpc_call = lambda m, p, timeout=12.0: tx7
    r7 = lt.resolve_confirmed_fill("SIG7", WALLET, MINT, attempts=1)
    check("wallet resolved from meta.loadedAddresses (versioned tx)",
          bool(r7) and r7.get("wallet_account_indexes") == [2],
          f"wallet_indexes={r7.get('wallet_account_indexes') if r7 else None}")

    print("═══ 8. raw integer token truth ═════════════════════════════════")
    huge = 2 ** 53 + 1   # would silently corrupt in float arithmetic
    tx8 = _tx(pre_raw=0, post_raw=huge)
    lt._rpc_call = lambda m, p, timeout=12.0: tx8
    r8 = lt.resolve_confirmed_fill("SIG8", WALLET, MINT, attempts=1)
    check("2^53+1 raw units survive exactly (int, never float)",
          bool(r8) and int(r8.get("token_delta_raw")) == huge
          and isinstance(r8.get("token_delta_raw"), int),
          f"delta_raw={r8.get('token_delta_raw') if r8 else None}")

    print("═══ 9. blockTime ownership ═════════════════════════════════════")
    check("resolved fill carries the chain blockTime verbatim",
          bool(r8) and float(r8.get("block_time")) == 1784161315.0)

    print("═══ 10. meta.err rejection (fail-closed) ═══════════════════════")
    tx10 = _tx(err={"InstructionError": [0, "Custom"]})
    lt._rpc_call = lambda m, p, timeout=12.0: tx10
    check("failed on-chain tx (meta.err set) resolves to None",
          lt.resolve_confirmed_fill("SIG10", WALLET, MINT, attempts=1) is None)

    print("═══ 11. missing metadata fail-closed ═══════════════════════════")
    tx11a = _tx(meta_present=False)
    lt._rpc_call = lambda m, p, timeout=12.0: tx11a
    check("missing meta resolves to None",
          lt.resolve_confirmed_fill("SIG11A", WALLET, MINT, attempts=1) is None)
    tx11b = _tx(block_time=None)
    lt._rpc_call = lambda m, p, timeout=12.0: tx11b
    check("missing blockTime resolves to None (ownership time is mandatory)",
          lt.resolve_confirmed_fill("SIG11B", WALLET, MINT, attempts=1) is None)
    lt._rpc_call = lambda m, p, timeout=12.0: None
    check("unfetchable transaction resolves to None",
          lt.resolve_confirmed_fill("SIG11C", WALLET, MINT, attempts=1) is None)

    print("═══ 12. sizing-gate fixtures A-F ═══════════════════════════════")
    sizing = subprocess.run([sys.executable, str(ROOT / "launch" / "verify_sizing_gate.py")],
                            capture_output=True, text=True, cwd=str(ROOT))
    stail = (sizing.stdout or "").strip().splitlines()[-1:] or ["<no output>"]
    check("sizing gate suite (static S1-S7 + dynamic A-F)",
          sizing.returncode == 0, stail[0][:110])

    print("═══ 13. effective live controls ════════════════════════════════")
    try:
        from core.schema import get_config_value
        size = float(get_config_value("LIVE_POSITION_SIZE_USD", 0.0) or 0.0)
        cap = float(get_config_value("LIVE_MAX_TOTAL_EXPOSURE_USD", 0.0) or 0.0)
        max_open = int(get_config_value("LIVE_MAX_OPEN_POSITIONS", 1) or 0)
        daily = float(get_config_value("LIVE_DAILY_LOSS_LIMIT_USD", cap) or 0.0)
        reserve = float(getattr(lt, "_GAS_RESERVE_SOL", 0.0) or 0.0)
        check("LIVE_POSITION_SIZE_USD > 0", size > 0,
              f"{size} (run the launcher interview to stamp live sizing)")
        check("LIVE_MAX_TOTAL_EXPOSURE_USD > 0", cap > 0, str(cap))
        check("LIVE_MAX_OPEN_POSITIONS >= 1", max_open >= 1, str(max_open))
        check("wallet gas reserve > 0 SOL", reserve > 0, f"{reserve} SOL")
        check("daily loss limit resolves positive", daily > 0, f"${daily}")
    except Exception as exc:
        check("live controls readable", False, str(exc)[:120])

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed: {FAILURES[:6]}")
        print("FAIL — LIVE REMAINS BLOCKED")
        return 1
    print("PASS — LIMITED LIVE CANARY READY")
    return 0


if __name__ == "__main__":
    sys.exit(main())
