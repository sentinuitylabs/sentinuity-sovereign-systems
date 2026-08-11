"""
ui/sentinuity_tokens.py — SENTINUITY VISUAL TOKEN LAYER v2

Read-only presentation helper. No DB access, no trading logic, no I/O.
Import cost is a dict lookup and one CSS string.

WHY THIS FILE EXISTS
────────────────────
`services/sovereign_hub.py` currently contains 107 distinct hex literals and
242 occurrences of `font-size:0.66rem`, while `SENTINUITY_COLORS` (7 keys) is
referenced 5 times. The doctrine is therefore aspirational: there is no
mechanism by which a violation could be detected, because a violation is just
another f-string. This module is that mechanism.

Rules:
  1. No renderer may emit a literal hex value. Use `C[...]` or `var(--snt-*)`.
  2. No renderer may emit a literal font-size. Use `T[...]` or `var(--snt-t-*)`.
  3. `authority_style()` is the ONLY place a truth-state maps to a colour.
     If a new state appears in the engine, it lands in ABSENCE by default —
     fail-closed visually, exactly as the engine fails closed logically.

Contains no thresholds. Every numeric limit shown in the UI must be read from
the owning service at render time (peak_truth.MAX_DIVERGENCE_PCT, etc.) so the
UI can never drift from the gate it is describing.
"""

from __future__ import annotations

# ═════════════════════════════════════════════════════════════════════════════
# COLOUR — seven semantic families. Each has: hi / base / dim fill / edge.
# ═════════════════════════════════════════════════════════════════════════════
C = {
    # ground & crystal containment
    "void":            "#050210",
    "void_deep":       "#030109",
    "crystal_fill":    "rgba(28,16,58,.34)",
    "crystal_fill_hi": "rgba(38,22,74,.46)",
    "crystal_edge":    "rgba(154,132,232,.16)",
    "crystal_edge_hi": "rgba(154,132,232,.30)",

    # text ramp — muted grey-lilac. There is no white.
    "ink_1": "#CFC7E8",   # the one figure that matters on a card
    "ink_2": "#9A93B8",   # body, reasons, plain-language explanation
    "ink_3": "#6E6889",   # labels, units, source names
    "ink_4": "#494360",   # axes, scaffolding, timestamps

    # EMERALD — confirmed / corroborated / settled positive / alive
    "em_hi": "#14F195", "em": "#0FBE76",
    "em_dim": "rgba(20,241,149,.16)", "em_edge": "rgba(20,241,149,.38)",

    # GOLD — runner, provisional authority, peak/floor transition, attention
    "gold_hi": "#FFD700",   # RESERVED: ARMED floor and true apex only
    "gold": "#E8B84C",      # the working gold
    "gold_dim": "rgba(232,184,76,.15)", "gold_edge": "rgba(232,184,76,.40)",

    # CORAL — hard blocker, refusal, unsafe execution, realised loss
    "coral_hi": "#FF5C6E", "coral": "#D8394C",
    "coral_dim": "rgba(255,92,110,.14)", "coral_edge": "rgba(255,92,110,.36)",

    # VIOLET / MAGENTA — excursion, distance travelled, diagnostic depth
    "vio_hi": "#C77DFF", "vio": "#9945FF", "mag": "#E879F9",
    "vio_dim": "rgba(153,69,255,.18)", "vio_edge": "rgba(153,69,255,.34)",

    # CYAN / TEAL — neutral instrumentation, observation, read-only evidence
    "cy_hi": "#5FD3E0", "cy": "#3E9AA8",
    "cy_dim": "rgba(95,211,224,.13)", "cy_edge": "rgba(95,211,224,.30)",

    # ABSENCE — proposed 7th family. Not refusal; lack of evidence.
    "abs": "#565073",
    "abs_dim": "rgba(86,80,115,.16)", "abs_edge": "rgba(86,80,115,.42)",

    # UI_METER_MATERIAL_20260809
    # A meter track is a RECESSED CHANNEL cut into the crystal, not an absent
    # value. The living trade meter previously used abs_dim as its track, so the
    # single most-looked-at surface in the app was rendered in the design
    # system's "no data" material — a desaturated mauve at 16% opacity, which
    # over dark glass reads as pale milky lavender. That is the pasty-white
    # appearance. Absence tokens are now reserved for genuinely absent data.
    "track":       "rgba(5,2,16,.72)",       # deeper than the pane it sits in
    "track_edge":  "rgba(154,132,232,.20)",  # crystal rim, catches the light
    "track_inner": "rgba(0,0,0,.55)",        # inset shadow, sells the recess
    "datum":       "rgba(207,199,232,.55)",  # the 0% entry datum line
}

# ═════════════════════════════════════════════════════════════════════════════
# TYPE — hierarchy is carried by size and weight so colour is free to carry
# semantics. This is the structural fix for "colour must encode semantics".
# ═════════════════════════════════════════════════════════════════════════════
T = {
    "trace":  "10px",    # trace lines only
    "micro":  "11px",    # units, ages, source labels, chips
    "label":  "12px",    # eyebrows, section captions
    "body":   "13px",    # plain-language reasons
    "state":  "16px",    # state vocabulary and token names
    "figure": "26px",    # exactly one per card: the PnL figure
}

F_MONO = '"Share Tech Mono", ui-monospace, SFMono-Regular, Menlo, monospace'
F_UI   = '"Barlow Condensed", "Roboto Condensed", system-ui, sans-serif'

# ═════════════════════════════════════════════════════════════════════════════
# AUTHORITY MAP — the single point where engine truth-state becomes colour.
# Keys are the exact strings emitted by the services. Unknown → ABSENCE.
# ═════════════════════════════════════════════════════════════════════════════
_EM, _GOLD, _CORAL, _CY, _VIO, _ABS = "em", "gold", "coral", "cy", "vio", "abs"

_AUTHORITY = {
    # peak_truth.evaluate_position → out["state"]
    "OBSERVED_PEAK":          (_CY,    "OBSERVED",     "Seen. Nothing has corroborated it."),
    "CORROBORATED_MARK_PEAK": (_CY,    "CORROBORATED", "Two independent witnesses agree on the mark."),
    "EXECUTABLE_PEAK":        (_EM,    "EXECUTABLE",   "An actual-size quote exists at this price."),
    "TRUSTED_PEAK":           (_GOLD,  "TRUSTED",      "Confirmed across two distinct slots by independent families."),

    # mode3_peak_continuity floor states
    "ARMED_TRUSTED":            (_GOLD,  "ARMED",   "Floor is live and trailing."),
    "ARMED_STICKY":             (_GOLD,  "STICKY",  "Floor held while the peak source is briefly absent."),
    "RUNNER_FLOOR_UNAVAILABLE": (_ABS,   "NOT ARMED", "No qualified peak has ever been recorded."),
    "RUNNER_FLOOR_EXPIRED":     (_CORAL, "EXPIRED", "Grace window elapsed. Floor released, explicitly."),

    # mode3 peak status
    "LIVE":    (_EM,   "LIVE",    "Peak source confirmed inside the live window."),
    "STICKY":  (_GOLD, "STICKY",  "Peak source absent but inside grace."),
    "EXPIRED": (_CORAL, "EXPIRED", "Grace elapsed."),
    "NONE":    (_ABS,  "NONE",    "No qualified peak recorded."),

    # price_router warnings
    "LIVE_MARK":  (_EM,   "LIVE",      "Fresh executable mark."),
    "LAST_GOOD":  (_GOLD, "LAST GOOD", "Serving the last mark that passed all gates."),
    "STALE":      (_ABS,  "STALE",     "Older than the freshness gate. Not evidence."),
    "RPC_DEGRADED": (_CORAL, "DEGRADED", "Recovery path. A degraded source cannot witness."),
    "API_FALLBACK": (_CORAL, "FALLBACK", "Fallback path. Cannot promote to authority."),
    "NO_DATA":    (_ABS,  "NO DATA",   "Nothing to read."),
    "NO_DATA_POST_REFERENCE": (_ABS, "NO MARK", "No trusted price at or after the reference timestamp."),
}

# peak_truth.MARKET_TRUTH_ERROR_CLASSES — the market actually spoke.
_MARKET_REFUSAL = {"NO_ROUTE", "IMPACT_BLOCK", "ILLIQUID"}
# Everything else describes OUR pipeline. Blindness, not refusal.
_PIPELINE_BLIND = {
    "AUTH_FAILURE", "RATE_LIMIT", "TIMEOUT", "PROVIDER_ERROR",
    "STALE_RESPONSE", "MALFORMED_RESPONSE", "UNCLASSIFIED",
}


def authority_style(state: str) -> tuple:
    """Map an engine truth-state string to (family, display_label, plain_english).

    Unknown states fall through to ABSENCE rather than to a cheerful default.
    A state the UI does not recognise is a state the UI must not vouch for.
    """
    key = str(state or "").strip().upper()
    return _AUTHORITY.get(key, (_ABS, key or "UNKNOWN", "Unrecognised state. Treated as no evidence."))


def quote_error_style(error_class: str) -> tuple:
    """Distinguish 'the market refused' from 'we could not see'.

    peak_truth.classify_quote_error already makes this distinction and
    MARKET_TRUTH_ERROR_CLASSES already encodes it. Collapsing both into one
    red 'NO QUOTE' badge — as the current UI does — teaches the operator that
    a provider timeout means the position is unsellable. It does not.
    """
    key = str(error_class or "").strip().upper()
    if key == "OK":
        return (_EM, "OK", "Quote returned.")
    if key in _MARKET_REFUSAL:
        return (_CORAL, key.replace("_", " "),
                "The market answered. This is evidence about sellability.")
    if key in _PIPELINE_BLIND:
        return (_ABS, key.replace("_", " "),
                "Our pipeline failed, not the market. This is not evidence about sellability.")
    return (_ABS, key or "NO QUOTE", "Unclassified. Treated as no evidence.")


def fam(name: str, tone: str = "hi") -> str:
    """Resolve a family + tone to a token value. `fam('coral')` → coral_hi."""
    return C.get(f"{name}_{tone}", C.get(name, C["abs"]))


# ═════════════════════════════════════════════════════════════════════════════
# CSS — inject once, near the top of the hub, AFTER ui.theme.semantic_css().
# ═════════════════════════════════════════════════════════════════════════════
def tokens_css() -> str:
    """Return the token stylesheet. Idempotent, no side effects.

    Note the mobile rule at the end: the existing responsive shell raises
    `p`, `li` and `label` to .82rem, but essentially all dense hub data is
    rendered in `<div>`/`<span>` with an inline `font-size:0.66rem`, which
    the shell never touches. That is the mechanical reason mobile reads as
    illegible despite a correct-looking media query.
    """
    decls = "".join(f"--snt-{k.replace('_','-')}:{v};" for k, v in C.items())
    types = "".join(f"--snt-t-{k}:{v};" for k, v in T.items())
    return f"""<style id="snt-tokens-v2">
:root{{{decls}{types}--snt-f-mono:{F_MONO};--snt-f-ui:{F_UI};}}

/* Machine truth is monospace and tabular. System voice is condensed sans.
   The typeface itself encodes who is speaking. */
.snt-mono{{font-family:var(--snt-f-mono);font-variant-numeric:tabular-nums;letter-spacing:.02em}}
.snt-ui{{font-family:var(--snt-f-ui)}}

/* One figure per card, and it is the only large thing on it. */
.snt-figure{{font-family:var(--snt-f-mono);font-size:var(--snt-t-figure);
  line-height:1;font-variant-numeric:tabular-nums}}
.snt-state{{font-size:var(--snt-t-state)}}
.snt-label{{font-size:var(--snt-t-label);letter-spacing:.2em;text-transform:uppercase;
  color:var(--snt-ink-3)}}
.snt-micro{{font-size:var(--snt-t-micro);color:var(--snt-ink-3)}}
.snt-trace{{font-family:var(--snt-f-mono);font-size:var(--snt-t-trace);
  font-variant-numeric:tabular-nums;line-height:1.55}}

/* Crystal containment. */
.snt-pane{{border:1px solid var(--snt-crystal-edge);border-radius:7px;
  background:linear-gradient(180deg,var(--snt-crystal-fill-hi),var(--snt-crystal-fill));
  overflow:hidden}}

/* Absence is dashed and desaturated. It must never be mistaken for refusal. */
.snt-absent{{border:1px dashed var(--snt-abs-edge);background:var(--snt-abs-dim);
  color:var(--snt-ink-3)}}

/* Motion: transitions only, no whole-card flashing. */
.snt-rung{{transition:background .24s ease-out,border-color .24s ease-out}}
.snt-latch{{animation:sntLatch .4s cubic-bezier(.2,.9,.3,1) both}}
@keyframes sntLatch{{0%{{transform:translateX(-6px);opacity:.4}}100%{{transform:none;opacity:1}}}}
.snt-breathe::after{{animation:sntBreathe 2.4s ease-in-out infinite}}
@keyframes sntBreathe{{0%,100%{{opacity:.25}}50%{{opacity:.95}}}}
@media (prefers-reduced-motion:reduce){{
  *,*::after,*::before{{animation:none!important;transition:none!important}}
}}

/* THE MOBILE FIX: inline font-size on divs/spans bypasses the responsive
   shell entirely. This reclaims them without touching any renderer. */
@media(max-width:430px){{
  [data-testid="stAppViewContainer"] .snt-pane div[style*="font-size:0.66rem"],
  [data-testid="stAppViewContainer"] .snt-pane span[style*="font-size:0.66rem"]{{
    font-size:var(--snt-t-micro)!important;
  }}
  .snt-pane{{border-radius:6px}}
}}
</style>"""


__all__ = ["C", "T", "F_MONO", "F_UI", "authority_style",
           "quote_error_style", "fam", "tokens_css"]
