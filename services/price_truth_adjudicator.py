"""
price_truth_adjudicator.py — PRICE_TRUTH_SIGNOFF_20260809

ONE canonical deterministic adjudicator. Given a set of source observations for
a mint, it emits ONE structured verdict distinguishing four permanently separate
peak types.

DESIGN CONTRACT
  * Deterministic. No LLM, no network, no DB. Pure function of its inputs.
    Callers fetch observations; this module only judges them.
  * Never launders provenance. A degraded native mark stays degraded. External
    agreement creates a NEW authority class; it never promotes the original
    mark's family.
  * Never collapses the peak types. OBSERVED, CONFIRMED_MARKET and EXECUTABLE
    are persisted separately and one never overwrites another.
  * Executable authority is strictly strongest and is the ONLY class permitted
    to arm a live runner floor.
  * Unknown is not fresh, unknown is not valid, and an unrecognised source name
    is not an authority. Every ambiguity fails closed for live capital.

WHAT CHANGED IN 20260809 (audit remediation — blockers 3, 6, 7, 8, 9)
  B3  Executable-vs-anchor sanity is now SYMMETRIC. The previous check only
      rejected a quote far BELOW consensus, so an executable quote 10x ABOVE a
      corroborated consensus armed a live floor at the 10x price and cited the
      corroborators as justification. Above-anchor divergence is now the
      TIGHTER bound, because a premium is almost always a decimal/unit/mint bug
      rather than a market condition.
  B6  Executable selection is no longer "first in list order". Invalid and
      diagnostic quotes are rejected first, then the freshest surviving quote
      wins. A stale row can no longer mask a fresh valid row.
  B7  Source family classification is an EXACT registry lookup. The previous
      substring scan classified `dexscreener_jupiter_mirror` as executable and
      `alternative_feed` as native (because "native" is a substring of
      "alternative"). Unknown sources are UNKNOWN and carry no authority.
  B8  Vendor observations no longer assert age 0.0 merely because we fetched
      them now. `age_sec` means "age of the underlying market observation" and
      may be None = UNKNOWN. Unknown age is NOT treated as fresh: it disqualifies
      an observation from corroborating and from any executable role.
  B9  Direct on-chain pool simulation at our exact size is a first-class
      EXECUTABLE family (`pool_executable`), not a native observation. Router
      and pool are independent executable witnesses; their agreement is what
      EXECUTABLE_CROSS_SOURCE_CONFIRMED was always supposed to mean.

WHY EXTERNAL CORROBORATION IS NOT ENOUGH FOR LIVE
  External analytics providers (DexScreener, GeckoTerminal, GMGN, DEXTools)
  publish on caches measured in tens of seconds. They can prove a move was
  REAL. They cannot prove it was REACHABLE, and they cannot supply the reaction
  latency a stop needs. Corroborating a stale mark with four other stale marks
  does not change that.

  Therefore:
      EXTERNAL_MARKET_CORROBORATED       -> may arm a PAPER floor, research truth
      EXECUTABLE_CONFIRMED               -> may arm a LIVE floor (single family)
      EXECUTABLE_CROSS_SOURCE_CONFIRMED  -> may arm a LIVE floor (two families)
  This split is enforced in authority_for_mode() below, not left to callers.
"""
from __future__ import annotations

import statistics
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

# ── Source families ──────────────────────────────────────────────────────────
# Independence is a property of the WITNESS, not the label. Two vendors reading
# the same Raydium pool through the same indexer are one observation wearing two
# names. Sources sharing a family corroborate nothing.

FAMILY_NATIVE = "native_observed"          # our own WS/RPC spot mark
FAMILY_ROUTER_EXECUTABLE = "router_executable"   # Jupiter/Metis route for our size
FAMILY_POOL_EXECUTABLE = "pool_executable"       # our own AMM sim for our size
FAMILY_INDEXER = "market_indexer"          # DexScreener / GeckoTerminal / DEXTools
FAMILY_AGGREGATOR = "market_aggregator"    # GMGN and similar
FAMILY_UNKNOWN = "unknown"

# Families that can carry EXECUTABLE authority. Both answer "what could OUR
# exact size be sold for", by genuinely different methods:
#   router -> an aggregator's live route for our raw amount
#   pool   -> our own decode of raw on-chain reserves + AMM math for our raw amount
# A router bug (bad decimals, wrong mint, stale route) does not reproduce in the
# pool calculation, and vice versa. That is real evidence independence.
EXECUTABLE_FAMILIES = (FAMILY_ROUTER_EXECUTABLE, FAMILY_POOL_EXECUTABLE)

# Families eligible to corroborate a MARKET (not executable) move.
MARKET_CORROBORATOR_FAMILIES = (FAMILY_INDEXER, FAMILY_AGGREGATOR)

# ── EXACT source registry (B7) ───────────────────────────────────────────────
# Exact, case-folded match ONLY. No substring inference — a substring scan
# classified `dexscreener_jupiter_mirror` as executable and `alternative_feed`
# as native. Register new sources here explicitly; anything unregistered is
# UNKNOWN and carries no authority in any mode.
SOURCE_REGISTRY: Dict[str, str] = {
    # native spot observation (NOT executable — no size dimension)
    "native": FAMILY_NATIVE,
    "native_ws": FAMILY_NATIVE,
    "native_curve": FAMILY_NATIVE,
    "curve_reserve": FAMILY_NATIVE,
    "pool_quote": FAMILY_NATIVE,
    "rpc": FAMILY_NATIVE,
    "rpc_direct": FAMILY_NATIVE,
    "ws_price_oracle": FAMILY_NATIVE,

    # router executable — an aggregator route priced for our exact raw amount
    "jupiter": FAMILY_ROUTER_EXECUTABLE,
    "jupiter_executable": FAMILY_ROUTER_EXECUTABLE,
    "jupiter-full-position": FAMILY_ROUTER_EXECUTABLE,
    "router_exec": FAMILY_ROUTER_EXECUTABLE,
    # QuickNode Metis runs Jupiter's own routing engine. It is TRANSPORT
    # redundancy, not evidence independence: it shares this family deliberately
    # so that jupiter+metis can never be counted as two witnesses.
    "metis": FAMILY_ROUTER_EXECUTABLE,
    "quicknode_metis": FAMILY_ROUTER_EXECUTABLE,

    # pool executable — our own decode + AMM math for our exact raw amount
    "pump_curve_exact_sell": FAMILY_POOL_EXECUTABLE,
    "pool_exact_sell": FAMILY_POOL_EXECUTABLE,
    "pool_executable": FAMILY_POOL_EXECUTABLE,

    # market indexers — display prices, one shared upstream witness
    "dexscreener": FAMILY_INDEXER,
    "geckoterminal": FAMILY_INDEXER,
    "dextools": FAMILY_INDEXER,
    "birdeye": FAMILY_INDEXER,

    # aggregators
    "gmgn": FAMILY_AGGREGATOR,
}


def register_source(source_id: str, family: str) -> None:
    """Register an additional exact source id. Explicit by design."""
    sid = str(source_id or "").strip().lower()
    if sid and family:
        SOURCE_REGISTRY[sid] = str(family)


def source_family(source: Any) -> str:
    """Exact registry lookup. Unregistered source -> FAMILY_UNKNOWN, never a guess."""
    return SOURCE_REGISTRY.get(str(source or "").strip().lower(), FAMILY_UNKNOWN)


# ── Policy thresholds ────────────────────────────────────────────────────────
DEFAULT_MAX_OBSERVATION_AGE_SEC = 30.0
DEFAULT_MAX_EXECUTABLE_AGE_SEC = 5.0
DEFAULT_CONSENSUS_SPREAD_PCT = 8.0      # max spread across corroborating sources
DEFAULT_OUTLIER_RATIO = 1.60            # >60% above consensus median => outlier
DEFAULT_MIN_CORROBORATORS = 2           # distinct independent families
# B3 — SYMMETRIC bounds against the best available anchor.
DEFAULT_MAX_EXECUTABLE_DISCOUNT_PCT = 35.0  # below anchor: liquidity/impact
DEFAULT_MAX_EXECUTABLE_PREMIUM_PCT = 15.0   # above anchor: almost always a bug
# B9 — two executable families must agree within this to be cross-source.
DEFAULT_EXECUTABLE_CROSS_TOLERANCE_PCT = 10.0
# Price impact ceiling for a quote to count as clean executable authority.
DEFAULT_MAX_PRICE_IMPACT_PCT = 25.0
# An executable quote with NO anchor of any kind (no consensus, no pool, no
# native mark) cannot be sanity-checked at all. Fail closed by default.
DEFAULT_ALLOW_ANCHORLESS_EXECUTABLE = False

# Verdict states
STATE_UNKNOWN = "UNKNOWN"
STATE_OBSERVED = "OBSERVED_ONLY"
STATE_NATIVE_OUTLIER = "NATIVE_OUTLIER"
STATE_MARKET_CORROBORATED = "EXTERNAL_MARKET_CORROBORATED"
STATE_EXECUTABLE_CONFIRMED = "EXECUTABLE_CONFIRMED"
STATE_EXECUTABLE_CROSS_SOURCE = "EXECUTABLE_CROSS_SOURCE_CONFIRMED"

# Every state that may arm a live floor.
LIVE_ARMING_STATES = (STATE_EXECUTABLE_CONFIRMED, STATE_EXECUTABLE_CROSS_SOURCE)

# ── Reason codes ─────────────────────────────────────────────────────────────
# Explicit, greppable, machine-checkable. Never free text alone.
R_NO_OBSERVATIONS = "NO_OBSERVATIONS"
R_UNREGISTERED_SOURCE = "UNREGISTERED_SOURCE"
R_NO_PRICE = "NO_PRICE"
R_AGE_UNKNOWN = "AGE_UNKNOWN"
R_STALE = "STALE"
R_NOT_SELLABLE = "NOT_SELLABLE"
R_INTEGRITY_NOT_VALID = "INTEGRITY_NOT_VALID"
R_IMPACT_EXCEEDS_CAP = "IMPACT_EXCEEDS_CAP"
R_EXEC_BELOW_ANCHOR = "EXEC_BELOW_ANCHOR"
R_EXEC_ABOVE_ANCHOR = "EXEC_ABOVE_ANCHOR"
R_NO_ANCHOR = "NO_ANCHOR_FOR_EXECUTABLE_SANITY"
R_INSUFFICIENT_CORROBORATION = "INSUFFICIENT_CORROBORATION"
R_SPREAD_TOO_WIDE = "CONSENSUS_SPREAD_TOO_WIDE"
R_NATIVE_OUTLIER = "NATIVE_OUTLIER"
R_EXEC_CROSS_DISAGREE = "EXECUTABLE_FAMILIES_DISAGREE"
R_OK = "OK"

# Integrity status vocabulary shared with services/peak_truth.py. Only VALID
# may carry live authority; DIAGNOSTIC_ONLY explicitly may not (B4).
INTEGRITY_VALID = "VALID"


def _f(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")) or f <= 0:
            return None
        return f
    except (TypeError, ValueError):
        return None


def _age(v: Any) -> Optional[float]:
    """Age in seconds, or None meaning EXPLICITLY UNKNOWN (B8). Never coerced to 0."""
    try:
        if v is None:
            return None
        f = float(v)
        if f != f or f < 0:
            return None
        return f
    except (TypeError, ValueError):
        return None


def _pct(price: Optional[float], entry: Optional[float]) -> Optional[float]:
    p, e = _f(price), _f(entry)
    if p is None or e is None:
        return None
    return (p - e) / e * 100.0


def _divergence_pct(value: float, anchor: float) -> float:
    """Signed % of `value` relative to `anchor`. Positive = value above anchor."""
    return (value - anchor) / anchor * 100.0


class Observation(dict):
    """
    One source observation.

    Required: source, price
    Optional:
      age_sec           age of the UNDERLYING market observation, or None=UNKNOWN
      fetch_age_sec     how long ago WE retrieved it (diagnostics only)
      degraded          provenance is degraded (still recorded, never trusted)
      sellable          executable quote is actually sellable at our size
      integrity_status  'VALID' | 'DIAGNOSTIC_ONLY' (peak_truth contract)
      raw_amount        the raw token amount the quote was priced for
      quote_out_raw     raw output amount
      min_out_raw       raw minimum output
      price_impact_pct  known impact, or omitted = UNKNOWN (never 0 by default)
      context_slot      slot the quote/state was valid at
      route             route identity
      liquidity_usd, pair_address
    """

    def __init__(self, source: str, price: Any, age_sec: Any = None, **kw):
        super().__init__(source=str(source), price=_f(price),
                         age_sec=_age(age_sec),
                         family=source_family(source), **kw)


# ─────────────────────────────────────────────────────────────────────────────
# Eligibility
# ─────────────────────────────────────────────────────────────────────────────

def _corroborator_eligible(obs: Mapping[str, Any],
                           max_age: float) -> Tuple[bool, str]:
    """A market observation may corroborate only if it is priced, fresh and clean.

    B8: unknown age is NOT fresh. A vendor observation with no meaningful
    publication timestamp cannot assert that the market printed this price
    within the freshness window, so it does not corroborate.
    """
    if obs.get("family") not in MARKET_CORROBORATOR_FAMILIES:
        return False, R_UNREGISTERED_SOURCE
    if _f(obs.get("price")) is None:
        return False, R_NO_PRICE
    if obs.get("degraded"):
        return False, R_INTEGRITY_NOT_VALID
    age = _age(obs.get("age_sec"))
    if age is None:
        return False, R_AGE_UNKNOWN
    if age > max_age:
        return False, R_STALE
    return True, R_OK


def _executable_eligible(obs: Mapping[str, Any], *, max_age: float,
                         max_impact_pct: float) -> Tuple[bool, str]:
    """An executable observation may carry authority only if fully evidenced.

    Fails closed on every ambiguity. In particular a missing integrity_status is
    treated as NOT VALID: peak_truth.record_executable_quote() always writes one,
    so its absence means the row did not come through the validated path (B4).
    """
    if obs.get("family") not in EXECUTABLE_FAMILIES:
        return False, R_UNREGISTERED_SOURCE
    if _f(obs.get("price")) is None:
        return False, R_NO_PRICE
    if not bool(obs.get("sellable")):
        return False, R_NOT_SELLABLE
    if str(obs.get("integrity_status") or "").strip().upper() != INTEGRITY_VALID:
        return False, R_INTEGRITY_NOT_VALID
    age = _age(obs.get("age_sec"))
    if age is None:
        return False, R_AGE_UNKNOWN
    if age > max_age:
        return False, R_STALE
    impact = _f(obs.get("price_impact_pct"))
    # Impact is optional: a direct pool simulation may not express one. Known
    # impact above the cap is rejected; unknown impact is carried forward as
    # explicitly unknown and surfaced in the verdict (B5).
    if impact is not None and impact > max_impact_pct:
        return False, R_IMPACT_EXCEEDS_CAP
    return True, R_OK


def _pick_freshest(candidates: List[Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
    """B6: choose by evidence quality, never by list position.

    Freshest wins. On an exact age tie the LOWER price wins, because between two
    equally-evidenced quotes the conservative one is the safer floor.
    """
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda o: (_age(o.get("age_sec")) or 1e9, _f(o.get("price")) or 1e18),
    )[0]


# ─────────────────────────────────────────────────────────────────────────────
# Adjudication
# ─────────────────────────────────────────────────────────────────────────────

def adjudicate(
    *,
    mint: str,
    entry_price: Any = None,
    observations: Iterable[Mapping[str, Any]],
    reference_time: Optional[float] = None,
    max_observation_age_sec: float = DEFAULT_MAX_OBSERVATION_AGE_SEC,
    max_executable_age_sec: float = DEFAULT_MAX_EXECUTABLE_AGE_SEC,
    consensus_spread_pct: float = DEFAULT_CONSENSUS_SPREAD_PCT,
    outlier_ratio: float = DEFAULT_OUTLIER_RATIO,
    min_corroborators: int = DEFAULT_MIN_CORROBORATORS,
    max_executable_discount_pct: float = DEFAULT_MAX_EXECUTABLE_DISCOUNT_PCT,
    max_executable_premium_pct: float = DEFAULT_MAX_EXECUTABLE_PREMIUM_PCT,
    executable_cross_tolerance_pct: float = DEFAULT_EXECUTABLE_CROSS_TOLERANCE_PCT,
    max_price_impact_pct: float = DEFAULT_MAX_PRICE_IMPACT_PCT,
    allow_anchorless_executable: bool = DEFAULT_ALLOW_ANCHORLESS_EXECUTABLE,
) -> Dict[str, Any]:
    """
    Return ONE structured verdict. Never raises.

    The verdict never mutates state and never decides an exit. It reports what
    the evidence supports; callers apply it through authority_for_mode().
    """
    now = float(reference_time or time.time())
    entry = _f(entry_price)

    v: Dict[str, Any] = {
        "mint": str(mint or ""),
        "reference_time": now,
        "state": STATE_UNKNOWN,
        "authority_reason": R_NO_OBSERVATIONS,
        "reason_code": R_NO_OBSERVATIONS,
        "warnings": [],

        "sources": {},
        "degraded_sources": [],
        "rejected": {},              # source -> reason code

        "market_consensus_price": None,
        "market_consensus_spread_pct": None,
        "corroborating_families": [],
        "corroborator_count": 0,

        "native_price": None,
        "native_family": None,
        "native_is_outlier": False,

        # executable detail, per family and combined
        "router_executable_price": None,
        "router_executable_age_sec": None,
        "pool_executable_price": None,
        "pool_executable_age_sec": None,
        "executable_price": None,
        "executable_age_sec": None,
        "executable_family": None,
        "executable_families": [],
        "executable_price_impact_pct": None,
        "executable_impact_known": False,
        "executable_sellable": False,
        "executable_raw_amount": None,
        "executable_context_slot": None,
        "executable_cross_source": False,
        "executable_cross_divergence_pct": None,
        "executable_anchor_price": None,
        "executable_anchor_kind": None,
        "executable_anchor_divergence_pct": None,
        # Why an otherwise-eligible executable quote was refused. Held separately
        # so that falling back to a weaker state (e.g. EXTERNAL_MARKET_
        # CORROBORATED) cannot overwrite it and report OK to the operator.
        "executable_reject_code": None,

        "observed_price": None,
        "confirmed_market_price": None,
        "executable_confirmed_price": None,

        "observed_pnl_pct": None,
        "confirmed_market_pnl_pct": None,
        "executable_pnl_pct": None,

        "market_move_confirmed": False,
        "executable_move_confirmed": False,
    }

    def _warn(code: str, detail: str = "") -> None:
        v["warnings"].append(code if not detail else f"{code}:{detail}")

    try:
        obs_list: List[Mapping[str, Any]] = []
        for o in (observations or []):
            if not isinstance(o, Mapping):
                continue
            d = dict(o)
            # Family is derived from the exact registry, never accepted from the
            # caller: a caller-supplied family would reopen the laundering path.
            d["family"] = source_family(d.get("source"))
            d["age_sec"] = _age(d.get("age_sec"))
            obs_list.append(d)

        if not obs_list:
            return v

        for o in obs_list:
            name = str(o.get("source"))
            v["sources"][name] = {
                "price": _f(o.get("price")),
                "age_sec": o.get("age_sec"),
                "fetch_age_sec": _age(o.get("fetch_age_sec")),
                "family": o.get("family"),
                "degraded": bool(o.get("degraded")),
                "integrity_status": o.get("integrity_status"),
                "price_impact_pct": _f(o.get("price_impact_pct")),
                "pnl_pct": _pct(o.get("price"), entry),
            }
            if o.get("degraded"):
                v["degraded_sources"].append(name)
            if o.get("family") == FAMILY_UNKNOWN:
                v["rejected"][name] = R_UNREGISTERED_SOURCE
                _warn(R_UNREGISTERED_SOURCE, name[:40])

        # ── Native spot observation ──────────────────────────────────────────
        native = _pick_freshest([o for o in obs_list
                                 if o.get("family") == FAMILY_NATIVE
                                 and _f(o.get("price")) is not None])
        if native:
            v["native_price"] = _f(native.get("price"))
            v["native_family"] = FAMILY_NATIVE

        # ── OBSERVED PEAK ────────────────────────────────────────────────────
        # Highest credible price from any REGISTERED source. Research value only.
        # Explicitly may include a degraded native mark, which is exactly why it
        # can never arm a floor by itself.
        credible = [_f(o.get("price")) for o in obs_list
                    if o.get("family") != FAMILY_UNKNOWN and _f(o.get("price"))]
        v["observed_price"] = max(credible) if credible else None
        v["observed_pnl_pct"] = _pct(v["observed_price"], entry)

        # ── CONSENSUS across INDEPENDENT external witnesses ──────────────────
        # The native mark is deliberately EXCLUDED from the consensus it is
        # being tested against; otherwise a +410% native outlier drags the
        # median it is supposed to be measured by.
        corroborators = []
        for o in obs_list:
            if o.get("family") not in MARKET_CORROBORATOR_FAMILIES:
                continue
            ok, why = _corroborator_eligible(o, max_observation_age_sec)
            if ok:
                corroborators.append(o)
            else:
                v["rejected"].setdefault(str(o.get("source")), why)

        fams = sorted({str(o.get("family")) for o in corroborators})
        v["corroborating_families"] = fams
        v["corroborator_count"] = len(fams)

        prices = [_f(o.get("price")) for o in corroborators if _f(o.get("price"))]
        consensus: Optional[float] = None
        if prices:
            consensus = statistics.median(prices)
            v["market_consensus_price"] = consensus
            if len(prices) > 1 and consensus:
                v["market_consensus_spread_pct"] = (
                    (max(prices) - min(prices)) / consensus * 100.0)
            else:
                v["market_consensus_spread_pct"] = 0.0

        # ── NATIVE OUTLIER TEST ──────────────────────────────────────────────
        # This is what rejects a +17,773% highest_price_seen automatically.
        # It is evaluated and REPORTED independently of state resolution so that
        # an outlier is never silently masked by a later executable confirmation.
        if consensus and v["native_price"]:
            if v["native_price"] > consensus * outlier_ratio:
                v["native_is_outlier"] = True
                _warn(R_NATIVE_OUTLIER,
                      f"native={v['native_price']:.10g}_consensus={consensus:.10g}")

        # ── CONFIRMED MARKET PEAK ────────────────────────────────────────────
        spread = v["market_consensus_spread_pct"]
        if consensus and v["corroborator_count"] >= min_corroborators:
            if spread is not None and spread <= consensus_spread_pct:
                v["confirmed_market_price"] = consensus
                v["confirmed_market_pnl_pct"] = _pct(consensus, entry)
                v["market_move_confirmed"] = True
            else:
                _warn(R_SPREAD_TOO_WIDE, f"{spread:.1f}pct")

        # ── EXECUTABLE EVIDENCE (B6 selection, B4 integrity, B5 impact) ──────
        eligible_exec: Dict[str, Mapping[str, Any]] = {}
        for fam in EXECUTABLE_FAMILIES:
            cands = []
            for o in obs_list:
                if o.get("family") != fam:
                    continue
                ok, why = _executable_eligible(
                    o, max_age=max_executable_age_sec,
                    max_impact_pct=max_price_impact_pct)
                if ok:
                    cands.append(o)
                else:
                    v["rejected"].setdefault(str(o.get("source")), why)
            best = _pick_freshest(cands)
            if best is not None:
                eligible_exec[fam] = best

        router = eligible_exec.get(FAMILY_ROUTER_EXECUTABLE)
        pool = eligible_exec.get(FAMILY_POOL_EXECUTABLE)
        if router:
            v["router_executable_price"] = _f(router.get("price"))
            v["router_executable_age_sec"] = _age(router.get("age_sec"))
        if pool:
            v["pool_executable_price"] = _f(pool.get("price"))
            v["pool_executable_age_sec"] = _age(pool.get("age_sec"))
        v["executable_families"] = sorted(eligible_exec.keys())

        # Choose the operative executable quote. When both families are present
        # and agree, take the LOWER price: two independent methods bracket the
        # reachable price, and the floor should sit on the conservative side.
        chosen: Optional[Mapping[str, Any]] = None
        if router and pool:
            rp, pp = _f(router.get("price")), _f(pool.get("price"))
            if rp and pp:
                div = abs(_divergence_pct(rp, pp))
                v["executable_cross_divergence_pct"] = div
                if div <= executable_cross_tolerance_pct:
                    v["executable_cross_source"] = True
                    chosen = router if rp <= pp else pool
                else:
                    # Independent methods disagree. That is exactly the signal
                    # this design exists to surface: do not silently prefer one.
                    _warn(R_EXEC_CROSS_DISAGREE, f"{div:.1f}pct")
                    v["rejected"]["executable_cross_source"] = R_EXEC_CROSS_DISAGREE
                    chosen = router if rp <= pp else pool
        else:
            chosen = router or pool

        exec_ok = chosen is not None
        if chosen is not None:
            impact = _f(chosen.get("price_impact_pct"))
            v["executable_price"] = _f(chosen.get("price"))
            v["executable_age_sec"] = _age(chosen.get("age_sec"))
            v["executable_family"] = chosen.get("family")
            v["executable_sellable"] = True
            v["executable_price_impact_pct"] = impact
            v["executable_impact_known"] = impact is not None
            v["executable_raw_amount"] = chosen.get("raw_amount")
            v["executable_context_slot"] = chosen.get("context_slot")
            if impact is None:
                _warn("PRICE_IMPACT_UNKNOWN", str(chosen.get("source"))[:40])

        # ── B3: SYMMETRIC anchor sanity ──────────────────────────────────────
        # Anchor preference: market consensus > the OTHER executable family >
        # native spot mark. An executable quote with no anchor of any kind
        # cannot be sanity-checked and fails closed by default.
        if exec_ok:
            exec_price = float(v["executable_price"])
            anchor: Optional[float] = None
            anchor_kind: Optional[str] = None
            if consensus:
                anchor, anchor_kind = consensus, "market_consensus"
            elif router and pool:
                other = pool if chosen is router else router
                other_px = _f(other.get("price"))
                if other_px:
                    anchor, anchor_kind = other_px, "executable_peer"
            if anchor is None and v["native_price"]:
                anchor, anchor_kind = float(v["native_price"]), "native_observed"

            v["executable_anchor_price"] = anchor
            v["executable_anchor_kind"] = anchor_kind

            if anchor is None:
                if not allow_anchorless_executable:
                    exec_ok = False
                    v["executable_reject_code"] = R_NO_ANCHOR
                    v["reason_code"] = R_NO_ANCHOR
                    v["authority_reason"] = (
                        "executable_quote_has_no_independent_anchor_to_sanity_check_against")
                    _warn(R_NO_ANCHOR)
            else:
                div = _divergence_pct(exec_price, anchor)
                v["executable_anchor_divergence_pct"] = div
                if div > max_executable_premium_pct:
                    # ABOVE anchor. A premium is almost never a market condition
                    # at our size; it is a decimal, unit, mint or stale-route
                    # bug. This is the tighter bound on purpose.
                    exec_ok = False
                    v["executable_reject_code"] = R_EXEC_ABOVE_ANCHOR
                    v["reason_code"] = R_EXEC_ABOVE_ANCHOR
                    v["authority_reason"] = (
                        f"executable_{div:.1f}pct_above_{anchor_kind}_exceeds_"
                        f"{max_executable_premium_pct:.0f}pct")
                    _warn(R_EXEC_ABOVE_ANCHOR, f"{div:.1f}pct")
                elif -div > max_executable_discount_pct:
                    # BELOW anchor. Real, but it is a liquidity warning rather
                    # than a corroborated exit price. Report, do not confirm.
                    exec_ok = False
                    v["executable_reject_code"] = R_EXEC_BELOW_ANCHOR
                    v["reason_code"] = R_EXEC_BELOW_ANCHOR
                    v["authority_reason"] = (
                        f"executable_{-div:.1f}pct_below_{anchor_kind}_exceeds_"
                        f"{max_executable_discount_pct:.0f}pct")
                    _warn(R_EXEC_BELOW_ANCHOR, f"{-div:.1f}pct")

        if exec_ok:
            v["executable_confirmed_price"] = v["executable_price"]
            v["executable_pnl_pct"] = _pct(v["executable_price"], entry)
            v["executable_move_confirmed"] = True

        # ── STATE RESOLUTION (strongest wins) ────────────────────────────────
        if v["executable_move_confirmed"]:
            if v["executable_cross_source"]:
                v["state"] = STATE_EXECUTABLE_CROSS_SOURCE
                v["reason_code"] = R_OK
                v["authority_reason"] = (
                    f"router_and_pool_executable_agree_within_"
                    f"{v['executable_cross_divergence_pct']:.1f}pct_"
                    f"age_{v['executable_age_sec']:.1f}s")
            else:
                v["state"] = STATE_EXECUTABLE_CONFIRMED
                v["reason_code"] = R_OK
                missing = ("pool_executable" if v["executable_family"]
                           == FAMILY_ROUTER_EXECUTABLE else "router_executable")
                v["authority_reason"] = (
                    f"single_family_{v['executable_family']}_fresh_"
                    f"{v['executable_age_sec']:.1f}s_sellable_"
                    f"no_{missing}_witness_available")
        elif v["native_is_outlier"]:
            v["state"] = STATE_NATIVE_OUTLIER
            v["reason_code"] = R_NATIVE_OUTLIER
            v["authority_reason"] = (
                f"native_{v['native_price']:.10g}_exceeds_consensus_"
                f"{consensus:.10g}_by_ratio_{outlier_ratio}")
        elif v["market_move_confirmed"]:
            v["state"] = STATE_MARKET_CORROBORATED
            # Do not report OK if an executable quote was actively refused; the
            # operator must see WHY the stronger evidence was rejected.
            v["reason_code"] = v["executable_reject_code"] or R_OK
            _why_exec = (f"_executable_refused_{v['executable_reject_code']}"
                         if v["executable_reject_code"] else
                         "_no_confirmed_executable_quote")
            v["authority_reason"] = (
                f"{v['corroborator_count']}_independent_families_within_"
                f"{consensus_spread_pct:.0f}pct{_why_exec}")
        else:
            v["state"] = STATE_OBSERVED
            if v["executable_reject_code"]:
                v["reason_code"] = v["executable_reject_code"]
            elif v["reason_code"] in (R_NO_OBSERVATIONS,):
                v["reason_code"] = R_INSUFFICIENT_CORROBORATION
                v["authority_reason"] = (
                    f"insufficient_corroboration_families={v['corroborator_count']}"
                    f"_need={min_corroborators}")
        return v
    except Exception as exc:  # never raise into an execution path
        v["state"] = STATE_UNKNOWN
        v["reason_code"] = "ADJUDICATOR_ERROR"
        v["authority_reason"] = f"adjudicator_error={type(exc).__name__}"
        return v


# ─────────────────────────────────────────────────────────────────────────────
# Authority
# ─────────────────────────────────────────────────────────────────────────────

def authority_for_mode(verdict: Mapping[str, Any], *, is_live: bool,
                       require_cross_source: bool = False) -> Dict[str, Any]:
    """
    Translate a verdict into floor-arming authority for a specific mode.

    THIS IS THE SPLIT THAT MATTERS. External market corroboration proves a move
    was REAL. Only a fresh executable quote proves it was REACHABLE at our size.
    Paper may harvest on the former; live may not.

    `require_cross_source` lets live policy be tightened to demand both an
    independent router quote AND an independent pool calculation. It defaults to
    False so this remediation does not change live arming policy on its own;
    flip it once direct-pool coverage is measured.

    Returns {"may_arm", "peak_price", "authority_class", "cross_source",
             "reason", "reason_code"}
    """
    out = {"may_arm": False, "peak_price": None, "authority_class": "NONE",
           "cross_source": False, "reason": "no_authority",
           "reason_code": R_NO_OBSERVATIONS}
    try:
        state = str(verdict.get("state") or STATE_UNKNOWN)
        cross = bool(verdict.get("executable_cross_source"))
        out["cross_source"] = cross
        out["reason_code"] = str(verdict.get("reason_code") or "")

        if state in LIVE_ARMING_STATES:
            if is_live and require_cross_source and not cross:
                out["reason"] = (
                    "cross_source_executable_required_for_live: only "
                    f"{verdict.get('executable_family')} witness available")
                out["reason_code"] = R_EXEC_CROSS_DISAGREE
                return out
            out.update(
                may_arm=True,
                peak_price=verdict.get("executable_confirmed_price"),
                authority_class=state,
                reason=str(verdict.get("authority_reason") or ""),
            )
            return out

        if state == STATE_NATIVE_OUTLIER:
            out["reason"] = ("native_outlier_rejected: "
                             + str(verdict.get("authority_reason") or ""))
            out["reason_code"] = R_NATIVE_OUTLIER
            return out

        if state == STATE_MARKET_CORROBORATED:
            if is_live:
                # Deliberate refusal. Live protection requires a price we can
                # actually reach at our size, not a price several indexers agree
                # the market printed some seconds ago.
                out["reason"] = (
                    "external_corroboration_insufficient_for_live: fresh "
                    "executable quote required before a live floor may arm")
                out["reason_code"] = R_INSUFFICIENT_CORROBORATION
                return out
            out.update(
                may_arm=True,
                peak_price=verdict.get("confirmed_market_price"),
                authority_class=STATE_MARKET_CORROBORATED,
                reason=str(verdict.get("authority_reason") or ""),
            )
            return out

        out["reason"] = f"state={state}: {verdict.get('authority_reason')}"
        return out
    except Exception as exc:
        out["reason"] = f"authority_error={type(exc).__name__}"
        out["reason_code"] = "AUTHORITY_ERROR"
        return out


def render_console(verdict: Mapping[str, Any]) -> str:
    """Operator-readable Price Truth block. Presentation only."""
    try:
        lines = [f"PRICE TRUTH — {str(verdict.get('mint') or '')[:20]}"]
        for name, s in (verdict.get("sources") or {}).items():
            pnl = s.get("pnl_pct")
            age = s.get("age_sec")
            flag = "  DEGRADED" if s.get("degraded") else ""
            pnl_s = f"{pnl:+.1f}%" if pnl is not None else "n/a"
            age_s = f"{age:.1f}s" if age is not None else "UNKNOWN"
            lines.append(f"  {name:<24}{pnl_s:>10}   age {age_s}{flag}")
        rejected = verdict.get("rejected") or {}
        if rejected:
            lines.append("")
            for name, why in rejected.items():
                lines.append(f"  REJECTED {name:<22} {why}")
        ob = verdict.get("observed_pnl_pct")
        cm = verdict.get("confirmed_market_pnl_pct")
        ex = verdict.get("executable_pnl_pct")
        lines.append("")
        lines.append(f"  observed peak        {ob:+.1f}%" if ob is not None
                     else "  observed peak        n/a")
        lines.append(f"  market consensus     {cm:+.1f}%" if cm is not None
                     else "  market consensus     n/a")
        lines.append(f"  executable peak      {ex:+.1f}%" if ex is not None
                     else "  executable peak      n/a")
        lines.append("")
        lines.append(f"  EXEC FAMILIES        {verdict.get('executable_families')}")
        lines.append(f"  CROSS SOURCE         {verdict.get('executable_cross_source')}")
        lines.append(f"  MARKET MOVE          "
                     f"{'CONFIRMED' if verdict.get('market_move_confirmed') else 'NOT CONFIRMED'}")
        lines.append(f"  EXECUTABLE MOVE      "
                     f"{'CONFIRMED' if verdict.get('executable_move_confirmed') else 'NOT CONFIRMED'}")
        lines.append(f"  STATE                {verdict.get('state')}")
        if verdict.get("native_is_outlier"):
            lines.append("  OUTLIER              NATIVE")
        for w in (verdict.get("warnings") or []):
            lines.append(f"  WARNING              {w}")
        lines.append(f"  REASON               {verdict.get('reason_code')} "
                     f"{verdict.get('authority_reason')}")
        return "\n".join(lines)
    except Exception:
        return "PRICE TRUTH — render error"
