"""Path globs with the same semantics as somnus-metadata (`**` crosses directories, `*` and `?` do not)."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Iterable


@lru_cache(maxsize=1024)
def compile_glob(pattern: str) -> re.Pattern:
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(out + r"\Z")


def match(path: str, pattern: str) -> bool:
    if pattern.endswith("/"):
        pattern += "**"
    return bool(compile_glob(pattern).match(path))


def match_any(path: str, patterns: Iterable[str]) -> bool:
    return any(match(path, p) for p in patterns)
