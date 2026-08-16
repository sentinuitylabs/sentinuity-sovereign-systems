"""
services/internal_capability_index.py — SEARCH SELF BEFORE SEARCHING THE WORLD
==============================================================================
Before the Council asks GitHub for a mechanism, it must ask Sentinuity whether
it already has one.

WHY THIS EXISTS
---------------
A runtime regression can be caused by an existing capability becoming disconnected,
bypassed, or superseded. In that case external reconnaissance is the wrong first
move: the organism must establish what it already implements and how the affected
caller is wired before treating the mechanism as novel.

So this module answers four questions, in order:
    Do we already do this?
    Did we previously do this better?
    Is the mechanism implemented somewhere but not wired to the caller?
    Would adopting this duplicate or contradict an existing authority?

METHOD AND ITS LIMITS
---------------------
This is a lexical + structural index over the source tree: module names,
def/class names, docstrings, and a bounded keyword scan. It is NOT semantic
code understanding, and it will not recognise a capability that shares no
vocabulary with the query.

That limitation is declared rather than hidden: a weak match returns
UNSUPPORTED, never NEW. Concluding "Sentinuity does not have this" requires
evidence, and lexical absence is not evidence. This is the same discipline the
brief applies to UNKNOWN.

RUNTIME SAFETY
--------------
Pure filesystem read. Never imports the modules it indexes — importing a
service to inspect it would execute it. Bounded by file count and file size,
and the index is cached with an mtime signature so a rebuild is not triggered
by every query.
"""

from __future__ import annotations

import ast
import os
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from services.organism_causal_brief import (
    UNKNOWN, Unknown, Evidence, CapabilityVerdict, is_known,
)

__all__ = ["CapabilitySymbol", "InternalCapabilityIndex", "classify_mechanism"]

_MAX_FILE_BYTES = 2_000_000
_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "with", "by",
    "is", "are", "be", "that", "this", "it", "as", "from", "at", "use", "using",
    "code", "python", "solana", "implementation", "mechanism", "pattern",
    "system", "service", "module", "function", "class", "data", "value",
}


def _tokens(text: str) -> List[str]:
    raw = re.split(r"[^a-zA-Z0-9]+", str(text or "").lower())
    return [t for t in raw if len(t) > 2 and t not in _STOPWORDS]


@dataclass
class CapabilitySymbol:
    """One addressable capability in the tree: a module, class or function."""
    name: str
    kind: str                 # module | class | function
    path: str
    lineno: int
    doc: str = ""
    tokens: set = field(default_factory=set)

    def reference(self) -> str:
        return f"{self.path}:{self.lineno}:{self.name}"


class InternalCapabilityIndex:
    """Bounded, cached, read-only index of what Sentinuity can already do."""

    def __init__(self, roots: Sequence[str] = ("services", "core"),
                 base: Optional[str] = None, max_files: int = 800):
        self.base = base or os.getcwd()
        self.roots = list(roots)
        self.max_files = int(max_files)
        self.symbols: List[CapabilitySymbol] = []
        self.built_at: float = 0.0
        self.signature: str = ""
        self.files_indexed: int = 0
        self.parse_failures: List[Tuple[str, str]] = []

    # ── build ────────────────────────────────────────────────────────────
    def _iter_files(self):
        seen = 0
        for root in self.roots:
            full = os.path.join(self.base, root)
            if not os.path.isdir(full):
                continue
            for dirpath, dirnames, filenames in os.walk(full):
                dirnames[:] = [d for d in dirnames
                               if d not in ("__pycache__", ".git", "node_modules")]
                for fn in sorted(filenames):
                    if not fn.endswith(".py"):
                        continue
                    p = os.path.join(dirpath, fn)
                    try:
                        if os.path.getsize(p) > _MAX_FILE_BYTES:
                            continue
                    except OSError:
                        continue
                    seen += 1
                    if seen > self.max_files:
                        return
                    yield p

    def _current_signature(self) -> str:
        parts = []
        for p in self._iter_files():
            try:
                parts.append(f"{p}:{int(os.path.getmtime(p))}")
            except OSError:
                continue
        return str(hash("|".join(parts)))

    def build(self, force: bool = False) -> "InternalCapabilityIndex":
        sig = self._current_signature()
        if not force and self.symbols and sig == self.signature:
            return self
        symbols: List[CapabilitySymbol] = []
        failures: List[Tuple[str, str]] = []
        count = 0
        for path in self._iter_files():
            rel = os.path.relpath(path, self.base).replace("\\", "/")
            try:
                src = open(path, "r", encoding="utf-8", errors="replace").read()
                tree = ast.parse(src, filename=rel)
            except Exception as exc:
                # A file we cannot read is UNKNOWN territory, not empty
                # territory. Recorded so classification can declare blind spots.
                failures.append((rel, f"{type(exc).__name__}: {exc}"))
                continue
            count += 1
            mod_doc = ast.get_docstring(tree) or ""
            mod_name = os.path.splitext(os.path.basename(rel))[0]
            symbols.append(CapabilitySymbol(
                name=mod_name, kind="module", path=rel, lineno=1, doc=mod_doc[:2000],
                tokens=set(_tokens(mod_name)) | set(_tokens(mod_doc[:2000]))))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    doc = ast.get_docstring(node) or ""
                    kind = "class" if isinstance(node, ast.ClassDef) else "function"
                    symbols.append(CapabilitySymbol(
                        name=node.name, kind=kind, path=rel, lineno=node.lineno,
                        doc=doc[:1200],
                        tokens=set(_tokens(node.name)) | set(_tokens(doc[:1200]))))
        self.symbols = symbols
        self.signature = sig
        self.built_at = time.time()
        self.files_indexed = count
        self.parse_failures = failures
        return self

    # ── query ────────────────────────────────────────────────────────────
    def search(self, description: str, limit: int = 12) -> List[Tuple[CapabilitySymbol, float]]:
        if not self.symbols:
            self.build()
        q = set(_tokens(description))
        if not q:
            return []
        scored = []
        for sym in self.symbols:
            if not sym.tokens:
                continue
            overlap = q & sym.tokens
            if not overlap:
                continue
            # Coverage of the QUERY matters more than size of the symbol.
            score = len(overlap) / len(q)
            if sym.kind in ("function", "class"):
                score *= 1.15          # concrete implementations outrank modules
            name_hits = q & set(_tokens(sym.name))
            score += 0.25 * len(name_hits)
            scored.append((sym, round(min(score, 3.0), 4)))
        scored.sort(key=lambda kv: -kv[1])
        return scored[:limit]

    def blind_spots(self) -> List[Tuple[str, str]]:
        """Files that could not be parsed. Explicit unknown territory."""
        return list(self.parse_failures)


# ─────────────────────────────────────────────────────────────────────────────
# CLASSIFICATION
# ─────────────────────────────────────────────────────────────────────────────

def classify_mechanism(description: str,
                       index: Optional[InternalCapabilityIndex] = None,
                       *,
                       invariants: Optional[Sequence[str]] = None,
                       strong: float = 0.55,
                       weak: float = 0.25) -> Tuple[str, List[Evidence], object]:
    """Classify a candidate mechanism against the existing organism.

    Returns (verdict, evidence, detail). The verdict is one of
    CapabilityVerdict.*, every element of evidence carries a file:line origin,
    and detail is UNKNOWN when the index cannot speak to the question.

    The bar is deliberately asymmetric. Claiming ALREADY_HAVE needs a strong
    lexical match to a concrete implementation. Claiming NEW needs the index to
    be healthy AND the query to be well-formed — otherwise UNSUPPORTED. It is
    always safer to say "I cannot tell" than to authorise an expedition for
    something we already built.
    """
    idx = (index or InternalCapabilityIndex()).build()
    ev: List[Evidence] = []

    if not idx.symbols:
        return (CapabilityVerdict.UNSUPPORTED,
                [Evidence(claim="internal capability index is empty",
                          origin="internal_capability_index.build",
                          confidence="HIGH")],
                Unknown("index_empty"))

    q = set(_tokens(description))
    if len(q) < 2:
        return (CapabilityVerdict.UNSUPPORTED,
                [Evidence(claim=f"query too thin to classify: {description!r}",
                          origin="internal_capability_index.classify_mechanism",
                          confidence="HIGH")],
                Unknown("query_underspecified"))

    hits = idx.search(description, limit=12)
    if not hits:
        # Lexical absence is NOT evidence of capability absence. Say so.
        ev.append(Evidence(
            claim=("no lexical match in the internal index; this does not "
                   "establish that the capability is absent"),
            origin=f"internal_capability_index({idx.files_indexed} files)",
            confidence="LOW"))
        return CapabilityVerdict.UNSUPPORTED, ev, Unknown("no_lexical_match")

    best_sym, best_score = hits[0]
    concrete = [(s, sc) for s, sc in hits if s.kind in ("function", "class")]
    for sym, sc in hits[:5]:
        ev.append(Evidence(
            claim=f"internal {sym.kind} '{sym.name}' matches (score {sc})",
            origin=sym.reference(), confidence="MEDIUM",
            detail={"doc": sym.doc[:240], "score": sc}))

    # Contradiction check runs first: an invariant beats a good idea.
    inv_tokens = set()
    for inv in (invariants or []):
        inv_tokens |= set(_tokens(inv))
    if inv_tokens:
        clash = q & inv_tokens & {"disable", "bypass", "remove", "skip",
                                  "ignore", "loosen", "widen", "lower"}
        if clash:
            ev.append(Evidence(
                claim=f"mechanism language collides with a stated invariant: {sorted(clash)}",
                origin="brief.invariants", confidence="HIGH"))
            return CapabilityVerdict.CONTRADICTS_EXISTING, ev, {"clash": sorted(clash)}

    if concrete and concrete[0][1] >= strong:
        sym, sc = concrete[0]
        detail = {"symbol": sym.reference(), "score": sc,
                  "note": ("capability appears to exist; verify whether it is "
                           "wired to the affected caller before proposing new work")}
        return CapabilityVerdict.ALREADY_HAVE, ev, detail

    if best_score >= weak:
        detail = {"symbol": best_sym.reference(), "score": best_score,
                  "note": "partial internal coverage; a gap may exist at the margins"}
        return CapabilityVerdict.PARTIAL, ev, detail

    if idx.blind_spots():
        ev.append(Evidence(
            claim=f"{len(idx.blind_spots())} source files could not be parsed",
            origin="internal_capability_index.blind_spots", confidence="HIGH",
            detail={"files": [p for p, _ in idx.blind_spots()][:10]}))
        return CapabilityVerdict.UNSUPPORTED, ev, Unknown("index_incomplete")

    ev.append(Evidence(
        claim=("weak internal coverage across a fully-parsed index; a genuine "
               "gap is plausible"),
        origin=f"internal_capability_index({idx.files_indexed} files, 0 blind spots)",
        confidence="MEDIUM"))
    return CapabilityVerdict.NEW, ev, {"best_score": best_score,
                                       "best_symbol": best_sym.reference()}
