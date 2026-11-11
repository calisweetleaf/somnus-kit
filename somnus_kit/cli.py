"""somnus-kit command line.

  somnus-kit init <repo> [--adapters claude,copilot] [--callers DIR] [--protect GLOB]... [--no-metadata]
  somnus-kit brief <repo> --task TEXT [--scope GLOB]... [--budget-chars N] [--json]
  somnus-kit verify <repo> [--session ID | --staged | --range A..B] [--operator] [--json]
  somnus-kit end <repo> [--session ID]
  somnus-kit agents <repo>
  somnus-kit hooks install|uninstall|status <repo>
  somnus-kit fleet status|check [--fleet fleet.json] [--json]
  somnus-kit where <repo>

Exit codes: 0 PASS or WARN, 1 FAIL, 2 usage or config error.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

from . import KIT_VERSION, fleet, gitio, home, hooks, initcmd, libs, manifest as M, session, snapshot, verify


class UsageError(Exception):
    pass


def _repo(arg: str) -> Path:
    p = Path(arg).expanduser().resolve()
    if not p.exists():
        raise UsageError(f"{arg}: no such directory")
    top = gitio.toplevel(p)
    if top is None:
        raise UsageError(f"{arg}: not inside a git repository")
    return top


def _print(obj, as_json: bool, text: str) -> None:
    sys.stdout.write(json.dumps(obj, indent=2, ensure_ascii=False) + "\n" if as_json else text)


def cmd_init(a) -> int:
    repo = _repo(a.repo)
    adapters = [x for x in (a.adapters or "").split(",") if x]
    rep = initcmd.init(repo, adapters=adapters, callers=Path(a.callers) if a.callers else None, protect=a.protect or [],
                       no_metadata=a.no_metadata, cartographer=a.cartographer, force_adapters=a.force_adapters)
    lines = [f"somnus-kit init: {repo} ({rep['repo_id']})" + (" [re-init]" if rep["reinit"] else ""),
             "written in tree: " + (", ".join(rep["written_in_tree"]) or "nothing (already current)")]
    m = rep["out_of_tree"]["model"]
    lines.append(f"cartographer model: {'built (' + m.get('profile', '?') + ') -> ' + m['dir'] if m.get('ok') else 'not built: ' + str(m.get('reason') or m.get('attempts'))}")
    d = rep["out_of_tree"]["debug"]
    lines.append(f"debug baseline: {d['path'] + ' ' + json.dumps(d['summary']) if d else 'not run'}")
    lines += [f"warning: {w}" for w in rep["warnings"]]
    _print(rep, a.json, "\n".join(lines) + "\n")
    return 0


def cmd_brief(a) -> int:
    repo = _repo(a.repo)
    rh = home.RepoHome(repo)
    data = session.brief(repo, a.task, a.scope or [], cartographer=a.cartographer, budget=a.budget_chars, rh=rh)
    path = rh.sessions / f"{data['id']}.json"
    if a.json:
        slim = {k: v for k, v in data.items() if k != "files"}
        slim["session_file"] = str(path)
        _print(slim, True, "")
    else:
        man = M.load(repo)
        snap = snapshot.Snapshot(repo, "worktree")
        rules = session.rules_text(repo, man, sorted(data["files"]), snap)
        sys.stdout.write(session.render(data, rules, str(path)))
    return 0


def cmd_verify(a) -> int:
    repo = _repo(a.repo)
    rh = home.RepoHome(repo)
    modes = sum(bool(x) for x in (a.staged, a.range))
    if modes > 1:
        raise UsageError("--staged and --range are exclusive")
    operator = a.operator or os.environ.get("SOMNUS_KIT_OPERATOR") == "1"
    sess = None
    if a.session:
        sess = session.load(rh, a.session)
    elif not a.range and session.active_id(rh):
        sess = session.load(rh)
    if a.staged:
        cs = snapshot.from_staged(repo)
    elif a.range:
        cs = snapshot.from_range(repo, a.range)
    elif sess:
        cs = snapshot.from_session(repo, sess)
    else:
        cs = snapshot.from_worktree(repo)
    rep = verify.run(repo, cs, sess, operator=operator, rh=rh)
    rh.ensure("logs")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    (rh.logs / f"verify-{stamp}.json").write_text(json.dumps(rep, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _print(rep, a.json, verify.human(rep))
    return verify.exit_code(rep)


def cmd_end(a) -> int:
    repo = _repo(a.repo)
    sid = session.end(home.RepoHome(repo), a.session)
    print(f"closed session {sid}" if sid else "no active session")
    return 0


def cmd_agents(a) -> int:
    repo = _repo(a.repo)
    man = M.load(repo, required=True)
    written, warnings = initcmd.apply_agents(repo, man, force_adapters=a.force_adapters)
    written2, _ = initcmd.apply_agents(repo, man, force_adapters=a.force_adapters)
    cur = (repo / M.KIT_JSON).read_text(encoding="utf-8")
    if cur != M.dumps(man):
        M.save(repo, man)
        written.append(M.KIT_JSON)
    print("written: " + (", ".join(sorted(set(written + written2))) or "nothing (already current)"))
    for w in warnings:
        print(f"warning: {w}")
    return 0


def cmd_hooks(a) -> int:
    repo = _repo(a.repo)
    fn = {"install": hooks.install, "uninstall": hooks.uninstall, "status": hooks.status}[a.action]
    rep = fn(repo)
    _print(rep, True, "")
    return 0


def cmd_fleet(a) -> int:
    rep = fleet.status(a.fleet) if a.action == "status" else fleet.check(a.fleet)
    if a.json:
        _print(rep, True, "")
    else:
        lines = [f"fleet {a.action}: {rep['fleet']}" + (f"  status {rep['status']}" if "status" in rep else "")]
        for r in rep["repos"]:
            if a.action == "status":
                lines.append(f"  [{r['state']:<9}] {r['path']}  " + (
                    f"id={r['repo_id']} head={r['head']} init={r['initialised']} hooks={r['hooks']} "
                    f"session={r['active_session'] or '-'} dirty={r['dirty']}" if r["state"] != "MISSING" else r["detail"]))
            else:
                lines.append(f"  [{r['status']:<5}] {r['path']}  " + (
                    f"{r.get('mode')} changed={r.get('changed')} failed={','.join(r.get('failed_checks') or []) or '-'}"
                    if r["status"] != "ERROR" else r["detail"]))
        _print(rep, False, "\n".join(lines) + "\n")
    if a.action == "check":
        return 1 if rep["status"] == verify.FAIL else 0
    return 0


def cmd_where(a) -> int:
    repo = _repo(a.repo)
    rh = home.RepoHome(repo)
    _print({"repo": str(repo), "repo_id": rh.id, "home": str(rh.home),
            **{k: str(getattr(rh, k)) for k in ("models", "sessions", "debug", "logs", "hooks")},
            "active_session": session.active_id(rh), "libs": libs.versions()}, True, "")
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="somnus-kit", description="Repo guard for agent work (v%s)" % KIT_VERSION)
    p.add_argument("--version", action="version", version=KIT_VERSION)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init")
    s.add_argument("repo")
    s.add_argument("--adapters", help="comma list: claude,copilot")
    s.add_argument("--callers", help="dir of GitHub caller workflows to install under .github/workflows/")
    s.add_argument("--protect", action="append", help="extra protected glob (repeatable)")
    s.add_argument("--no-metadata", action="store_true", help="do not seed .somnus.toml; do not require headers")
    s.add_argument("--cartographer", help="Cartographer checkout (contains scripts/codebase/)")
    s.add_argument("--force-adapters", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("brief")
    s.add_argument("repo")
    s.add_argument("--task", required=True)
    s.add_argument("--scope", action="append")
    s.add_argument("--budget-chars", type=int, default=6000)
    s.add_argument("--cartographer")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_brief)

    s = sub.add_parser("verify")
    s.add_argument("repo")
    s.add_argument("--session")
    s.add_argument("--staged", action="store_true")
    s.add_argument("--range")
    s.add_argument("--operator", action="store_true", help="operator commit: ø/protected become WARN (also SOMNUS_KIT_OPERATOR=1)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_verify)

    s = sub.add_parser("end")
    s.add_argument("repo")
    s.add_argument("--session")
    s.set_defaults(fn=cmd_end)

    s = sub.add_parser("agents")
    s.add_argument("repo")
    s.add_argument("--force-adapters", action="store_true")
    s.set_defaults(fn=cmd_agents)

    s = sub.add_parser("hooks")
    s.add_argument("action", choices=["install", "uninstall", "status"])
    s.add_argument("repo")
    s.set_defaults(fn=cmd_hooks)

    s = sub.add_parser("fleet")
    s.add_argument("action", choices=["status", "check"])
    s.add_argument("--fleet")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_fleet)

    s = sub.add_parser("where")
    s.add_argument("repo")
    s.set_defaults(fn=cmd_where)
    return p


def main(argv=None) -> int:
    try:
        a = parser().parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code not in (0, None) else 0
    try:
        return a.fn(a)
    except (UsageError, M.ManifestError, gitio.GitError, session.SessionError, fleet.FleetError) as exc:
        print(f"somnus-kit: {exc}", file=sys.stderr)
        return 2
