"""somnus-metadata as a library: config resolution, carrier detection, header validation.

APIs used: somnus.config.load / LOCAL_NAME / BUILTIN, somnus.db.glob_regex / matches_any,
somnus.carriers.for_text / SIDECAR_SUFFIX, somnus.document.from_text, somnus.model.validate.validate.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

from . import libs


def available() -> bool:
    return libs.metadata() is not None


def seed_toml(extra_excludes: list[str]) -> str:
    """A .somnus.toml whose [index].exclude repeats the built-ins (a local exclude replaces them:
    somnus config.py load(), the round-1 upstream finding) plus the kit's additions."""
    meta = libs.metadata()
    builtin = list(meta.config.BUILTIN["index"]["exclude"]) if meta else []
    excl = builtin + [e for e in extra_excludes if e not in builtin]
    body = ",\n  ".join(f'"{e}"' for e in excl)
    return ("# somnus-metadata repository config (owned by somnus-metadata; seeded by somnus-kit init).\n"
            "# [index].exclude replaces the built-in list, so the built-ins are repeated here.\n"
            "[index]\n"
            f"exclude = [\n  {body},\n]\n")


class Policy:
    """Which paths in a tree must carry a somnus header, per that tree's .somnus.toml."""

    def __init__(self, toml_text: Optional[str], scratch: Path):
        self.active = toml_text is not None
        self.error: Optional[str] = None
        self.exclude: list[str] = []
        self.roots: list[str] = ["."]
        meta = libs.metadata()
        if not self.active or meta is None:
            return
        (scratch / meta.config.LOCAL_NAME).write_text(toml_text, encoding="utf-8")
        try:
            cfg = meta.config.load(scratch)
        except meta.config.ConfigError as exc:
            self.error = str(exc)
            return
        self.exclude = list(cfg.exclude)
        self.roots = [os.path.relpath(r, scratch.resolve()).replace(os.sep, "/") for r in cfg.roots]
        from somnus.db import glob_regex
        self._patterns = [glob_regex(p) for p in self.exclude]

    def requires(self, path: str, text: Optional[str]) -> bool:
        if not self.active or self.error or text is None:
            return False
        meta = libs.metadata()
        from somnus.db import matches_any
        in_root = any(r == "." or path == r or path.startswith(r.rstrip("/") + "/") for r in self.roots)
        if not in_root or matches_any(path, self._patterns):
            return False
        if path.endswith(meta.carriers.SIDECAR_SUFFIX):
            return False
        return meta.carriers.for_text(Path(path), text) is not None


def header_errors(path: str, text: str, sidecar_text: Optional[str] = None) -> list[str]:
    """Core-level validation diagnostics; empty means a valid header (embedded or sidecar)."""
    meta = libs.metadata()
    if sidecar_text is not None:
        doc = meta.document.from_text(Path(path + meta.carriers.SIDECAR_SUFFIX), sidecar_text)
    else:
        doc = meta.document.from_text(Path(path), text)
    diags = meta.model.validate.validate(doc, "core")
    return [f"{d.code} {d.message}" for d in diags if d.is_error]
