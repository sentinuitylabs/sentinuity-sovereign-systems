"""Compatibility shim for May22 preflight.

The May22 package stores sovereign_identity under services/. Some preflight versions check core/sovereign_identity.py. This shim preserves the old import path without touching trading logic.
"""
try:
    from services.sovereign_identity import *  # noqa: F401,F403
except Exception:
    SOVEREIGN_IDENTITY_AVAILABLE = False
