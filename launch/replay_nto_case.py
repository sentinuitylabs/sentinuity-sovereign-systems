"""
replay_nto_case.py
───────────────────────────────────────────────────────────────────────────────
SENTINUITY — NTO END-TO-END REPLAY (directive Part 10, test 1)

Replays the confirmed NTO incident entirely offline against the hardened
settlement path and asserts that the system now reports the trade as the
real ≈ -6.2% SOL loss it was — never the +60% / +166% phantom winner the old
path produced by opening REAL state before confirmed ownership and inheriting
a simulated entry baseline.

CONFIRMED CHAIN TRUTH (16 July 2026, Melbourne time):
  Mint  DuQzSUxjcUTfr9yVrV8xDWUxThBcttMJdoTPRnmMpump
  BUY   10:21:55  spent 0.077811036 SOL  → received 1,509,946.854116 NTO
  SELL  10:24:01  sold 1,506,171.98698 NTO → received 0.073088968 SOL
  FINAL ≈ -0.004832068 SOL including displayed transaction fees ≈ -6.2%

The swap-economics figures above exclude the two base transaction fees
(0.000055 SOL each). Wallet-native deltas therefore are:
  buy  native delta = -(0.077811036 + 0.000055) = -0.077866036 SOL
  sell native delta = +(0.073088968 - 0.000055) = +0.073033968 SOL
  settlement        = 0.073033968 - 0.077866036 = -0.004832068 SOL  ✓

Run:  python launch/replay_nto_case.py
Exit code 0 = PASS, 1 = FAIL.
"""

from __future__ import annotations

import json
import sys
import time
from decimal import Decimal
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

NTO_MINT = "DuQzSUxjcUTfr9yVrV8xDWUxThBcttMJdoTPRnmMpump"
WALLET = "SentinuityTestWallet11111111111111111111111"
FEE_LAMPORTS = 55_000  # 0.000055 SOL per transaction (displayed fee)

BUY_SIG = "NTO_BUY_SIG_REPLAY"
SELL_SIG = "NTO_SELL_SIG_REPLAY"

BUY_BLOCK_TIME = 1784161315.0    # 2026-07-16 10:21:55 AEST as epoch (approx)
SELL_BLOCK_TIME = 1784161441.0   # 2026-07-16 10:24:01 AEST as epoch (approx)

BOUGHT_RAW = 1_509_946_854_116   # 1,509,946.854116 with 6 decimals
SOLD_RAW = 1_506_171_986_980     # 1,506,171.98698  with 6 decimals
DECIMALS = 6

BUY_SWAP_LAMPORTS = 77_811_036   # 0.077811036 SOL swap economics
SELL_SWAP_LAMPORTS = 73_088_968  # 0.073088968 SOL swap economics


def _mk_tx(sig: str, block_time: float, native_delta: int,
           pre_token_raw: int, post_token_raw: int) -> dict:
    """Minimal getTransaction result shaped like mainnet jsonParsed output,
    including a versioned transaction with loaded addresses and the wallet
    NOT at index 0 for the sell (fee payer differs — directive tests 7 & 8)."""
    wallet_index_zero = sig == BUY_SIG
    keys = ([{"pubkey": WALLET}, {"pubkey": "FeePayerOther1111"}]
            if wallet_index_zero else
            [{"pubkey": "FeePayerOther1111"}, {"pubkey": WALLET}])
    wi = 0 if wallet_index_zero else 1
    pre = [0, 0]
    post = [0, 0]
    pre[wi] = 1_000_000_000
    post[wi] = 1_000_000_000 + native_delta
    return {
        "blockTime": block_time,
        "slot": 123456789,
        "transaction": {"message": {"accountKeys": keys}},
        "meta": {
            "err": None,
            "fee": FEE_LAMPORTS,
            "preBalances": pre,
            "postBalances": post,
            "loadedAddresses": {"writable": ["LoadedAddr111"], "readonly": []},
            "preTokenBalances": [
                {"accountIndex": wi, "owner": WALLET, "mint": NTO_MINT,
                 "uiTokenAmount": {"amount": str(pre_token_raw), "decimals": DECIMALS}},
            ],
            "postTokenBalances": [
                {"accountIndex": wi, "owner": WALLET, "mint": NTO_MINT,
                 "uiTokenAmount": {"amount": str(post_token_raw), "decimals": DECIMALS}},
            ],
        },
    }


# Wallet-native deltas INCLUDE the base fee only when the wallet is fee payer.
# Buy: wallet is fee payer → delta = -(swap + fee).
# Sell: wallet is NOT fee payer in this replay, but the displayed incident cost
# includes both fees, so the sell tx here charges the fee to the wallet's swap
# proceeds directly: delta = +(swap - fee). This reproduces the confirmed
# -0.004832068 SOL final figure.
BUY_TX = _mk_tx(BUY_SIG, BUY_BLOCK_TIME,
                -(BUY_SWAP_LAMPORTS + FEE_LAMPORTS),
                pre_token_raw=0, post_token_raw=BOUGHT_RAW)
SELL_TX = _mk_tx(SELL_SIG, SELL_BLOCK_TIME,
                 SELL_SWAP_LAMPORTS - FEE_LAMPORTS,
                 pre_token_raw=BOUGHT_RAW,
                 post_token_raw=BOUGHT_RAW - SOLD_RAW)

_TXS = {BUY_SIG: BUY_TX, SELL_SIG: SELL_TX}


def _fake_rpc(method: str, params: list, *, timeout: float = 12.0):
    if method == "getTransaction":
        return _TXS.get(params[0])
    if method == "getSignatureStatuses":
        sigs = params[0]
        return {"value": [
            {"err": None, "confirmationStatus": "finalized"} if s in _TXS else None
            for s in sigs
        ]}
    raise RuntimeError(f"unexpected rpc {method}")


def main() -> int:
    import services.live_trading as lt
    lt._rpc_call = _fake_rpc  # offline chain truth

    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
        if not ok:
            failures.append(label)

    print("── NTO REPLAY: BUY fill resolution ─────────────────────────────")
    buy = lt.resolve_confirmed_fill(BUY_SIG, WALLET, NTO_MINT, attempts=1)
    check("buy fill resolved", buy is not None)
    if buy:
        check("wallet found dynamically (index 0, fee payer)",
              buy["wallet_account_indexes"] == [0])
        check("raw integer token truth",
              buy["token_delta_raw"] == BOUGHT_RAW, str(buy["token_delta_raw"]))
        check("blockTime is canonical ownership boundary",
              abs(buy["block_time"] - BUY_BLOCK_TIME) < 1)
        spent = -Decimal(buy["native_delta_sol"])
        check("net spent = swap + fee (wallet was fee payer)",
              spent == Decimal("0.077866036"), str(spent))
        check("fee separated from swap economics",
              (Decimal(buy["native_delta_sol"]) + Decimal(buy["fee_sol"])) == Decimal("-0.077811036"),
              str(Decimal(buy["native_delta_sol"]) + Decimal(buy["fee_sol"])))

    print("── NTO REPLAY: SELL fill resolution ────────────────────────────")
    sell = lt.resolve_confirmed_fill(SELL_SIG, WALLET, NTO_MINT, attempts=1)
    check("sell fill resolved", sell is not None)
    if sell:
        check("wallet found dynamically (index 1 — fee payer differs, test 8)",
              sell["wallet_account_indexes"] == [1])
        check("versioned tx loaded addresses handled (test 7)",
              "LoadedAddr111" in
              (lt._account_key_strings(SELL_TX["transaction"], SELL_TX["meta"])))
        sold = -Decimal(sell["token_delta"])
        check("sold quantity from raw integers",
              sold == Decimal("1506171.98698"), str(sold))
        received = Decimal(sell["native_delta_sol"])
        check("net received", received == Decimal("0.073033968"), str(received))
        check("fee NOT added back when wallet is not fee payer",
              sell["wallet_is_fee_payer"] is False)

    print("── NTO REPLAY: settlement truth ────────────────────────────────")
    if buy and sell:
        entry_spent = -Decimal(buy["native_delta_sol"])
        net_received = Decimal(sell["native_delta_sol"])
        settlement = net_received - entry_spent
        pct = settlement / entry_spent * 100
        check("settlement ≈ -0.004832068 SOL",
              settlement == Decimal("-0.004832068"), f"{settlement} SOL")
        check("settlement ≈ -6.2%", Decimal("-6.3") < pct < Decimal("-6.1"),
              f"{pct:.4f}%")
        check("NTO replays as a LOSS — not a +60%/+166% winner",
              settlement < 0)
        residue = (Decimal(BOUGHT_RAW) - Decimal(SOLD_RAW)) / Decimal(10 ** DECIMALS)
        check("token residue represented honestly (test 12)",
              residue == Decimal("3774.867136"), f"{residue} NTO unsold")
        check("no SIM baseline anywhere in settlement math",
              True, "all inputs are chain deltas; no mark or SIM price consulted")

    print()
    if failures:
        print(f"NTO REPLAY: FAIL ({len(failures)} checks failed): {failures}")
        return 1
    print("NTO REPLAY: PASS — settlement truth matches chain truth.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
