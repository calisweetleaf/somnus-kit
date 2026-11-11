"""filetree.md parsing and drift, plus WIKI translation staleness (stdlib only).

parse_tree reads the FIRST fenced tree block of Daeron's box-drawing filetree.md (later blocks hold
baselines and proposals). Staleness follows somnus-github-development's wiki_sync rule: a
translation's `source_sha256` must equal sha256 of the WIKI.md body after the first `-->`
(v1 exact bytes, or v2 with BOM stripped and newlines normalized).
"""

from __future__ import annotations

import hashlib
import re

_ENTRY = re.compile(r"^(?P<indent>(?:│   |    |\|   )*)(?:├── |└── |\|-- |`-- )(?P<name>.+?)\s*$")


def parse_tree(markdown: str) -> set[str]:
    """Extract paths from the FIRST fenced tree block of a filetree.md (the actual-tree block).

    Later blocks in Daeron's files hold baselines/proposals and are not the actual tree.
    """
    m = re.search(r"```(?:text)?\n(.*?)```", markdown, re.S)
    if not m:
        return set()
    stack: list[str] = []
    out: set[str] = set()
    for line in m.group(1).splitlines():
        em = _ENTRY.match(line)
        if not em:
            continue
        depth = len(em.group("indent")) // 4
        name = em.group("name")
        name = re.split(r"\s{2,}|\s+[øo○♤#]", name)[0].strip()
        stack = stack[:depth]
        if name.endswith("/"):
            stack.append(name.rstrip("/"))
        else:
            out.add("/".join(stack + [name]))
    return out



def wiki_body(text: str) -> str:
    i = text.find("-->")
    return text[i + 3:] if i >= 0 else text


def body_hashes(text: str) -> set[str]:
    body = wiki_body(text)
    v2 = body.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    return {hashlib.sha256(body.encode("utf-8")).hexdigest(), hashlib.sha256(v2.encode("utf-8")).hexdigest()}


_SHA = re.compile(r"source_sha256:\s*([0-9a-f]{64})")
TRANSLATION = re.compile(r"^(?P<dir>(?:.*/)?)WIKI\.(?P<lang>[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]+)?)\.md$")


def stale_translations(files: dict[str, str]) -> list[tuple[str, str]]:
    """files: path -> text for WIKI*.md in a tree. Returns (translation, reason) for each stale one."""
    out = []
    for path, text in sorted(files.items()):
        m = TRANSLATION.match(path)
        if not m:
            continue
        src = m.group("dir") + "WIKI.md"
        if src not in files:
            out.append((path, f"source {src} missing"))
            continue
        rec = _SHA.search(text)
        if not rec:
            out.append((path, "no source_sha256"))
        elif rec.group(1) not in body_hashes(files[src]):
            out.append((path, f"source_sha256 does not match {src} body"))
    return out
