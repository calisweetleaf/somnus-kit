"""Import Daeron's tools as libraries: somnus-metadata (`somnus`) and somnus-debug (`somnus_debug`).

Both are optional at import time so the kit can still report honestly (NOT_RUN) when one is
missing. Extra search paths: $SOMNUS_KIT_LIBPATH (os.pathsep separated `src` dirs).
"""

from __future__ import annotations

import importlib
import os
import sys
from types import ModuleType
from typing import Optional

for _p in filter(None, os.environ.get("SOMNUS_KIT_LIBPATH", "").split(os.pathsep)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_cache: dict[str, Optional[ModuleType]] = {}
_errors: dict[str, str] = {}


def _try(name: str) -> Optional[ModuleType]:
    if name not in _cache:
        try:
            _cache[name] = importlib.import_module(name)
        except Exception as exc:  # ImportError or a broken install: report, do not crash the guard
            _cache[name] = None
            _errors[name] = f"{type(exc).__name__}: {exc}"
    return _cache[name]


def metadata() -> Optional[ModuleType]:
    """The somnus-metadata package (`somnus`), with config/document/carriers/validate loaded."""
    mod = _try("somnus")
    if mod is None:
        return None
    for sub in ("somnus.config", "somnus.document", "somnus.carriers", "somnus.model.validate", "somnus.infer"):
        if _try(sub) is None:
            return None
    return mod


def debug() -> Optional[ModuleType]:
    """somnus_debug.doctor.core (ConfigManager, ProjectScanner)."""
    if _try("somnus_debug") is None:
        return None
    return _try("somnus_debug.doctor.core")


def error(name: str) -> str:
    return _errors.get(name, "not importable")


def versions() -> dict[str, Optional[str]]:
    out: dict[str, Optional[str]] = {}
    for name in ("somnus", "somnus_debug"):
        mod = _try(name)
        out[name] = getattr(mod, "__version__", None) if mod else None
    return out
