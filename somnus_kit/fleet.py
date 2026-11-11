"""`fleet status|check` over local clones listed in fleet.json `{ "repos": [{ "path", "remote", "profile" }] }`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from . import gitio, home, hooks, manifest as M, session, snapshot, verify


class FleetError(ValueError):
    pass


def load(path: Optional[str]) -> tuple[Path, list[dict]]:
    p = Path(path).expanduser() if path else home.somnus_home() / "fleet.json"
    if not p.is_file():
        raise FleetError(f"fleet file not found: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FleetError(f"{p}: {exc}") from exc
    repos = data.get("repos") if isinstance(data, dict) else None
    if not isinstance(repos, list) or not all(isinstance(r, dict) and r.get("path") for r in repos):
        raise FleetError(f"{p}: expected {{\"repos\": [{{\"path\": ...}}]}}")
    return p, repos


def _resolve(base: Path, entry: dict) -> Path:
    p = Path(entry["path"]).expanduser()
    return (p if p.is_absolute() else base.parent / p).resolve()


def status(path: Optional[str]) -> dict:
    fp, repos = load(path)
    rows = []
    for e in repos:
        root = _resolve(fp, e)
        row = {"path": str(root), "remote": e.get("remote"), "profile": e.get("profile")}
        top = gitio.toplevel(root) if root.is_dir() else None
        if top is None:
            row.update(state="MISSING", detail="not a git clone")
            rows.append(row)
            continue
        rh = home.RepoHome(top)
        actual = gitio.remotes(top).get("origin")
        cs = snapshot.from_worktree(top)
        row.update(state="OK", repo_id=rh.id, head=(gitio.head(top) or "")[:12], branch=gitio.branch(top),
                   initialised=(top / M.KIT_JSON).is_file(), hooks=hooks.status(top, rh)["installed"],
                   active_session=session.active_id(rh), dirty=len(cs.changes),
                   remote_matches=(e.get("remote") is None or e.get("remote") == actual), actual_remote=actual)
        if not row["initialised"] or not row["hooks"] or not row["remote_matches"]:
            row["state"] = "ATTENTION"
        rows.append(row)
    return {"fleet": str(fp), "repos": rows}


def check(path: Optional[str]) -> dict:
    fp, repos = load(path)
    rows, overall = [], verify.PASS
    for e in repos:
        root = _resolve(fp, e)
        top = gitio.toplevel(root) if root.is_dir() else None
        if top is None:
            rows.append({"path": str(root), "status": "ERROR", "detail": "not a git clone"})
            overall = verify.FAIL
            continue
        rh = home.RepoHome(top)
        try:
            sess = session.load(rh) if session.active_id(rh) else None
            cs = snapshot.from_session(top, sess) if sess else snapshot.from_worktree(top)
            rep = verify.run(top, cs, sess, rh=rh)
        except (M.ManifestError, gitio.GitError, session.SessionError) as exc:
            rows.append({"path": str(top), "status": "ERROR", "detail": str(exc)})
            overall = verify.FAIL
            continue
        failed = [f"{c['id']}.{c['name']}" for c in rep["checks"] if c["status"] == verify.FAIL]
        rows.append({"path": str(top), "repo_id": rh.id, "status": rep["status"], "mode": rep["mode"],
                     "changed": len(rep["changed"]), "failed_checks": failed})
        if verify.RANK[rep["status"]] > verify.RANK[overall]:
            overall = rep["status"]
    return {"fleet": str(fp), "status": overall, "repos": rows}
