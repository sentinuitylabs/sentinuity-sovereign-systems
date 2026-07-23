"""
ui/cinematic_overlay.py
========================
EXPRESSION LAYER — makes the organism understandable by watching.

Two non-blocking @st.fragment components:

1. render_cinematic_overlay()
   Shows one high-signal event at a time. Auto-fades 4s.
   Never blocks UI. Never full-screen.
   Sources: cognition_log, polaris_proposals, code_patches

2. render_lifecycle_visual()
   Shows open position lifecycle states with pnl + trailing stop.
   Sources: paper_positions, cognition_log

Performance rules (LOCKED):
  - @st.fragment only
  - DB timeout <= 2s
  - busy_timeout <= 2000
  - READ-ONLY — zero writes
  - No polling loops
  - No full-screen blackout
  - Fail silently — never crash parent page
"""
from __future__ import annotations
import time
import sqlite3
import streamlit as st
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "sentinuity_matrix.db"


# ── TRUTH FABRIC RAIN LIVE FEED — SIGNOFF_MATRIX_LIVE_TELEMETRY_20260718 ─────
# The rain previously carried only a fixed allow-listed vocabulary, which read
# as static/stale. It now also carries the machine's ACTUAL recent operating
# telemetry — sanitised cognition-log lines, live heartbeat ages, and gate
# counters — so the fabric visibly IS the running system. Doctrine:
#   * read-only URI connection, 1.5s timeout, zero writes, total fail-safety
#     (any error returns empty lists and the fixed vocabulary still renders);
#   * NOTHING sensitive can reach the canvas: lines mentioning keys/secrets/
#     tokens/passwords/seeds are dropped outright; base58 wallet/mint addresses
#     and long hex are masked to a 4-char stub; URLs are stripped;
#   * strings are length-capped and drawn from stage/service/counter fields
#     only — never env, never .env, never config VALUES (config keys only).
import re as _re

_RAIN_SENSITIVE = _re.compile(
    r"(?i)(api[\s_-]?key|secret|token|bearer|password|passphrase|"
    r"private[\s_-]?key|seed|mnemonic|authorization|credential|signature=)")
_RAIN_B58 = _re.compile(r"[1-9A-HJ-NP-Za-km-z]{28,}")
_RAIN_HEX = _re.compile(r"(?:0x)?[0-9a-fA-F]{24,}")
_RAIN_URL = _re.compile(r"https?://\S+")


def _rain_sanitize(text: str, cap: int = 46, min_len: int = 6) -> str | None:
    """Return a display-safe string or None if the line must not render."""
    try:
        s = str(text or "").strip()
        if not s:
            return None
        if _RAIN_SENSITIVE.search(s):
            return None                      # drop, never mask, key-ish lines
        s = _RAIN_URL.sub("", s)
        s = _RAIN_B58.sub(lambda m: m.group(0)[:4] + "…", s)
        s = _RAIN_HEX.sub(lambda m: m.group(0)[:6] + "…", s)
        s = _re.sub(r"\s+", " ", s).strip(" -·|")
        if len(s) < max(1, int(min_len)):
            return None
        return s[:cap]
    except Exception:
        return None


def _rain_live_feed() -> tuple[list[str], list[str]]:
    """(ordinary, authority) live strings for the rain. Fail-safe: ([],[])."""
    ordinary: list[str] = []
    authority: list[str] = []
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=1.5)
        conn.execute("PRAGMA busy_timeout=900")
        conn.row_factory = sqlite3.Row
        now = time.time()
        # 1) Recent cognition — the machine narrating its own work.
        try:
            for r in conn.execute(
                "SELECT stage, message FROM cognition_log "
                "ORDER BY rowid DESC LIMIT 36"):
                stage = _rain_sanitize(r["stage"], 14, min_len=2) or "SYS"
                msg = _rain_sanitize(r["message"], 40)
                if msg:
                    up = str(r["stage"] or "").upper()
                    realm = ("AUTH" if up in ("EXECUTOR", "GUARDIAN", "SYSTEM", "GATE")
                             else "BUILD" if up in ("FORGE", "COUNCIL", "RESEARCH", "DEBATE", "POLARIS")
                             else "INTEL" if up in ("ORACLE", "INGEST", "QUALIFIER", "SCOUT", "RESOLVER")
                             else "FLOW")
                    line = f"{realm}:{stage}::{msg}"
                    (authority if realm == "AUTH" else ordinary).append(line)
        except Exception:
            pass
        # 2) Live service heartbeats — the pulse itself, with real ages.
        try:
            for r in conn.execute(
                "SELECT service_name, last_pulse FROM system_heartbeat"):
                svc = _rain_sanitize(r["service_name"], 28, min_len=3)
                p = float(r["last_pulse"] or 0)
                if svc and p > 0:
                    ordinary.append(f"INTEL:hb.{svc}={max(0, int(now - p))}s")
        except Exception:
            pass
        # 3) Gate counters — the flow the constellation measures.
        try:
            for label, sql in (
                ("snapshots.priced_10m",
                 "SELECT COUNT(*) FROM market_snapshots WHERE price_status='priced' "
                 "AND COALESCE(price_updated_at,0)>?"),
                ("snapshots.qualified_10m",
                 "SELECT COUNT(*) FROM market_snapshots WHERE quality_status='qualified' "
                 "AND COALESCE(price_updated_at,0)>?"),
            ):
                n = conn.execute(sql, (now - 600,)).fetchone()[0]
                ordinary.append(f"FLOW:{label}={int(n or 0)}")
            n_open = conn.execute(
                "SELECT COUNT(*) FROM paper_positions WHERE status='OPEN'"
            ).fetchone()[0]
            ordinary.append(f"FLOW:paper_positions.open={int(n_open or 0)}")
        except Exception:
            pass
        # 4) Executor decision contract — authority-grade truth, verbatim state.
        try:
            r = conn.execute(
                "SELECT verdict, pattern_state, created_at FROM "
                "live_decision_contract ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if r:
                v = _rain_sanitize(r["verdict"], 24, min_len=3)
                ps = _rain_sanitize(r["pattern_state"], 16, min_len=3)
                age = max(0, int(now - float(r["created_at"] or now)))
                if v:
                    authority.append(f"AUTH:CONTRACT.{v}·{age}s")
                if ps:
                    authority.append(f"AUTH:PATTERN.{ps}")
        except Exception:
            pass
        conn.close()
    except Exception:
        return [], []
    # De-dup, hard-cap payload so the injected HTML stays small.
    def _cap(xs: list[str], n: int) -> list[str]:
        seen, out = set(), []
        for x in xs:
            if x not in seen:
                seen.add(x)
                out.append(x)
            if len(out) >= n:
                break
        return out
    return _cap(ordinary, 44), _cap(authority, 12)


def inject_holographic_substrate_rain() -> None:
    """Inject the single canonical vertical Sentinuity Truth Fabric rain.

    SIGNOFF_MATRIX_LIVE_TELEMETRY_20260718: the rain now streams the machine's
    own sanitised operating telemetry (see _rain_live_feed) merged with the
    fixed contract vocabulary, at official-Matrix density: adjacent columns,
    tight line pitch, crisp glyphs (no canvas filter blur), gold leading glyph.
    Streamlit reruns refresh the payload, so the fabric tracks the live system.
    This replacement actively removes old horizontal/diagonal watermark DOM,
    CSS and rerun leftovers before installing one fixed vertical-only canvas.
    """
    try:
        import json as _rj
        import streamlit.components.v1 as components
        _live_ord, _live_auth = _rain_live_feed()
        _payload = (_RAIN_JS
                    .replace("__LIVE_ORDINARY__", _rj.dumps(_live_ord))
                    .replace("__LIVE_AUTHORITY__", _rj.dumps(_live_auth)))
        components.html(_payload, height=0, width=0)
    except Exception:
        pass


_RAIN_JS = r"""
<script>
(() => {
  try {
    const doc = window.parent.document;
    const win = window.parent;
    const ID = 'snty-holographic-substrate-rain';
    const STYLE_ID = 'snty-holographic-substrate-style';
    const GUARD_ID = 'snty-horizontal-watermark-guard';

    const legacySelectors = [
      '.snty-v2-matrix',
      '.snty-matrix-watermark',
      '.matrix-watermark',
      '.matrix-drift',
      '.matrix-overlay',
      '.cinematic-watermark',
      '.truth-watermark',
      '[class*="matrix-drift"]',
      '[id*="matrix-drift"]',
      '[class*="matrix-watermark"]',
      '[id*="matrix-watermark"]',
      '[class*="horizontal-watermark"]',
      '[id*="horizontal-watermark"]'
    ].join(',');

    const legacyText = /(?:STATE\s*::\s*)?(?:BLOCKED|READY|STALE|ALIVE)?\s*[·|: -]*\s*RUNTIME\s*[·|: -]*\s*TRUTH/i;

    function removeLegacy(root = doc) {
      try {
        root.querySelectorAll(legacySelectors).forEach(el => el.remove());
      } catch (_) {}

      // Catch anonymous legacy nodes whose class/id changed between builds.
      try {
        root.querySelectorAll('div,span,p,section,aside').forEach(el => {
          if (el.id === ID || el.closest(`#${ID}`)) return;
          const text = String(el.textContent || '').replace(/\s+/g, ' ').trim();
          if (!text || text.length > 180 || !legacyText.test(text)) return;

          const cs = win.getComputedStyle(el);
          const transform = String(cs.transform || '');
          const animation = String(cs.animationName || '');
          const position = String(cs.position || '');
          const wide = el.scrollWidth > win.innerWidth * 0.75;
          const moving = animation !== 'none' || (transform && transform !== 'none');
          const overlay = position === 'fixed' || position === 'absolute';

          if (wide || moving || overlay) el.remove();
        });
      } catch (_) {}
    }

    removeLegacy();

    // Remove known legacy keyframes/rules. Iterate backwards to avoid index drift.
    try {
      [...doc.styleSheets].forEach(sheet => {
        try {
          const rules = [...(sheet.cssRules || [])];
          for (let i = rules.length - 1; i >= 0; i--) {
            const text = String(rules[i].cssText || '');
            if (
              /sntyMatrixDrift|matrix-drift|matrix-watermark|horizontal-watermark/i.test(text) ||
              /translateX\s*\(/i.test(text) && /RUNTIME|TRUTH|matrix/i.test(text) ||
              /rotate\s*\(\s*-?3deg\s*\)/i.test(text)
            ) {
              sheet.deleteRule(i);
            }
          }
        } catch (_) {}
      });
    } catch (_) {}

    const old = doc.getElementById(ID);
    if (old) old.remove();
    const oldStyle = doc.getElementById(STYLE_ID);
    if (oldStyle) oldStyle.remove();

    const priorGuard = win[GUARD_ID];
    if (priorGuard && typeof priorGuard.disconnect === 'function') {
      priorGuard.disconnect();
    }

    const style = doc.createElement('style');
    style.id = STYLE_ID;
    style.textContent = `
      ${legacySelectors}{
        display:none!important;
        visibility:hidden!important;
        opacity:0!important;
        animation:none!important;
        transform:none!important;
        pointer-events:none!important;
      }
      #${ID}{
        position:fixed;
        inset:0;
        width:100vw;
        height:100vh;
        z-index:0;
        pointer-events:none;
        opacity:.22;                  /* RAIN_CRISP_20260718: screen blend removed — it washed glyphs into the gradient and read as blur */
        mix-blend-mode:normal;
      }
      [data-testid="stAppViewContainer"]{position:relative;isolation:isolate;}
      [data-testid="stAppViewContainer"] > section,
      [data-testid="stAppViewContainer"] main{position:relative;z-index:1;}
      @media(max-width:760px){#${ID}{opacity:.15;}}
      @media(prefers-reduced-motion:reduce){#${ID}{display:none!important;}}
    `;
    doc.head.appendChild(style);

    // Keep removing stale overlay nodes inserted later by Streamlit reruns.
    const observer = new MutationObserver(mutations => {
      for (const mutation of mutations) {
        for (const node of mutation.addedNodes || []) {
          if (node && node.nodeType === 1) removeLegacy(node);
        }
      }
    });
    observer.observe(doc.body, {childList:true, subtree:true});
    win[GUARD_ID] = observer;

    const canvas = doc.createElement('canvas');
    canvas.id = ID;
    canvas.setAttribute('aria-hidden', 'true');
    const host = doc.querySelector('[data-testid="stAppViewContainer"]') || doc.body;
    host.prepend(canvas);

    const ctx = canvas.getContext('2d', {alpha:true});
    if (!ctx) return;

    const ordinaryStatic = [
      'services.execution_engine.scan_for_entries',
      'services.live_decision_contract.publish_executor_contract',
      'services.pattern_live_arming.evaluate_pattern_permission',
      'core.outcome_taxonomy.classify_realized_pnl',
      'services.live_trading._execute_jupiter_swap',
      'services.live_settlement_recovery.reconcile_pending',
      'services.price_router.get_authoritative_price',
      'services.ws_price_oracle.publish_heartbeat',
      'services.system_guardian.claim_restart',
      'services.neural_supervisor.evaluate_candidate',
      'services.wallet_scout.get_active_wallets',
      'services.smart_wallet_hub.rank_wallet_profiles',
      'services.copytrade_influence.evaluate_signal',
      'services.macro_channel.evaluate_channel',
      'services.substrate_paper_trader.open_position',
      'services.council_execution_spine.run_cycle',
      'ui.state_contract.load_sovereign_truth',
      'ui.live_gate_constellation.render_live_gate_constellation',
      'mode_b_decision_ledger.snapshot_id',
      'market_snapshots.cluster_id',
      'paper_positions.realized_pnl_usd',
      'live_tx_ledger.quote_age_before_broadcast_sec',
      'system_heartbeat.last_pulse',
      'candidate_price_ts',
      'settlement_reconciled',
      'paper_shadow_twin',
      'oracle_authority',
      'capital_deployment_sequence'
    ];
    const authorityStatic = [
      'FINAL_FIRE_AUTHORITY.SEALED',
      'EXECUTOR_PUBLICATION.FRESH',
      'PATTERN_PERMISSION.CONFIRMED',
      'LIVE_LANE_ARMED.TRUE',
      'CAPACITY_AUTHORITY.PASS',
      'PRICE_TRUTH.ALIVE',
      'SETTLEMENT_TRUTH.RESOLVED',
      'EXEC_READY.TRUE'
    ];
    // SIGNOFF_MATRIX_LIVE_TELEMETRY_20260718: sanitised live operating
    // telemetry injected server-side. Live lines take priority so the fabric
    // reads as the running machine; the fixed vocabulary remains as substrate.
    const liveOrdinary  = __LIVE_ORDINARY__;
    const liveAuthority = __LIVE_AUTHORITY__;
    const ordinary  = liveOrdinary.concat(liveOrdinary, ordinaryStatic);
    const authority = liveAuthority.concat(authorityStatic);

    const palette = {
      green:[20,241,149],
      cyan:[142,249,255],
      violet:[153,69,255],
      gold:[255,215,0]
    };

    let columns = [], width = 0, height = 0, dpr = 1, last = 0;
    const fpsInterval = 1000 / 30;             /* smoother official cadence */
    const rgba = (rgb,a) => `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${a})`;

    function makeColumn(index, fontSize) {
      const authorityStream = Math.random() < .10;
      const source = authorityStream ? authority : ordinary;
      const text = source[(Math.random() * source.length) | 0];
      return {
        x:index * fontSize,
        y:-Math.random() * height,
        speed:.85 + Math.random() * 1.55,
        chars:String(text).replace(/\s+/g, '_').split(''),
        authority:authorityStream,
        realm:String(text).split(':',1)[0]
      };
    }

    function resize() {
      width = Math.max(1, win.innerWidth);
      height = Math.max(1, win.innerHeight);
      dpr = Math.min(win.devicePixelRatio || 1, 2);   /* crisp glyphs */
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      canvas.style.width = width + 'px';
      canvas.style.height = height + 'px';
      ctx.setTransform(dpr,0,0,dpr,0,0);
      const fontSize = width < 760 ? 12 : 14;
      columns = Array.from(
        {length:Math.ceil(width / fontSize)},
        (_, i) => makeColumn(i, fontSize)
      );
    }

    function draw(now) {
      win.requestAnimationFrame(draw);
      if (doc.hidden || now - last < fpsInterval) return;
      last = now;

      const fontSize = width < 760 ? 12 : 14;
      const lineHeight = fontSize;               /* tight official pitch */

      ctx.fillStyle = 'rgba(4,0,9,0.40)';   /* RAIN_CRISP_20260718: faster trail decay = shorter ghost, sharper glyphs */
      ctx.fillRect(0,0,width,height);
      ctx.font = `600 ${fontSize}px JetBrains Mono, Share Tech Mono, monospace`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';

      for (const col of columns) {
        const len = col.chars.length;
        for (let i = 0; i < len; i++) {
          const y = col.y - i * lineHeight;
          if (y < -lineHeight || y > height + lineHeight) continue;

          const depth = i / Math.max(1, len - 1);
          const trail = 1 - depth;
          const isHead = i === 0;

          if (isHead) {
            /* gold leading glyph — sharp, minimal bloom */
            ctx.fillStyle = rgba(palette.gold, col.authority ? 1 : .95);
            ctx.shadowColor = '#FFD700';
            ctx.shadowBlur = col.authority ? 2 : 1;
          } else {
            const realmRgb = col.realm === 'BUILD' ? palette.violet
              : col.realm === 'INTEL' ? palette.cyan
              : col.realm === 'AUTH' ? palette.gold
              : palette.green;
            const rgb = depth > .90 ? palette.violet : realmRgb;
            const base = col.authority ? .14 : .10;
            const gain = col.authority ? .64 : .56;
            ctx.fillStyle = rgba(rgb, base + trail * gain);
            ctx.shadowBlur = 0;
          }
          ctx.fillText(col.chars[i], col.x, Math.round(y));  /* RAIN_CRISP_20260718: integer y kills sub-pixel AA blur */
        }

        // Vertical-only motion. x is immutable.
        col.y += col.speed;
        if (col.y - len * lineHeight > height + 80) {
          const replacement = makeColumn(Math.round(col.x / fontSize), fontSize);
          replacement.x = col.x;
          replacement.y = -40 - Math.random() * height * .45;
          Object.assign(col, replacement);
        }
      }
      ctx.shadowBlur = 0;
    }

    resize();
    win.addEventListener('resize', resize, {passive:true});
    win.requestAnimationFrame(draw);
  } catch (_) {}
})();
</script>
"""


_C = {
    "entry":"#14F195",
    "mode_b":"#00E5FF",
    "bomb":"#FF6B35",
    "forge":"#FFD700",
    "harmonic":"#9945FF",
    "patch":"#4FC3F7",
    "heal":"#76B900",
    "operator":"#FF4444",
    "scale_in":"#14F195",
    "harvest":"#FFD700",
    "scale_out":"#FF4444",
    "running":"#14F195",
    "defending":"#FF9900",
}

_EVENT_MAP = {
    "EXECUTION_OPEN":("ENTRY OPENED","entry",1),
    "MODE_B_PASS":("MODE B FIRE","mode_b",1),
    "BOMB_SIGNATURE":("BOMB SIGNATURE","bomb",1),
    "FORGE_COMPLETE":("MASTERPIECE FORGED","forge",1),
    "HARMONIC_CONVERGENCE":("HARMONIC CONVERGENCE","harmonic",1),
    "PATCH_APPLIED":("PATCH APPLIED","patch",2),
    "AUTO_HEAL":("AUTO-HEAL FIRED","heal",2),
    "GUARDIAN_HEAL":("AUTO-HEAL FIRED","heal",2),
    "HITL_REQUIRED":("OPERATOR REQUIRED","operator",1),
    "FORGE_WRITER":("MASTERPIECE FORGED","forge",2),
    "CODE_PATCH_CREATED":("PATCH APPLIED","patch",2),
}

_LC_MAP = {
    "SCALE_IN":("SCALE IN","scale_in","▲"),
    "PARTIAL_PROFIT":("HARVEST","harvest","◆"),
    "EXIT":("SCALE OUT","scale_out","▼"),
    "HOLD":("RUNNING","running","●"),
    "DEFENDING":("DEFENDING","defending","◉"),
}


def _db_read(sql: str, params: tuple = (), n: int = 1):
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=2.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=2000")
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchmany(n)
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


@st.fragment(run_every=4)
def render_cinematic_overlay() -> None:
    try:
        now = time.time()
        cutoff = now - 30
        rows = _db_read("""
            SELECT stage, message, timestamp
            FROM cognition_log
            WHERE timestamp > ?
            ORDER BY timestamp DESC LIMIT 20
        """, (cutoff,), n=20)

        event = None
        for row in rows:
            stage = (row.get("stage") or "").upper()
            msg = row.get("message") or ""
            if stage in _EVENT_MAP:
                event = (*_EVENT_MAP[stage], row)
                break
            for key, mapping in _EVENT_MAP.items():
                if key in msg.upper() or key in stage:
                    event = (*mapping, row)
                    break
            if event:
                break

        if not event:
            op_rows = _db_read("""
                SELECT proposal_type, created_at
                FROM polaris_proposals
                WHERE status IN ('forge_complete', 'HITL_REQUIRED', 'hitl_pending')
                  AND created_at > ?
                ORDER BY created_at DESC LIMIT 1
            """, (now - 300,), n=1)
            if op_rows:
                event = (
                    "OPERATOR REQUIRED",
                    "operator",
                    1,
                    {
                        "stage":"OPERATOR",
                        "message":"Seal required",
                        "timestamp":op_rows[0]["created_at"],
                    },
                )

        if not event:
            return

        label, color_key, priority, row = event
        color = _C.get(color_key, "#888")
        age = int(now - float(row.get("timestamp") or now))
        msg = (row.get("message") or "")[:80]
        alpha = max(0.3, 1.0 - (age / 30.0))

        st.markdown(
            f"""
            <div style="
                position:relative;
                margin:4px 0;
                padding:10px 16px;
                border-left:3px solid {color};
                border-radius:0 8px 8px 0;
                background:rgba(5,2,16,{0.7 * alpha:.2f});
                opacity:{alpha:.2f};
                font-family:Share Tech Mono,monospace;
                transition:opacity 0.5s;">
              <div style="display:flex;align-items:center;gap:10px;">
                <span style="font-size:10px;font-weight:700;color:{color};
                    letter-spacing:3px;text-shadow:0 0 8px {color}88;">{label}</span>
                <span style="font-size:9px;color:#444;font-family:monospace;">{age}s ago</span>
              </div>
              <div style="font-size:9px;color:#666;font-family:monospace;
                  margin-top:3px;white-space:nowrap;overflow:hidden;
                  text-overflow:ellipsis;">{msg}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    except Exception:
        pass


_EXIT_GEAR_CACHE: dict = {"ts": 0.0, "v": None}

def _exit_gear_cfg() -> dict:
    """Exit thresholds mirrored from current executor/config, cached 60s.
    Tight-runner activation is currently a source constant at +50%; the
    other displayed thresholds are read from system_config."""
    now = time.time()
    if _EXIT_GEAR_CACHE["v"] and now - _EXIT_GEAR_CACHE["ts"] < 60:
        return _EXIT_GEAR_CACHE["v"]
    v = {"hard_stop": 4.0, "runner_at": 20.0, "trail_pct": 10.0,
         "tight_at": 50.0, "tight_pct": 8.0}
    try:
        rows = _db_read(
            "SELECT key, value FROM system_config WHERE key IN "
            "('HARD_STOP_LOSS_PCT','RUNNER_ACTIVATE_PCT','RUNNER_TRAIL_PCT',"
            "'RUNNER_TRAIL_TIGHT_PCT')", n=8)
        m = {r["key"]: float(r["value"]) for r in rows if r.get("value") is not None}
        v["hard_stop"] = abs(m.get("HARD_STOP_LOSS_PCT", v["hard_stop"]))
        v["runner_at"] = m.get("RUNNER_ACTIVATE_PCT", v["runner_at"])
        v["trail_pct"] = m.get("RUNNER_TRAIL_PCT", v["trail_pct"])
        v["tight_pct"] = m.get("RUNNER_TRAIL_TIGHT_PCT", v["tight_pct"])
    except Exception:
        pass
    _EXIT_GEAR_CACHE.update(ts=now, v=v)
    return v


@st.fragment(run_every=20)
def render_lifecycle_visual() -> None:
    try:
        now = time.time()
        positions = _db_read("""
            SELECT id, token_name, entry_price, last_price,
                   position_size_usd, unrealized_pnl_usd,
                   highest_price_seen, opened_at, confidence,
                   peak_confidence, trail_stop_price
            FROM paper_positions
            WHERE status = 'OPEN'
            ORDER BY opened_at DESC LIMIT 5
        """, n=5)

        if not positions:
            return

        lc_actions: dict[str, str] = {}
        lc_rows = _db_read("""
            SELECT message, timestamp FROM cognition_log
            WHERE stage = 'PLI' AND timestamp > ?
            ORDER BY timestamp DESC LIMIT 20
        """, (now - 120,), n=20)

        for lr in lc_rows:
            msg = lr.get("message") or ""
            for action in ("SCALE_IN", "PARTIAL_PROFIT", "EXIT"):
                if action in msg.upper():
                    parts = msg.split()
                    token = parts[-1] if parts else "?"
                    lc_actions[token] = action
                    break

        st.markdown(
            '<div style="font-size:9px;color:#333;font-family:monospace;'
            'letter-spacing:2px;margin:6px 0 4px;">◈ POSITION LIFECYCLE</div>',
            unsafe_allow_html=True,
        )

        for pos in positions:
            token = (pos.get("token_name") or "?")[:12]
            entry = float(pos.get("entry_price") or 0)
            last = float(pos.get("last_price") or entry)
            pnl_usd = float(pos.get("unrealized_pnl_usd") or 0)
            trail = pos.get("trail_stop_price")
            hold_s = int(now - float(pos.get("opened_at") or now))
            pnl_pct = ((last - entry) / entry * 100) if entry > 0 else 0
            conf = float(pos.get("confidence") or 0)
            peak_c = float(pos.get("peak_confidence") or conf)

            lc_action = lc_actions.get(token, "HOLD")
            if lc_action not in _LC_MAP:
                if pnl_pct > 15:
                    lc_action = "PARTIAL_PROFIT"
                elif peak_c > 0 and conf > 0 and (peak_c - conf) > 0.25:
                    lc_action = "EXIT"
                elif pnl_pct < -3:
                    lc_action = "DEFENDING"
                else:
                    lc_action = "HOLD"

            # EXIT_GEAR_TRUTH_20260723 — show which exit governor owns this
            # trade RIGHT NOW, derived from the SAME system_config thresholds
            # evaluate_exit_for_position() reads (no invented heuristics).
            _gcfg = _exit_gear_cfg()
            if pnl_pct <= -_gcfg["hard_stop"]:
                gear, gear_fn, gear_col = "HARD_STOP", "hard_stop_floor", "#E2384D"
            elif pnl_pct >= 100.0:
                gear, gear_fn, gear_col = "MONSTER", "monster_trailing", "#9945FF"
            elif pnl_pct >= _gcfg["tight_at"]:
                gear, gear_fn, gear_col = "RUNNER·TIGHT", f"runner_trail {_gcfg['tight_pct']:.0f}%", "#FFD700"
            elif pnl_pct >= _gcfg["runner_at"]:
                gear, gear_fn, gear_col = "RUNNER", f"runner_trail {_gcfg['trail_pct']:.0f}%", "#FFD700"
            elif trail:
                gear, gear_fn, gear_col = "TRAILING", "trail_stop_latched", "#38E1FF"
            else:
                gear, gear_fn, gear_col = "BASE", "tp/sl/stagnation watch", "#14F195"

            lc_label, lc_col_key, lc_icon = _LC_MAP.get(
                lc_action, ("RUNNING", "running", "●")
            )
            color = _C.get(lc_col_key, "#888")
            pnl_col = "#14F195" if pnl_usd >= 0 else "#FF4444"
            pnl_sign = "+" if pnl_usd >= 0 else ""

            trail_str = ""
            if trail:
                t = float(trail)
                if t > 0 and last > 0:
                    trail_pct = ((last - t) / last * 100)
                    trail_str = f"trail {trail_pct:.1f}%"

            st.markdown(
                f"""
                <div style="display:flex;align-items:center;gap:10px;
                    padding:6px 10px;margin:2px 0;border-radius:6px;
                    border-left:2px solid {color};
                    background:rgba(5,2,16,0.6);
                    font-family:Share Tech Mono,monospace;">
                  <span style="font-size:11px;color:{color};min-width:14px;">{lc_icon}</span>
                  <span style="font-size:10px;color:#ccc;min-width:90px;
                    font-weight:600;letter-spacing:1px;">{token}</span>
                  <span style="font-size:9px;color:{color};min-width:80px;
                    letter-spacing:1px;">{lc_label}</span>
                  <span style="font-size:9px;color:{gear_col};min-width:96px;
                    font-weight:700;letter-spacing:1px;border:1px solid {gear_col}44;
                    border-radius:4px;padding:1px 5px;
                    background:rgba(5,2,16,0.7);">⚙ {gear}</span>
                  <span style="font-size:8px;color:{gear_col}AA;min-width:120px;
                    font-family:Share Tech Mono,monospace;">{gear_fn}</span>
                  <span style="font-size:10px;color:{pnl_col};min-width:60px;">
                    {pnl_sign}{pnl_usd:.2f}</span>
                  <span style="font-size:9px;color:{pnl_col};">{pnl_pct:+.1f}%</span>
                  <span style="font-size:8px;color:#333;margin-left:auto;">{trail_str}</span>
                  <span style="font-size:8px;color:#2a2a2a;">{hold_s}s</span>
                </div>
                """,
                unsafe_allow_html=True,
            )
    except Exception:
        pass
