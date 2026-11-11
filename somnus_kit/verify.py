"""`verify`: checks (a)-(g) from CONTRACT.md over a change set.

Status per check: PASS, WARN, FAIL, SKIP (not applicable), NOT_RUN (a needed library is missing;
counts as WARN, never as a pass). Overall: any FAIL -> FAIL, else any WARN/NOT_RUN -> WARN, else PASS.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from . import KIT_VERSION, agents, debug, docgate, filetree, globs, home, libs, manifest as M, metadata, roles
from .snapshot import ChangeSet

PASS, WARN, FAIL, SKIP, NOT_RUN = "PASS", "WARN", "FAIL", "SKIP", "NOT_RUN"
RANK = {PASS: 0, SKIP: 0, WARN: 1, NOT_RUN: 1, FAIL: 2}
SCHEMA = "somnus-kit.verify/1"
GEN_MARK = re.compile(r"@generated|do not edit", re.I)


@dataclass
class Check:
    id: str
    name: str
    status: str = PASS
    detail: str = ""
    files: list[str] = field(default_factory=list)
    items: list[str] = field(default_factory=list)

    def worsen(self, status: str, item: Optional[str] = None, path: Optional[str] = None) -> None:
        if RANK[status] > RANK[self.status] or self.status in (SKIP,) and status != SKIP:
            self.status = status
        if item:
            self.items.append(f"{status}: {item}")
        if path and path not in self.files:
            self.files.append(path)


@dataclass
class Ctx:
    repo: Path
    cs: ChangeSet
    man: dict          # manifest from the new tree (falls back to the repo's)
    old_man: dict
    session: Optional[dict]
    operator: bool
    rh: home.RepoHome
    kit_maintained: set = field(default_factory=set)


def _manifest_from(snap, fallback: dict) -> dict:
    text = snap.text(M.KIT_JSON)
    if text is None:
        return fallback
    try:
        return M.from_data(json.loads(text))
    except (json.JSONDecodeError, M.ManifestError):
        return fallback


def _ø(ctx: Ctx) -> str:
    return WARN if ctx.operator else FAIL


# ----------------------------------------------------------------------------- pre-pass

def kit_maintained(ctx: Ctx) -> set[str]:
    """Files whose change is exactly what the kit itself would write (region re-render, adapter regen)."""
    out = set()
    for path in ctx.cs.paths:
        new, old = ctx.cs.new.text(path), ctx.cs.old.text(path)
        reg_new = agents.extract(new)
        if reg_new is not None and new is not None:
            same_operator_text = agents.strip(new) == agents.strip(old) if old is not None else \
                agents.strip(new).strip() in ("", "# AGENTS.md")
            if same_operator_text and reg_new == agents.render(ctx.man):
                out.add(path)
        k = agents.adapter_for_path(path)
        if k and ctx.man["adapters"].get(k) and new == agents.ADAPTERS[k]["text"]:
            out.add(path)
    return out


# ----------------------------------------------------------------------------- (a) scope

def check_scope(ctx: Ctx) -> Check:
    c = Check("a", "scope")
    changed = ctx.cs.paths
    if not changed:
        c.detail = "no changes"
        return c
    exempt, outside = [], []
    s = ctx.session
    for path in changed:
        if path in ctx.kit_maintained:
            exempt.append(path)
            continue
        role = roles.resolve(path, ctx.cs.new.read(path) or ctx.cs.old.read(path), ctx.man)
        if role.glyph == roles.LIVING:
            exempt.append(path)
            continue
        if s is None:
            outside.append(path)
            continue
        sc = s["scope"]
        if path in sc["allowed"] or globs.match_any(path, sc["patterns"]):
            continue
        outside.append(path)
    if s is None:
        if outside:
            c.worsen(WARN, f"{len(outside)} changed file(s) with no declared scope (no session)")
            for p in outside:
                c.worsen(WARN, f"{p}: no session scope", p)
        c.detail = f"no session; {len(changed)} changed, scope not enforced"
    else:
        for p in outside:
            c.worsen(FAIL, f"{p}: outside scope ({len(s['scope']['declared'])} declared path(s) + "
                     f"{len(s['scope']['allowed'])} in the impact set; see session {s['id']})", p)
        inside = len(changed) - len(outside) - len(exempt)
        c.detail = (f"session {s['id']}: {inside} in scope, {len(exempt)} exempt (living/kit-maintained), "
                    f"{len(outside)} outside")
    return c


# ----------------------------------------------------------------------------- (b) roles

def check_roles(ctx: Ctx) -> Check:
    c = Check("b", "roles")
    tree = ctx.cs.new.files()
    counts = {roles.OPERATOR: 0, roles.LIVING: 0, roles.PUBLIC: 0}
    meta_note = "" if metadata.available() else " (somnus-metadata not importable: header tags not read)"
    pass_status = ctx.man["docgate"].get("pass_status", WARN)
    for path in ctx.cs.paths:
        content = ctx.cs.new.read(path)
        deleted = content is None
        r = roles.resolve(path, content if not deleted else ctx.cs.old.read(path), ctx.man)
        if not r.glyph:
            continue
        counts[r.glyph] += 1
        if r.glyph == roles.OPERATOR:
            if path in ctx.kit_maintained:
                c.items.append(f"PASS: {path}: only the kit region changed and it matches kit.json")
                continue
            how = "deleted" if deleted else "edited"
            c.worsen(_ø(ctx), f"{path}: ø operator doc {how} ({r.detail})"
                     + (" [operator override]" if ctx.operator else ""), path)
        elif r.glyph == roles.PUBLIC:
            if deleted:
                c.worsen(WARN, f"{path}: ♤ public doc deleted", path)
                continue
            text = ctx.cs.new.text(path) or ""
            problems = docgate.check(path, text, tree, ctx.man)
            if problems:
                c.worsen(FAIL, f"{path}: ♤ doc gate failed: " + "; ".join(problems), path)
            else:
                c.worsen(pass_status, f"{path}: ♤ mechanical doc gate passed; needs somnus-doc-system review", path)
        else:
            c.items.append(f"PASS: {path}: ○ living doc updated")
    c.detail = (f"ø {counts[roles.OPERATOR]}, ○ {counts[roles.LIVING]}, ♤ {counts[roles.PUBLIC]} changed" + meta_note)
    return c


# ----------------------------------------------------------------------------- (c) generated / owned

def check_generated(ctx: Ctx) -> Check:
    c = Check("c", "generated")
    owned = {**ctx.old_man.get("ownership", {}), **ctx.man.get("ownership", {})}
    for path in ctx.cs.paths:
        new, old = ctx.cs.new.text(path), ctx.cs.old.text(path)
        meta = owned.get(path)
        k = agents.adapter_for_path(path)
        if (meta and meta.get("class") == M.GENERATED) or (k and (ctx.man["adapters"].get(k) or ctx.old_man["adapters"].get(k))):
            if k and ctx.man["adapters"].get(k):
                if new != agents.ADAPTERS[k]["text"]:
                    what = "deleted" if new is None else "hand-edited"
                    c.worsen(FAIL, f"{path}: generated by somnus-kit (adapters.{k}), {what}; run `somnus-kit agents`", path)
            elif k and new is not None and not ctx.man["adapters"].get(k):
                c.worsen(WARN, f"{path}: adapter {k} is disabled in kit.json but the file changed", path)
            elif meta and not k:
                c.worsen(FAIL, f"{path}: generated by {meta.get('owner')}, changed by hand", path)
        reg_new, reg_old = agents.extract(new), agents.extract(old)
        if reg_new is not None and reg_new != agents.render(ctx.man):
            c.worsen(FAIL, f"{path}: somnus-kit region hand-edited (re-render with `somnus-kit agents`)", path)
        elif reg_old is not None and reg_new is None and new is not None:
            c.worsen(FAIL, f"{path}: somnus-kit region removed", path)
        for g in ctx.man.get("generated", []):
            if globs.match(path, g["glob"]):
                c.worsen(FAIL, f"{path}: declared generated by {g.get('owner', '?')} ({g['glob']}); regenerate, do not edit", path)
                break
        else:
            head = "\n".join((old or "").splitlines()[:10])
            if old is not None and GEN_MARK.search(head) and path not in ctx.kit_maintained and not meta:
                c.worsen(WARN, f"{path}: header says generated/do-not-edit and the file changed", path)
    c.detail = f"{len(c.files)} owned/generated file(s) touched" if c.files else "no generated or owned files touched"
    return c


# ----------------------------------------------------------------------------- (d) metadata

def check_metadata(ctx: Ctx, scratch: Path) -> Check:
    c = Check("d", "metadata")
    toml = ctx.cs.new.text(".somnus.toml")
    if toml is None:
        c.status, c.detail = SKIP, "no .somnus.toml in the tree: headers not required"
        return c
    if not ctx.man["metadata"].get("require_on_new", True):
        c.status, c.detail = SKIP, "kit.json metadata.require_on_new = false"
        return c
    if not metadata.available():
        c.status, c.detail = NOT_RUN, f"somnus-metadata not importable ({libs.error('somnus')})"
        return c
    pol = metadata.Policy(toml, scratch)
    if pol.error:
        c.worsen(FAIL, f".somnus.toml invalid: {pol.error}", ".somnus.toml")
        c.detail = "config error"
        return c
    sev = ctx.man["metadata"].get("severity", FAIL)
    checked = 0
    for path in ctx.cs.added():
        text = ctx.cs.new.text(path)
        if path in ctx.kit_maintained or not pol.requires(path, text):
            continue
        checked += 1
        side = ctx.cs.new.text(path + ".somnus.yaml")
        errs = metadata.header_errors(path, text, side)
        if errs:
            c.worsen(sev, f"{path}: new file without a valid somnus header ({errs[0]}); `somnus stamp {path} --write`", path)
    c.detail = f"{checked} new file(s) require a header; {len(c.files)} missing or invalid"
    return c


# ----------------------------------------------------------------------------- (e) debug

def check_debug(ctx: Ctx, scratch_old: Path, scratch_new: Path) -> Check:
    c = Check("e", "debug")
    py = [p for p in ctx.cs.paths if p.endswith(".py")]
    if not ctx.man["debug"].get("enabled", True):
        c.status, c.detail = SKIP, "kit.json debug.enabled = false"
        return c
    if not py:
        c.status, c.detail = SKIP, "no Python files changed"
        return c
    if not debug.available():
        c.status, c.detail = NOT_RUN, f"somnus_debug not importable ({libs.error('somnus_debug.doctor.core')})"
        return c
    base = None
    if ctx.session and ctx.session.get("debug_baseline"):
        p = Path(ctx.session["debug_baseline"]["path"])
        if p.is_file():
            base = json.loads(p.read_text(encoding="utf-8"))
            src = f"session baseline {p.name}"
    if base is None:
        old_root = ctx.cs.old.materialize(scratch_old)
        base = debug.scan(old_root, debug.find_config(old_root, ctx.old_man))
        src = f"scan of {ctx.cs.old.label}"
    new_root = ctx.cs.new.materialize(scratch_new)
    cur = debug.scan(new_root, debug.find_config(new_root, ctx.man))
    r = debug.regressions(base, cur, py)
    scanned = set(cur.get("files", []))
    uncovered = [p for p in py if p not in scanned and ctx.cs.new.read(p) is not None]
    if uncovered:
        c.worsen(WARN, f"{len(uncovered)} changed Python file(s) outside the doctor's include/ignore config: "
                 + ", ".join(uncovered[:5]))
    for line in r["fail"]:
        c.worsen(FAIL, line)
    for line in r["warn"]:
        c.worsen(WARN, line)
    c.files = sorted(set(r["files_fail"]) | set(r["files_warn"]))
    b, a = r["summary_before"], r["summary_after"]
    c.detail = (f"vs {src}: critical {b['critical_issues']}->{a['critical_issues']}, serious {b['serious_issues']}->"
                f"{a['serious_issues']}, total {b['total_issues']}->{a['total_issues']}, cycles "
                f"{b['dependency_cycles']}->{a['dependency_cycles']}")
    return c


# ----------------------------------------------------------------------------- (f) filetree + translations

def check_drift(ctx: Ctx) -> Check:
    c = Check("f", "drift")
    tree = ctx.cs.new.files()
    parts = []
    ft = ctx.cs.new.text("filetree.md")
    if ft is not None:
        listed = filetree.parse_tree(ft)
        if not listed:
            c.worsen(WARN, "filetree.md has no fenced tree block", "filetree.md")
        else:
            for p in ctx.cs.added():
                if p not in listed and p != "filetree.md":
                    c.worsen(WARN, f"{p}: added but not in filetree.md", p)
            for p in ctx.cs.deleted():
                if p in listed:
                    c.worsen(WARN, f"{p}: deleted but still listed in filetree.md", p)
            parts.append(f"filetree.md lists {len(listed)} path(s)")
    wiki = {p: ctx.cs.new.text(p) or "" for p in tree if re.search(r"(^|/)WIKI(\.[^/]+)?\.md$", p)}
    if wiki:
        stale = filetree.stale_translations(wiki)
        touched = set(ctx.cs.paths)
        for path, why in stale:
            src = re.sub(r"WIKI\.[^/]+\.md$", "WIKI.md", path)
            if src in touched or path in touched:
                c.worsen(WARN, f"{path}: stale translation ({why})", path)
            else:
                c.items.append(f"INFO: {path}: stale before this change ({why})")
        parts.append(f"{len(wiki)} wiki file(s), {len(stale)} stale translation(s)")
    if not parts:
        c.status, c.detail = SKIP, "no filetree.md and no WIKI*.md"
    else:
        c.detail = "; ".join(parts)
    return c


# ----------------------------------------------------------------------------- (g) protected

def check_protected(ctx: Ctx) -> Check:
    c = Check("g", "protected")
    pats = sorted(set(ctx.old_man.get("protected", [])) | set(ctx.man.get("protected", [])))
    for path in ctx.cs.paths:
        for pat in pats:
            if globs.match(path, pat):
                c.worsen(_ø(ctx), f"{path}: protected by kit.json ({pat})" + (" [operator override]" if ctx.operator else ""), path)
                break
    c.detail = f"{len(c.files)} protected path(s) touched of {len(pats)} pattern(s)"
    return c


# ----------------------------------------------------------------------------- driver

def run(repo: Path, cs: ChangeSet, session: Optional[dict], *, operator: bool = False,
        rh: Optional[home.RepoHome] = None) -> dict:
    rh = rh or home.RepoHome(repo)
    repo_man = M.load(repo)
    man = _manifest_from(cs.new, repo_man)
    old_man = _manifest_from(cs.old, man)
    ctx = Ctx(repo, cs, man, old_man, session, operator, rh)
    ctx.kit_maintained = kit_maintained(ctx)
    checks = [check_scope(ctx), check_roles(ctx), check_generated(ctx)]
    with rh.scratch() as s1, rh.scratch() as s2, rh.scratch() as s3:
        checks.append(check_metadata(ctx, s1))
        checks.append(check_debug(ctx, s2, s3))
    checks += [check_drift(ctx), check_protected(ctx)]
    status = PASS
    for c in checks:
        if RANK[c.status] > RANK[status]:
            status = FAIL if c.status == FAIL else WARN
    return {
        "schema": SCHEMA, "kit_version": KIT_VERSION, "status": status, "mode": cs.mode,
        "repo": str(repo), "repo_id": rh.id, "session": session["id"] if session else None,
        "operator": operator, "base": cs.old.label, "target": cs.new.label,
        "changed": [{"status": ch.status, "path": ch.path, **({"old_path": ch.old_path} if ch.old_path else {})}
                    for ch in cs.changes],
        "libs": libs.versions(),
        "checks": [asdict(c) for c in checks],
    }


def human(rep: dict) -> str:
    out = [f"somnus-kit verify: {rep['status']}  ({rep['mode']}: {rep['base']} -> {rep['target']}"
           + (f", session {rep['session']}" if rep["session"] else "") + ")",
           f"changed: {len(rep['changed'])} file(s)" + (" [operator]" if rep["operator"] else ""), ""]
    for c in rep["checks"]:
        out.append(f"[{c['status']:<7}] ({c['id']}) {c['name']}: {c['detail']}")
        for it in c["items"]:
            out.append(f"            {it}")
    return "\n".join(out) + "\n"


def exit_code(rep: dict) -> int:
    return 1 if rep["status"] == FAIL else 0
