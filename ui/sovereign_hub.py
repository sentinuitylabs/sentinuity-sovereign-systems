# ═══════════════════════════════════════════════════════════════════════════
# ui/sovereign_hub.py — RETIRED (SIGNOFF_TIER1_DOCTRINE_20260716)
# ═══════════════════════════════════════════════════════════════════════════
# TRACE RESULT (16 Jul 2026): this file was a stale divergent copy of the
# canonical hub — 519,813 bytes vs 573,511, twelve functions behind, three
# days older. Reachability audit across every .py and .bat in the repo found
# ZERO imports and ZERO execution paths: the launcher runs
#     services\sovereign_hub.py
# exclusively (Launch_Sentinuity.bat:347), and the only textual reference to
# this path was a comment in ui/world_state.py.
#
# Two independently evolving 500KB hub files is the same silent-divergence
# defect the Tier 1 audit found in the theme modules. A full copy cannot be a
# shim here: the hub is a Streamlit SCRIPT with top-level st.* calls, so
# `from services.sovereign_hub import *` would EXECUTE the entire dashboard at
# import time. This tombstone therefore fails loudly instead.
#
# If you reached this error, point your command at the canonical file:
#     python -m streamlit run services\sovereign_hub.py
# ═══════════════════════════════════════════════════════════════════════════
raise RuntimeError(
    "ui/sovereign_hub.py is retired. The canonical hub is "
    "services\\sovereign_hub.py (see Launch_Sentinuity.bat:347). "
    "This tombstone exists to prevent a stale divergent copy from being "
    "executed or silently evolved. SIGNOFF_TIER1_DOCTRINE_20260716."
)
