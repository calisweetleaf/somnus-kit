"""Document roles: ø operator (read-only to agents), ○ living (agent-maintained), ♤ public (doc gate).

Resolution order, first decisive answer wins:
  1. kit.json `roles.overrides` (path or glob -> glyph, or "none")
  2. the file's somnus-metadata header, deliberate signal: a `classification.tags` entry
     `boundary:operator|living|public` (or the bare glyph)
  3. Daeron's doc-boundary convention by file stem (AGENTS, PLAN, TASK, SCOPE / ARCHITECTURE_MAP,
     CONTEXT, DECISIONS, MEMORY, NOTEPAD, STATE / README, SPEC, ARCHITECTURE, DEVELOPMENT_SETUP, filetree)
  4. the header's `access.visibility`: operator-only -> ø, public -> ♤
  5. the header's `classification.type` in the living set (notepad, state, memory, decision-record) -> ○

somnus-metadata core 2.0.0 has no boundary field. `classification.role` (normative, informative,
working, ...) and `classification.type` are mostly inferred by `somnus stamp`, so they are not
treated as boundary statements on their own (somnus-metadata's own PLAN.md is stamped
type=plan role=working, while the convention makes PLAN operator-owned).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Optional

from . import globs, libs

OPERATOR, LIVING, PUBLIC = "ø", "○", "♤"
NAMES = {OPERATOR: "operator", LIVING: "living", PUBLIC: "public"}

CONVENTION = {
    "AGENTS": OPERATOR, "PLAN": OPERATOR, "TASK": OPERATOR, "SCOPE": OPERATOR,
    "ARCHITECTURE_MAP": LIVING, "CONTEXT": LIVING, "DECISIONS": LIVING, "MEMORY": LIVING,
    "NOTEPAD": LIVING, "STATE": LIVING,
    "README": PUBLIC, "SPEC": PUBLIC, "ARCHITECTURE": PUBLIC, "DEVELOPMENT_SETUP": PUBLIC,
    "FILETREE": PUBLIC,
}
DOC_SUFFIXES = {".md", ".markdown", ".mdx", ".rst", ".txt", ""}
TAGS = {
    "boundary:operator": OPERATOR, "boundary:living": LIVING, "boundary:public": PUBLIC,
    OPERATOR: OPERATOR, LIVING: LIVING, PUBLIC: PUBLIC,
}
LIVING_TYPES = {"notepad", "state", "memory", "decision-record"}


@dataclass
class Role:
    glyph: Optional[str]
    source: str  # override | metadata-tag | convention | metadata-visibility | metadata-type | none
    detail: str = ""

    @property
    def name(self) -> str:
        return NAMES.get(self.glyph or "", "none")


def convention(path: str) -> Optional[str]:
    p = PurePosixPath(path)
    if p.suffix.lower() not in DOC_SUFFIXES:
        return None
    return CONVENTION.get(p.stem.upper())


def header_of(content: Optional[bytes], path: str) -> Optional[dict]:
    """The somnus payload of a file's content, parsed by somnus-metadata itself (None if absent)."""
    if content is None:
        return None
    meta = libs.metadata()
    if meta is None:
        return None
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    try:
        doc = meta.document.from_text(Path(path), text, lenient=True)
    except Exception:  # a carrier error is a metadata problem, not a role statement
        return None
    return doc.payload


def resolve(path: str, content: Optional[bytes], manifest: dict) -> Role:
    overrides = manifest.get("roles", {}).get("overrides", {})
    if path in overrides:
        g = overrides[path]
        return Role(None if g == "none" else g, "override", f"kit.json roles.overrides[{path!r}]")
    for pattern, g in overrides.items():
        if globs.match(path, pattern):
            return Role(None if g == "none" else g, "override", f"kit.json roles.overrides[{pattern!r}]")
    payload = header_of(content, path)
    if payload:
        tags = (payload.get("classification") or {}).get("tags") or []
        for t in tags:
            if isinstance(t, str) and t in TAGS:
                return Role(TAGS[t], "metadata-tag", f"classification.tags contains {t!r}")
    conv = convention(path)
    if conv:
        return Role(conv, "convention", f"doc-boundary convention for {PurePosixPath(path).stem}")
    if payload:
        vis = (payload.get("access") or {}).get("visibility")
        if vis == "operator-only":
            return Role(OPERATOR, "metadata-visibility", "access.visibility = operator-only")
        if vis == "public":
            return Role(PUBLIC, "metadata-visibility", "access.visibility = public")
        typ = (payload.get("classification") or {}).get("type")
        if typ in LIVING_TYPES:
            return Role(LIVING, "metadata-type", f"classification.type = {typ}")
    return Role(None, "none")
