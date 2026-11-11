"""Mechanical doc gate for ♤ public docs.

This is the machine-checkable subset of Daeron's somnus-doc-system gates: no leftover placeholders,
relative links resolve inside the tree, no private terms from kit.json, and the doc has a heading.
Evidence quality and voice still need the somnus-doc-system review; verify says so on every pass.
"""

from __future__ import annotations

import posixpath
import re

PLACEHOLDER = re.compile(r"\b(TODO|TBD|FIXME|XXX)\b|\{\{[^}\n]+\}\}|<placeholder>|lorem ipsum", re.I)
LINK = re.compile(r"(?<!!)\[[^\]\n]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)|!\[[^\]\n]*\]\(([^)\s]+)\)")
FENCE = re.compile(r"```.*?```", re.S)


def check(path: str, text: str, tree_files: set[str], manifest: dict) -> list[str]:
    cfg = manifest.get("docgate", {})
    problems: list[str] = []
    prose = FENCE.sub("", text)
    ph = PLACEHOLDER.findall(prose)
    if len(ph) > int(cfg.get("max_placeholder", 0)):
        problems.append(f"{len(ph)} placeholder marker(s)")
    if path.lower().endswith((".md", ".markdown")) and not re.search(r"(?m)^#{1,6} \S", prose):
        problems.append("no heading")
    dirs = {posixpath.dirname(f) for f in tree_files}
    expanded = set(dirs)
    for d in dirs:
        while d:
            d = posixpath.dirname(d)
            expanded.add(d)
    for m in LINK.finditer(prose):
        target = m.group(1) or m.group(2)
        if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith("#"):
            continue
        target = target.split("#", 1)[0].split("?", 1)[0]
        if not target:
            continue
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), target)).lstrip("./") \
            if not target.startswith("/") else target.lstrip("/")
        if resolved.startswith(".."):
            problems.append(f"link leaves the repo: {target}")
        elif resolved not in tree_files and resolved.rstrip("/") not in expanded:
            problems.append(f"broken link: {target}")
    low = text.lower()
    for term in cfg.get("private_terms", []):
        if term and term.lower() in low:
            problems.append(f"private term present: {term!r}")
    return problems
