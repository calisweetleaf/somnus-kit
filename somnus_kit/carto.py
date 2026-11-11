"""Cartographer adapter (lane C). Consumed through its CLI only, feature-detected per run.

Contract features (CONTRACT.md "Cartographer CLI"):
  model.mjs --profile auto        -> feature `profile_auto` (profiles/index.json present and accepted)
  query.mjs ... impact --json     -> feature `impact_json`
  context.mjs ... --json          -> feature `context_json`
Anything missing falls back to the kit's stand-in, built on somnus-debug's import graph, and every
result records which source produced it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Optional

from . import globs

STOP = {"fix", "add", "the", "and", "for", "before", "after", "following", "with", "from", "into", "make",
        "update", "bug", "when", "that", "this", "not", "use", "using", "should", "change", "support", "handle",
        "issue", "implement", "remove", "new", "via", "its", "are", "was", "all", "any", "out", "off"}
TEST_DIR = re.compile(r"(^|/)(tests?|testing)/")
CODE_SUFFIXES = {".py", ".mjs", ".js", ".ts", ".tsx", ".jsx", ".rs", ".go", ".sh", ".toml", ".json", ".yaml", ".yml"}


def _run(argv: list[str], cwd: Optional[Path] = None, timeout: int = 300) -> tuple[int, str, str]:
    try:
        p = subprocess.run(argv, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", str(exc)


@dataclass
class Cartographer:
    root: Optional[Path]  # cartographer checkout (contains scripts/codebase/)
    node: Optional[str] = field(default_factory=lambda: shutil.which("node"))
    features: dict = field(default_factory=dict)
    reason: str = ""

    @classmethod
    def locate(cls, manifest: dict, explicit: Optional[str] = None) -> "Cartographer":
        cands = [explicit, os.environ.get("SOMNUS_CARTOGRAPHER"), (manifest.get("cartographer") or {}).get("path")]
        for c in cands:
            if not c:
                continue
            p = Path(c).expanduser()
            for base in (p, p / "src" / "cartographer"):
                if (base / "scripts" / "codebase" / "model.mjs").is_file():
                    carto = cls(base.resolve())
                    carto.detect()
                    return carto
        c = cls(None)
        c.reason = "no Cartographer checkout (use --cartographer, $SOMNUS_CARTOGRAPHER or kit.json cartographer.path)"
        return c

    @property
    def scripts(self) -> Path:
        assert self.root is not None
        return self.root / "scripts" / "codebase"

    @property
    def usable(self) -> bool:
        return self.root is not None and self.node is not None

    def detect(self) -> dict:
        f = {"profile_auto": False, "impact_json": False, "context_json": False}
        if self.node is None:
            self.reason = "node not on PATH"
        elif self.root is not None:
            _, _, err = _run([self.node, str(self.scripts / "query.mjs")])
            f["impact_json"] = "impact" in err
            _, _, err = _run([self.node, str(self.scripts / "context.mjs")])
            f["context_json"] = "--json" in err
            f["profile_auto"] = (self.scripts / "profiles" / "index.json").is_file()
        self.features = f
        return f

    def build(self, repo: Path, out: Path, profile: str = "auto") -> dict:
        out.mkdir(parents=True, exist_ok=True)
        tried = []
        order = [profile] if profile != "auto" else (["auto", "generic"] if self.features.get("profile_auto") else ["generic"])
        for prof in order:
            argv = [self.node, str(self.scripts / "model.mjs"), "--root", str(repo), "--out", str(out), "--profile", prof]
            code, so, se = _run(argv)
            tried.append({"profile": prof, "exit": code, "stderr_tail": se.strip().splitlines()[-3:]})
            if code == 0:
                chosen = prof
                m = re.search(r"model (\S+) \((\S+)\)", so)
                if m:
                    chosen = m.group(1)
                return {"ok": True, "profile_requested": profile, "profile": chosen,
                        "profile_line": so.strip().splitlines()[0] if so.strip() else "", "attempts": tried}
        return {"ok": False, "profile_requested": profile, "attempts": tried}

    def context(self, model: Path, task: str, budget: int = 6000) -> Optional[dict]:
        if not self.features.get("context_json"):
            return None
        code, so, se = _run([self.node, str(self.scripts / "context.mjs"), "--model", str(model), "--task", task,
                             "--budget-chars", str(budget), "--json"])
        if code not in (0, 1):
            return None
        try:
            j = json.loads(so)
        except json.JSONDecodeError:
            return None
        open_next = j.get("open_next") or []
        files = j.get("files")
        if files is None:  # pre-contract shape: open_next objects with path
            files = []
            for o in open_next:
                p = o.get("path") if isinstance(o, dict) else str(o)
                if p:
                    files.append(p.split(":")[0])
        files = [f.split(":")[0] if isinstance(f, str) else f.get("path") for f in files]
        return {"pack": j.get("pack") or j.get("text") or "", "files": [f for f in files if f],
                "open_next": open_next, "shape": "contract" if "pack" in j else "pre-contract"}

    def impact(self, model: Path, paths: list[str], hops: int = 1) -> Optional[dict]:
        if not self.features.get("impact_json") or not paths:
            return None
        code, so, se = _run([self.node, str(self.scripts / "query.mjs"), "--model", str(model), "impact", *paths, "--json"])
        if code not in (0, 1):
            return None
        try:
            j = json.loads(so)
        except json.JSONDecodeError:
            return None
        return normalize_impact(j, hops)


def _name(item) -> Optional[str]:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for k in ("file", "path", "name", "id"):
            if isinstance(item.get(k), str):
                return item[k]
    return None


def normalize_impact(j: dict, hops: int = 1) -> dict:
    """Contract lists may hold strings or objects. Importers deeper than `hops` and docs matched only by a
    bare file stem (Cartographer marks those weak) are left out of the allowed set but kept for display."""
    out: dict = {"trust": j.get("trust", {}), "dropped": {}}
    for k in ("files", "packages", "dependents"):
        out[k] = [n for n in (_name(i) for i in j.get(k, [])) if n]
    imps, deep = [], []
    for i in j.get("importers", []):
        n = _name(i)
        if not n:
            continue
        depth = i.get("depth", 1) if isinstance(i, dict) else 1
        (imps if depth <= hops else deep).append(n)
    out["importers"], out["dropped"]["importers_deeper"] = sorted(set(imps)), sorted(set(deep) - set(imps))
    docs, weak = [], []
    for d in j.get("docs", []):
        n = _name(d)
        if not n:
            continue
        kinds = {m.get("kind") for m in d.get("matched", []) if isinstance(m, dict)} if isinstance(d, dict) else set()
        (weak if kinds and kinds <= {"stem"} else docs).append(n)
    out["docs"], out["dropped"]["docs_weak"] = docs, weak
    out["tests"] = sorted({n for n in (_name(t) for t in j.get("tests", [])) if n})
    return out


# ----------------------------------------------------------------------------- stand-in


def _stem(t: str) -> str:
    for suf in ("ing", "es", "s", "ed"):
        if len(t) > 4 and t.endswith(suf):
            return t[: -len(suf)]
    return t


def terms(task: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_]+", task.lower())
    out = []
    for w in words:
        if len(w) < 3 or w in STOP:
            continue
        s = _stem(w)
        if s not in out:
            out.append(s)
    return out


def fallback_context(repo: Path, files: list[str], task: str, limit: int = 5) -> dict:
    """Rank source files by task terms: path/stem matches dominate, content matches break ties."""
    ts = terms(task)
    scored: list[tuple[float, str, str]] = []
    for rel in files:
        p = PurePosixPath(rel)
        if p.suffix not in CODE_SUFFIXES or TEST_DIR.search(rel) or "/fixtures/" in rel:
            continue
        name_parts = [_stem(x) for x in re.split(r"[^a-z0-9]+", p.stem.lower()) if x]
        path_hits = [t for t in ts if t in name_parts]
        score = 3.0 * len(path_hits)
        try:
            text = (repo / rel).read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            text = ""
        content_hits = [t for t in ts if t in text]
        score += 0.5 * len(content_hits)
        if score > 0:
            why = "path match: " + ",".join(path_hits) if path_hits else "content match: " + ",".join(content_hits)
            scored.append((score, rel, why))
    scored.sort(key=lambda x: (-x[0], x[1]))
    strong = [s for s in scored if s[0] >= 3.0]
    chosen = (strong or scored)[:limit] if strong else scored[: min(3, limit)]
    lines = [f"# Context pack (kit stand-in)", f"task: {task}", "", "## Open next"]
    lines += [f"{i + 1}. `{rel}` ({why})" for i, (_, rel, why) in enumerate(chosen)]
    return {"pack": "\n".join(lines) + "\n", "files": [c[1] for c in chosen],
            "open_next": [{"path": c[1], "why": c[2], "score": round(c[0], 2)} for c in chosen],
            "shape": "kit-stand-in"}


def fallback_impact(repo: Path, files: list[str], scope: list[str], imports: dict[str, list[str]]) -> dict:
    """importers (one hop) from somnus-debug's resolved import edges; tests by import or name; docs by mention."""
    scope_set = set(scope)
    importers = sorted({src for src, tgts in imports.items() if scope_set & set(tgts)} - scope_set)
    stems = {PurePosixPath(s).stem for s in scope if s.endswith(".py") and PurePosixPath(s).stem != "__init__"}
    tests = sorted({f for f in files if TEST_DIR.search(f) and f.endswith(".py") and (
        any(PurePosixPath(f).stem.startswith(f"test_{st}") or PurePosixPath(f).stem == f"{st}_test" for st in stems)
        or (f in imports and scope_set & set(imports[f])))})
    importers = [i for i in importers if i not in tests]
    docs = []
    pats = [re.compile(re.escape(s)) for s in scope] + [re.compile(rf"\b{re.escape(st)}\.py\b") for st in stems]
    for f in files:
        if not f.lower().endswith((".md", ".rst")):
            continue
        try:
            text = (repo / f).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if any(p.search(text) for p in pats):
            docs.append(f)
    return {"files": sorted(scope_set), "packages": [], "dependents": [], "importers": importers,
            "docs": sorted(docs), "tests": tests, "trust": {"source": "kit-stand-in",
                                                            "basis": "somnus_debug import edges, one hop"}}


def test_globs(scope: list[str]) -> list[str]:
    """New tests for in-scope modules are allowed: tests/**/test_<stem>*.py."""
    out = []
    for s in scope:
        p = PurePosixPath(s)
        if p.suffix == ".py" and p.stem != "__init__":
            out += [f"**/test_{p.stem}*.py", f"**/{p.stem}_test.py"]
    return out


def allowed(path: str, allow: set[str], patterns: list[str]) -> bool:
    return path in allow or globs.match_any(path, patterns)
