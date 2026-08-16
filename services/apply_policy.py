# coding: utf-8
"""Compatibility facade. Constitutional authority lives in golden_latch_gate."""
from __future__ import annotations
from typing import Iterable, Tuple
from services.golden_latch_gate import (
    ROOT, classify_path as _classify_path, classify as _classify,
    can_autoapply as _can_autoapply,
)

def classify_path(path: str) -> Tuple[str, str]:
    return _classify_path(path)

def classify(paths: Iterable[str]) -> Tuple[str, str]:
    return _classify(paths)

def can_autoapply(paths: Iterable[str], get_config_value=None) -> Tuple[bool, str, str]:
    return _can_autoapply(paths, get_config_value)
