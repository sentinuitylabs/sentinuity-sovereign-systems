"""
services/smart_money_metrics.py
================================
Computes per-token smart money metrics and scores.

SIGNOFF_SMART_MONEY_COVERAGE_20260810 (Bundle A — measurement/contract repair):

    The previous revision had three structural defects that made its output
    actively misleading rather than merely weak:

    1. HOLDER STARVATION SCORED AS DATA.
       market_snapshots.holder_count has no producer (market_intelligence
       writes literal None at both construction sites), so holders_now was
       always 0, the `holders_now > 0` guard always failed, and the two
       holder-derived components (25 + 15 = 40 of 100 points) were
       permanently unavailable.  The score was nonetheless emitted on the
       full 0-100 scale, so 'ELITE_RUNNER' (>=70) was mathematically
       unreachable and the effective ceiling was 60.

    2. ABSENCE OF EVIDENCE SCORED AS GOOD EVIDENCE.
       - sell pressure: with no rows in wallet_write_log, sells=0 and
         buys=0.001, giving sell_ratio=0.0 and a free +15 ("low selling").
       - volume stability: with a single snapshot, std=0 by construction,
         giving a free +15 ("stable volume").
       A token with no data at all therefore scored 30 => 'MOMENTUM'.
       Silence was being promoted to a signal.

    3. STARVED OUTPUT POISONED THE CALIBRATOR.
       tx_resolver._smart_money_score() maps an explicit score of 30 to
       0.30, but maps ABSENCE to 0.50 (neutral, produced=False).  Because
       EDGE_RESTORE_SIGNOFF_20260810 correctly moved this computation ahead
       of _write_qualifier_result(), the starved 0.30 began reaching
       calibrate_confidence() and LOWERED calibrated confidence relative to
       emitting nothing at all.

    This revision does not invent holder data and does not widen any gate.
    It makes the contract honest:

      * every component declares whether its inputs were actually OBSERVED;
      * the score is renormalised over observed components only, so it stays
        on a comparable 0-100 scale and tiers remain reachable;
      * absence is never scored as favourable, and never as zero;
      * below a coverage floor compute_metrics() returns None, which the
        existing market_intelligence call site already treats as "no smart
        money evidence" — restoring the neutral 0.50 calibrator path instead
        of the poisoned 0.30 path;
      * every evaluation, including unmeasurable ones, is recorded in
        smart_money_coverage so starvation is visible in telemetry rather
        than silently absorbed.

    No execution authority is granted or removed here.  Smart money remains
    additive evidence only.

Schema-safe: PRAGMA-checks all columns before use.  Never raises.
"""
import time
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB = str(BASE_DIR / 'sentinuity_matrix.db')

# ── Contract constants ──────────────────────────────────────────────────────
# Component weights are declared on a 100-point basis.  The delivered score is
# renormalised over the components whose inputs were actually observed, so a
# partially-covered token is scored on its own evidence rather than being
# silently penalised for missing producers.
_COMPONENT_WEIGHTS = {
    'holder_growth_180s': 25,
    'wallet_cluster':     20,
    'sell_pressure':      15,
    'volume_stability':   15,
    'volume_magnitude':   10,
    'holder_momentum_60s': 15,
}
_TOTAL_WEIGHT = sum(_COMPONENT_WEIGHTS.values())   # 100

# Minimum fraction of total weight that must be OBSERVED for the score to be
# emitted at all.  Below this, the token is reported as NOT_MEASURED and
# compute_metrics() returns None so downstream consumers fall back to their
# neutral/absent path rather than consuming a starved number.
MIN_COVERAGE_FRACTION = 0.35

# Volume stability requires a real sample; a single snapshot has std 0 by
# construction and must not be read as "stable".
MIN_VOL_SAMPLES_FOR_STABILITY = 3

TIER_THRESHOLDS = {'elite': 70, 'runner': 50, 'momentum': 30}


def _conn():
    c = sqlite3.connect(DB, timeout=5)
    c.row_factory = sqlite3.Row
    return c


def _get_cols(c, table: str) -> set:
    """Return set of column names for a table. Empty set if table missing."""
    try:
        return {r[1] for r in c.execute(f'PRAGMA table_info({table})').fetchall()}
    except Exception:
        return set()


def ensure_tables():
    """Create score_performance, token_metrics and smart_money_coverage if absent."""
    c = _conn()
    try:
        c.execute("""
            CREATE TABLE IF NOT EXISTS score_performance (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                score_bucket INTEGER UNIQUE,
                trades       INTEGER DEFAULT 0,
                wins         INTEGER DEFAULT 0,
                total_pnl    REAL    DEFAULT 0.0
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS token_metrics (
                token_name           TEXT,
                ts                   REAL,
                holders              INTEGER,
                holders_delta_60s    INTEGER,
                holders_delta_180s   INTEGER,
                unique_wallets_120s  INTEGER,
                wallet_cluster_score REAL,
                volume               REAL,
                volume_std_5m        REAL,
                top10_sell_ratio     REAL,
                price                REAL,
                smart_money_score    INTEGER DEFAULT 0,
                tier                 TEXT DEFAULT 'NOISE',
                PRIMARY KEY (token_name, ts)
            )
        """)
        # Additive coverage columns on token_metrics.  ALTER is used rather than
        # a positional INSERT so a drifted pre-existing table cannot silently
        # misalign values into the wrong columns (a real risk in the previous
        # revision, which used INSERT OR REPLACE ... VALUES with 13 positions).
        existing = _get_cols(c, 'token_metrics')
        for col, ddl in (
            ('coverage_pct',        'REAL'),
            ('components_observed', 'TEXT'),
            ('components_missing',  'TEXT'),
            ('measured',            'INTEGER DEFAULT 0'),
            ('provenance',          'TEXT'),
        ):
            if col not in existing:
                try:
                    c.execute(f'ALTER TABLE token_metrics ADD COLUMN {col} {ddl}')
                except Exception:
                    pass

        # Coverage ledger: one row per evaluation attempt, including the ones
        # that could not be scored.  This is the Bundle A measurement surface —
        # it makes producer starvation countable instead of invisible.
        c.execute("""
            CREATE TABLE IF NOT EXISTS smart_money_coverage (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                token               TEXT,
                ts                  REAL,
                measured            INTEGER DEFAULT 0,
                coverage_pct        REAL,
                observed_weight     INTEGER,
                total_weight        INTEGER,
                components_observed TEXT,
                components_missing  TEXT,
                raw_score           INTEGER,
                normalised_score    INTEGER,
                tier                TEXT,
                reason              TEXT,
                provenance          TEXT
            )
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_smc_ts ON smart_money_coverage(ts)
        """)
        c.commit()
    except Exception:
        pass
    finally:
        try:
            c.close()
        except Exception:
            pass


def _record_coverage(token, measured, coverage, observed_w, comps_obs,
                     comps_missing, raw, norm, tier, reason, provenance):
    """Best-effort coverage telemetry. Never raises, never blocks scoring."""
    try:
        c = _conn()
        c.execute("""
            INSERT INTO smart_money_coverage
                (token, ts, measured, coverage_pct, observed_weight, total_weight,
                 components_observed, components_missing, raw_score,
                 normalised_score, tier, reason, provenance)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (token, time.time(), 1 if measured else 0, round(coverage, 4),
              observed_w, _TOTAL_WEIGHT, ','.join(sorted(comps_obs)),
              ','.join(sorted(comps_missing)), raw, norm, tier, reason,
              provenance))
        c.commit()
        c.close()
    except Exception:
        pass


def classify(score) -> str:
    """Tier from a normalised 0-100 score. None/unmeasured never becomes NOISE."""
    if score is None:
        return 'NOT_MEASURED'
    if score >= TIER_THRESHOLDS['elite']:
        return 'ELITE_RUNNER'
    if score >= TIER_THRESHOLDS['runner']:
        return 'RUNNER'
    if score >= TIER_THRESHOLDS['momentum']:
        return 'MOMENTUM'
    return 'NOISE'


def compute_metrics(token: str) -> dict | None:
    """
    Compute smart money metrics for a token.

    Returns a dict with a normalised 0-100 'score' and 'tier' when enough
    component inputs were genuinely observed, otherwise returns None.

    Returning None is deliberate and load-bearing: the market_intelligence
    call site guards on `if _sm_result:`, so None leaves smart_money_score /
    smart_money_tier absent from the metrics dict, and tx_resolver's
    _smart_money_score() then takes its neutral (0.50, produced=False)
    branch instead of consuming a starved score as though it were evidence.
    """
    c = _conn()
    now = time.time()

    observed_components: set[str] = set()
    missing_components: set[str] = set()
    provenance_bits: list[str] = []
    raw_points = 0

    def _abort(reason: str):
        try:
            c.close()
        except Exception:
            pass
        _record_coverage(token, False, 0.0, 0, observed_components,
                         set(_COMPONENT_WEIGHTS) - observed_components,
                         None, None, 'NOT_MEASURED', reason,
                         ';'.join(provenance_bits))
        return None

    # ── PRAGMA check market_snapshots columns ─────────────────────────────────
    ms_cols = _get_cols(c, 'market_snapshots')
    if not ms_cols:
        return _abort('NO_MARKET_SNAPSHOTS_TABLE')

    if 'holder_count' in ms_cols:
        holder_col = 'holder_count'
    elif 'holders' in ms_cols:
        holder_col = 'holders'
    else:
        holder_col = None

    price_col = 'observed_price' if 'observed_price' in ms_cols else None

    # SIGNOFF_SMART_MONEY_VOLUME_CONTRACT_20260813:
    # market_intelligence persists the canonical 5-minute USD volume as
    # market_snapshots.vol_5m_usd (and carries volume_5m_usd in-memory for
    # calibration).  The smart-money scorer previously recognised only the
    # legacy volume_usd / volume names, so genuine observed volume was silently
    # classified as missing.  Prefer the live schema contract first, retain
    # backwards-compatible aliases, and never fabricate a value.
    if 'vol_5m_usd' in ms_cols:
        vol_col = 'vol_5m_usd'
    elif 'volume_5m_usd' in ms_cols:
        vol_col = 'volume_5m_usd'
    elif 'volume_usd' in ms_cols:
        vol_col = 'volume_usd'
    elif 'volume' in ms_cols:
        vol_col = 'volume'
    else:
        vol_col = None

    for ts_candidate in ('price_updated_at', 'created_at', 'timestamp'):
        if ts_candidate in ms_cols:
            ts_col = ts_candidate
            break
    else:
        ts_col = None

    if not ts_col:
        return _abort('NO_TIMESTAMP_COLUMN')

    # ── Fetch latest snapshot ─────────────────────────────────────────────────
    select_cols = [f'COALESCE({ts_col}, 0) AS snap_ts']
    if holder_col:
        # NULL is preserved as NULL here.  COALESCE(...,0) was the original
        # defect: it converted "no producer" into an apparent zero holders.
        select_cols.append(f'{holder_col} AS holders_now')
    if price_col:
        select_cols.append(f'COALESCE({price_col}, 0) AS price')
    if vol_col:
        select_cols.append(f'COALESCE({vol_col}, 0) AS vol_now')

    try:
        snap = c.execute(
            f"SELECT {', '.join(select_cols)} FROM market_snapshots "
            f"WHERE mint_address=? OR token_name=? "
            f"ORDER BY {ts_col} DESC LIMIT 1",
            (token, token)
        ).fetchone()
    except Exception as exc:
        return _abort(f'SNAPSHOT_QUERY_ERROR:{type(exc).__name__}')

    if not snap:
        return _abort('NO_SNAPSHOT_ROW')

    keys = snap.keys()
    price = float(snap['price']) if 'price' in keys and snap['price'] is not None else 0.0
    vol_now = float(snap['vol_now']) if 'vol_now' in keys and snap['vol_now'] is not None else 0.0

    holders_now = None
    if 'holders_now' in keys and snap['holders_now'] is not None:
        try:
            holders_now = int(snap['holders_now'])
        except Exception:
            holders_now = None

    # ── Holder deltas (components: holder_growth_180s, holder_momentum_60s) ───
    h_60 = None
    h_180 = None
    if holder_col and holders_now is not None and holders_now > 0:
        def holders_at(secs):
            try:
                r = c.execute(
                    f"SELECT {holder_col} FROM market_snapshots "
                    f"WHERE (mint_address=? OR token_name=?) AND {ts_col} < ? "
                    f"  AND {holder_col} IS NOT NULL "
                    f"ORDER BY {ts_col} DESC LIMIT 1",
                    (token, token, now - secs)
                ).fetchone()
                return int(r[0]) if r and r[0] is not None else None
            except Exception:
                return None

        prior_60 = holders_at(60)
        prior_180 = holders_at(180)
        if prior_60 is not None:
            h_60 = holders_now - prior_60
        if prior_180 is not None:
            h_180 = holders_now - prior_180
        provenance_bits.append(f'holders={holder_col}')

    if h_180 is not None:
        observed_components.add('holder_growth_180s')
        if h_180 > 50:
            raw_points += 25
        elif h_180 > 25:
            raw_points += 15
        elif h_180 > 10:
            raw_points += 5
    else:
        missing_components.add('holder_growth_180s')

    if h_60 is not None:
        observed_components.add('holder_momentum_60s')
        if h_60 > 20:
            raw_points += 15
        elif h_60 > 10:
            raw_points += 8
    else:
        missing_components.add('holder_momentum_60s')

    # ── Volume history (components: volume_stability, volume_magnitude) ───────
    vol_total = vol_now
    vol_std = None
    vol_samples = 0
    if vol_col:
        try:
            vols = [float(r[0] or 0) for r in c.execute(
                f"SELECT {vol_col} FROM market_snapshots "
                f"WHERE (mint_address=? OR token_name=?) AND {ts_col} > ?",
                (token, token, now - 300)
            ).fetchall()]
            vol_samples = len(vols)
            if vols:
                vol_total = sum(vols)
                mean = vol_total / vol_samples
                vol_std = (sum((v - mean) ** 2 for v in vols) / vol_samples) ** 0.5
                provenance_bits.append(f'vol={vol_col}/{vol_samples}s')
        except Exception:
            vol_samples = 0
            vol_std = None

    # Stability is only meaningful with a real sample.  One snapshot yields
    # std == 0 by construction and previously earned a free +15.
    if vol_std is not None and vol_samples >= MIN_VOL_SAMPLES_FOR_STABILITY and vol_total > 0:
        observed_components.add('volume_stability')
        if vol_std < 0.2 * vol_total:
            raw_points += 15
        elif vol_std < 0.5 * vol_total:
            raw_points += 8
    else:
        missing_components.add('volume_stability')

    if vol_col and vol_samples > 0:
        observed_components.add('volume_magnitude')
        if vol_total > 50000:
            raw_points += 10
        elif vol_total > 10000:
            raw_points += 5
    else:
        missing_components.add('volume_magnitude')

    # ── Wallet cluster (component: wallet_cluster) ───────────────────────────
    wallets = None
    wpo_cols = _get_cols(c, 'wallet_pattern_observations')
    if 'wallet_address' in wpo_cols and 'observed_at' in wpo_cols:
        try:
            wallets = int(c.execute(
                "SELECT COUNT(DISTINCT wallet_address) FROM wallet_pattern_observations "
                "WHERE mint_address=? AND observed_at > ?",
                (token, now - 120)
            ).fetchone()[0] or 0)
            provenance_bits.append('cluster=wallet_pattern_observations')
        except Exception:
            wallets = None

    # An observed count of zero IS evidence (the table was queryable and the
    # window was empty); an unqueryable table is not.
    if wallets is not None:
        observed_components.add('wallet_cluster')
        if wallets >= 10:
            raw_points += 20
        elif wallets >= 5:
            raw_points += 12
        elif wallets >= 3:
            raw_points += 6
    else:
        missing_components.add('wallet_cluster')

    cluster_score = float(wallets) if wallets is not None else 0.0

    # ── Sell pressure (component: sell_pressure) ─────────────────────────────
    sell_ratio = None
    wwl_cols = _get_cols(c, 'wallet_write_log')
    if 'action' in wwl_cols and 'amount_sol' in wwl_cols:
        try:
            row = c.execute(
                "SELECT "
                "  COALESCE(SUM(CASE WHEN action='sell' THEN amount_sol END),0), "
                "  COALESCE(SUM(CASE WHEN action='buy'  THEN amount_sol END),0), "
                "  COUNT(*) "
                "FROM wallet_write_log WHERE mint_address=?",
                (token,)
            ).fetchone()
            sells = float(row[0] or 0.0)
            buys = float(row[1] or 0.0)
            n_rows = int(row[2] or 0)
            # No rows, or no buy-side denominator, means UNMEASURED — not
            # "zero selling".  The previous revision divided by a 0.001 floor
            # and awarded the full +15 for silence.
            if n_rows > 0 and buys > 0:
                sell_ratio = sells / buys
                provenance_bits.append(f'sell=wallet_write_log/{n_rows}r')
        except Exception:
            sell_ratio = None

    if sell_ratio is not None:
        observed_components.add('sell_pressure')
        if sell_ratio < 0.3:
            raw_points += 15
        elif sell_ratio < 0.6:
            raw_points += 8
    else:
        missing_components.add('sell_pressure')

    # ── Coverage gate and renormalisation ────────────────────────────────────
    observed_weight = sum(_COMPONENT_WEIGHTS[k] for k in observed_components)
    coverage = observed_weight / float(_TOTAL_WEIGHT) if _TOTAL_WEIGHT else 0.0
    provenance = ';'.join(provenance_bits) or 'none'

    if coverage < MIN_COVERAGE_FRACTION or observed_weight <= 0:
        try:
            c.close()
        except Exception:
            pass
        # EDGE_AUDIT_20260815 — STRUCTURAL vs INCIDENTAL SHORTFALL.
        # A bare COVERAGE_BELOW_FLOOR reads as bad luck on this token. In the
        # 2026-08-15 window it was never bad luck: holder_growth_180s (25) and
        # holder_momentum_60s (15) are 40 of 100 weight and read holder counts
        # that market_intelligence.py never writes (":1020" and ":1309" both
        # set "holder_count": None). Naming the permanently-missing components
        # separates "this token is thin" from "this metric cannot ever fire".
        # The floor is NOT relaxed: a starved score is worse than no score.
        _structural = sorted(missing_components & {'holder_growth_180s',
                                                   'holder_momentum_60s'})
        _ceiling = (_TOTAL_WEIGHT - sum(_COMPONENT_WEIGHTS[k]
                                        for k in _structural)) / float(_TOTAL_WEIGHT)
        _reason = f'COVERAGE_BELOW_FLOOR_{coverage:.2f}'
        if _structural:
            _reason += (f'|STRUCTURAL_ABSENT={",".join(_structural)}'
                        f'|CEILING={_ceiling:.2f}|FLOOR={MIN_COVERAGE_FRACTION:.2f}')
        _record_coverage(token, False, coverage, observed_weight,
                         observed_components, missing_components,
                         raw_points, None, 'NOT_MEASURED',
                         _reason, provenance)
        return None

    # Renormalise onto the 0-100 contract so tiers stay reachable and a
    # partially-covered token is judged on the evidence it actually has.
    normalised = int(round(raw_points * (_TOTAL_WEIGHT / float(observed_weight))))
    normalised = max(0, min(_TOTAL_WEIGHT, normalised))
    tier = classify(normalised)

    _record_coverage(token, True, coverage, observed_weight,
                     observed_components, missing_components,
                     raw_points, normalised, tier, 'MEASURED', provenance)

    # ── Persist (named columns; never positional) ────────────────────────────
    try:
        c.execute("""
            INSERT OR REPLACE INTO token_metrics
                (token_name, ts, holders, holders_delta_60s, holders_delta_180s,
                 unique_wallets_120s, wallet_cluster_score, volume, volume_std_5m,
                 top10_sell_ratio, price, smart_money_score, tier,
                 coverage_pct, components_observed, components_missing,
                 measured, provenance)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (token, now, holders_now, h_60, h_180,
              wallets, cluster_score, vol_total, vol_std,
              sell_ratio, price, normalised, tier,
              round(coverage, 4), ','.join(sorted(observed_components)),
              ','.join(sorted(missing_components)), 1, provenance))
        c.commit()
    except Exception:
        pass

    try:
        c.close()
    except Exception:
        pass

    return {
        'token': token,
        'score': normalised,
        'tier': tier,
        'measured': True,
        'coverage_pct': round(coverage, 4),
        'components_observed': sorted(observed_components),
        'components_missing': sorted(missing_components),
        'raw_points': raw_points,
        'provenance': provenance,
        # Component values.  None means NOT MEASURED — callers must not
        # coerce these to 0.
        'holders_now': holders_now,
        'holders_delta_60s': h_60,
        'holders_delta_180s': h_180,
        'wallet_cluster_score': cluster_score if wallets is not None else None,
        'top10_sell_ratio': sell_ratio,
        'volume': vol_total,
        'volume_std_5m': vol_std,
        'price': price,
    }


def get_score(token: str) -> dict:
    """
    Latest cached score, or a fresh computation.

    Absence is reported as NOT_MEASURED with score None.  It is never
    reported as score 0 / NOISE, which would be indistinguishable from a
    genuinely bad token.
    """
    unmeasured = {'token': token, 'score': None, 'tier': 'NOT_MEASURED',
                  'measured': False, 'coverage_pct': 0.0}
    try:
        c = _conn()
        cols = _get_cols(c, 'token_metrics')
        want = ['smart_money_score', 'tier', 'holders_delta_60s',
                'holders_delta_180s', 'wallet_cluster_score',
                'top10_sell_ratio', 'volume', 'ts']
        for opt in ('coverage_pct', 'measured', 'provenance'):
            if opt in cols:
                want.append(opt)
        sel = ', '.join(w for w in want if w in cols)
        if not sel:
            c.close()
            return compute_metrics(token) or unmeasured
        r = c.execute(
            f"SELECT {sel} FROM token_metrics WHERE token_name=? "
            f"ORDER BY ts DESC LIMIT 1", (token,)
        ).fetchone()
        c.close()
        if r and (time.time() - float(r['ts'] or 0)) < 60:
            out = dict(r)
            out['score'] = out.pop('smart_money_score', None)
            out['measured'] = bool(out.get('measured', 1))
            return out
    except Exception:
        try:
            c.close()
        except Exception:
            pass
    return compute_metrics(token) or unmeasured


def bucket_score(score) -> int | None:
    """Map score to nearest 10-point bucket. None stays None."""
    if score is None:
        return None
    return int(score // 10) * 10


def record_trade_outcome(score, pnl: float) -> None:
    """
    Called when a position closes — updates score_performance.

    Unmeasured tokens are skipped rather than folded into bucket 0, which
    would contaminate the 0-9 bucket with tokens that were never scored.
    """
    bucket = bucket_score(score)
    if bucket is None:
        return
    try:
        c = _conn()
        win = 1 if pnl > 0 else 0
        c.execute("""
            INSERT INTO score_performance (score_bucket, trades, wins, total_pnl)
            VALUES (?, 1, ?, ?)
            ON CONFLICT(score_bucket) DO UPDATE SET
                trades    = trades + 1,
                wins      = wins + excluded.wins,
                total_pnl = total_pnl + excluded.total_pnl
        """, (bucket, win, pnl))
        c.commit()
        c.close()
    except Exception:
        pass


def get_best_thresholds() -> dict:
    """Return dynamically tuned thresholds from trade performance data."""
    defaults = dict(TIER_THRESHOLDS)
    try:
        c = _conn()
        rows = c.execute("""
            SELECT score_bucket,
                   trades,
                   wins * 1.0 / trades AS win_rate,
                   total_pnl / trades  AS avg_pnl
            FROM score_performance
            WHERE trades >= 5
            ORDER BY avg_pnl DESC
        """).fetchall()
        c.close()
        if len(rows) >= 2:
            defaults['elite'] = int(rows[0][0])
            defaults['runner'] = int(rows[1][0])
        return defaults
    except Exception:
        return defaults


def coverage_report(window_seconds: float = 3600.0) -> dict:
    """
    Bundle A evidence surface.

    Returns measured/unmeasured counts and per-component observation rates
    over the window, so producer starvation is countable before any threshold
    or enforcement decision is taken.  Read-only; safe to call from UI.
    """
    out = {'window_seconds': window_seconds, 'evaluations': 0, 'measured': 0,
           'unmeasured': 0, 'component_observed_rate': {}, 'reasons': {}}
    try:
        c = _conn()
        since = time.time() - window_seconds
        rows = c.execute(
            "SELECT measured, components_observed, reason FROM smart_money_coverage "
            "WHERE ts > ?", (since,)
        ).fetchall()
        c.close()
        out['evaluations'] = len(rows)
        counts = {k: 0 for k in _COMPONENT_WEIGHTS}
        for r in rows:
            if int(r[0] or 0):
                out['measured'] += 1
            else:
                out['unmeasured'] += 1
            for comp in str(r[1] or '').split(','):
                if comp in counts:
                    counts[comp] += 1
            reason = str(r[2] or 'UNKNOWN')
            out['reasons'][reason] = out['reasons'].get(reason, 0) + 1
        n = max(1, len(rows))
        out['component_observed_rate'] = {k: round(v / n, 4) for k, v in counts.items()}
    except Exception:
        pass
    return out


# Ensure tables exist on import
try:
    ensure_tables()
except Exception:
    pass