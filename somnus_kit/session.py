"""`brief`: open a scope session. Everything it writes goes under $SOMNUS_HOME."""

from __future__ import annotations

import datetime as dt
import json
import secrets
from pathlib import Path
from typing import Optional

from . import KIT_VERSION, carto, debug, gitio, globs, home, manifest as M, roles, snapshot

SCHEMA = "somnus-kit.session/1"


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def new_id() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)


class SessionError(LookupError):
    pass


def active_id(rh: home.RepoHome) -> Optional[str]:
    p = rh.sessions / "ACTIVE"
    return p.read_text(encoding="utf-8").strip() or None if p.is_file() else None


def load(rh: home.RepoHome, sid: Optional[str] = None) -> dict:
    sid = sid or active_id(rh)
    if not sid:
        raise SessionError("no active session; run `somnus-kit brief <repo> --task ...`")
    p = rh.sessions / f"{sid}.json"
    if not p.is_file():
        raise SessionError(f"session {sid} not found under {rh.sessions}")
    return json.loads(p.read_text(encoding="utf-8"))


def save(rh: home.RepoHome, data: dict) -> Path:
    rh.ensure("sessions")
    p = rh.sessions / f"{data['id']}.json"
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return p


def end(rh: home.RepoHome, sid: Optional[str] = None) -> Optional[str]:
    sid = sid or active_id(rh)
    if not sid:
        return None
    data = load(rh, sid)
    data["status"] = "closed"
    data["closed"] = now_iso()
    save(rh, data)
    if active_id(rh) == sid:
        (rh.sessions / "ACTIVE").unlink()
    return sid


def brief(repo: Path, task: str, scopes: list[str], *, cartographer: Optional[str] = None,
          budget: int = 6000, rh: Optional[home.RepoHome] = None) -> dict:
    rh = rh or home.RepoHome(repo)
    rh.ensure("sessions", "models", "debug", "logs")
    man = M.load(repo)
    initialised = (repo / M.KIT_JSON).is_file()
    sid = new_id()
    files = snapshot.hash_tree(repo)
    tree = sorted(files)
    notes: list[str] = []
    if not initialised:
        notes.append(f"{M.KIT_JSON} missing: repo not initialised, kit defaults in use")

    # debug baseline (also yields the import graph used by the stand-in impact)
    dbg = None
    if man["debug"].get("enabled", True) and debug.available():
        dbg = debug.scan(repo, debug.find_config(repo, man))
        (rh.debug / f"{sid}.json").write_text(json.dumps(dbg, indent=1) + "\n", encoding="utf-8")
    elif not debug.available():
        notes.append("somnus_debug not importable: no debug baseline; verify check (e) will be NOT_RUN")

    # cartographer model + context pack
    c = carto.Cartographer.locate(man, cartographer)
    carto_info: dict = {"available": c.usable, "features": c.features, "reason": c.reason, "model": None}
    ctx = None
    if c.usable:
        built = c.build(repo, rh.models, (man.get("cartographer") or {}).get("profile", "auto"))
        carto_info["build"] = built
        if built["ok"]:
            carto_info["model"] = str(rh.models)
            ctx = c.context(rh.models, task, budget)
    if ctx is None or not ctx["files"]:
        fb = carto.fallback_context(repo, tree, task)
        if ctx is None:
            ctx = fb
        else:  # Cartographer answered but named no files: keep its pack, take files from the stand-in
            ctx = {**ctx, "files": fb["files"], "open_next": ctx["open_next"] or fb["open_next"],
                   "shape": ctx["shape"] + "+kit-stand-in-files"}
    carto_info["context_source"] = ctx["shape"]

    # scope
    if scopes:
        seeds = sorted(f for f in tree if globs.match_any(f, scopes))
        declared, source = list(scopes), "--scope"
        if not seeds:
            notes.append("--scope matched no existing files (new files under it are still allowed)")
    else:
        seeds = [f for f in ctx["files"] if f in files]
        declared, source = list(seeds), "context-pack"
    imp = c.impact(rh.models, seeds, int(man["scope"].get("impact_hops", 1))) if (c.usable and carto_info["model"]) else None
    if imp is not None:
        imp["trust"] = {**imp.get("trust", {}), "source": "cartographer"}
    else:
        imp = carto.fallback_impact(repo, tree, seeds, (dbg or {}).get("imports", {}))
    allow = sorted(set(seeds) | set(imp["files"]) | set(imp["importers"]) | set(imp["tests"]) | set(imp["docs"]))
    patterns = declared + carto.test_globs(seeds) + list(man["scope"].get("always_allowed", []))

    data = {
        "schema": SCHEMA, "kit_version": KIT_VERSION, "id": sid, "status": "open", "created": now_iso(),
        "repo": str(repo), "repo_id": rh.id, "task": task, "head": gitio.head(repo), "branch": gitio.branch(repo),
        "files": files,
        "scope": {"source": source, "declared": declared, "seeds": seeds, "patterns": patterns,
                  "impact": imp, "allowed": allow},
        "context": {"pack": ctx["pack"], "open_next": ctx["open_next"], "files": ctx["files"]},
        "cartographer": carto_info,
        "debug_baseline": {"path": str(rh.debug / f"{sid}.json")} if dbg else None,
        "notes": notes,
    }
    save(rh, data)
    (rh.sessions / "ACTIVE").write_text(sid + "\n", encoding="utf-8")
    return data


def rules_text(repo: Path, man: dict, tree: list[str], snap: snapshot.Snapshot) -> str:
    by: dict[str, list[str]] = {roles.OPERATOR: [], roles.LIVING: [], roles.PUBLIC: []}
    for f in tree:
        if not f.lower().endswith((".md", ".markdown", ".rst", ".txt")) and f not in man["roles"]["overrides"]:
            continue
        r = roles.resolve(f, snap.read(f), man)
        if r.glyph:
            by[r.glyph].append(f)
    lines = ["## Rules for this repo", ""]
    lines.append("- ø operator, read-only: " + (", ".join(f"`{x}`" for x in by[roles.OPERATOR]) or "none found"))
    lines.append("- ○ living, keep current: " + (", ".join(f"`{x}`" for x in by[roles.LIVING]) or "none found"))
    lines.append("- ♤ public, doc gate: " + (", ".join(f"`{x}`" for x in by[roles.PUBLIC]) or "none found"))
    lines.append("- protected: " + ", ".join(f"`{x}`" for x in man["protected"]))
    gen = [p for p, m in man["ownership"].items() if m.get("class") == M.GENERATED] + [g["glob"] for g in man["generated"]]
    lines.append("- generated, never hand-edit: " + (", ".join(f"`{x}`" for x in gen) or "none"))
    if (repo / ".somnus.toml").is_file() and man["metadata"].get("require_on_new", True):
        lines.append("- new files need a somnus header: `somnus stamp PATH --write`")
    lines.append("- before stopping: `somnus-kit verify .` (FAIL = stop)")
    return "\n".join(lines) + "\n"


def render(data: dict, rules: str, path: str) -> str:
    s = data["scope"]
    imp = s["impact"]
    out = [f"# somnus-kit brief  session {data['id']}", "",
           f"task: {data['task']}", f"repo: {data['repo']} ({data['repo_id']})  head: {(data['head'] or 'none')[:12]}", "",
           f"## Scope ({s['source']})", ""]
    out += [f"- `{x}`" for x in s["declared"]] or ["- (empty: nothing matched the task; pass --scope)"]
    out += ["", f"## Impact ({imp.get('trust', {}).get('source', '?')})", ""]
    for k in ("importers", "tests", "docs", "dependents", "packages"):
        if imp.get(k):
            out.append(f"- {k}: " + ", ".join(f"`{x}`" for x in imp[k]))
    out += ["", rules, data["context"]["pack"].rstrip(), ""]
    if data["notes"]:
        out += ["## Notes", ""] + [f"- {n}" for n in data["notes"]] + [""]
    out.append(f"session file: {path}")
    return "\n".join(out) + "\n"
