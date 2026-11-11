"""`init` and `agents`: lay down the committed footprint, build out-of-tree state.

Committed (and recorded in kit.json `ownership`): `.somnus-kit/kit.json`, `.somnus.toml` (only if absent),
the AGENTS.md marked section, adapters kit.json enables, and `.github/workflows` callers when given.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path
from typing import Optional

from . import KIT_VERSION, agents, carto, debug, gitio, home, manifest as M, metadata


def _write(repo: Path, rel: str, text: str, written: list[str]) -> None:
    p = repo / rel
    if p.is_file() and p.read_text(encoding="utf-8") == text:
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    written.append(rel)


def apply_agents(repo: Path, man: dict, *, force_adapters: bool = False) -> tuple[list[str], list[str]]:
    """Render the AGENTS.md region and enabled adapters. Returns (written, warnings)."""
    written: list[str] = []
    warnings: list[str] = []
    agents_md = repo / "AGENTS.md"
    old = agents_md.read_text(encoding="utf-8") if agents_md.is_file() else None
    _write(repo, "AGENTS.md", agents.upsert(old, man), written)
    for key, spec in agents.ADAPTERS.items():
        rel = spec["path"]
        p = repo / rel
        if man["adapters"].get(key):
            if p.is_file() and p.read_text(encoding="utf-8") != spec["text"] and not force_adapters \
                    and man["ownership"].get(rel, {}).get("owner") != "somnus-kit":
                warnings.append(f"{rel} exists with operator content; not overwritten (use --force-adapters)")
                man["adapters"][key] = False
                continue
            _write(repo, rel, spec["text"], written)
            man["ownership"][rel] = {"owner": "somnus-kit", "class": M.GENERATED, "note": f"adapter {key}: pointer to AGENTS.md"}
        elif man["ownership"].get(rel, {}).get("owner") == "somnus-kit":
            if p.is_file() and p.read_text(encoding="utf-8") == spec["text"]:
                p.unlink()
                written.append(f"{rel} (removed: adapter disabled)")
            man["ownership"].pop(rel, None)
    return written, warnings


def init(repo: Path, *, adapters: list[str], callers: Optional[Path], protect: list[str], no_metadata: bool,
         cartographer: Optional[str], force_adapters: bool = False, rh: Optional[home.RepoHome] = None) -> dict:
    rh = rh or home.RepoHome(repo)
    rh.ensure("models", "debug", "logs", "sessions")
    existed = (repo / M.KIT_JSON).is_file()
    man = M.load(repo)
    remotes = gitio.remotes(repo)
    man["kit_version"] = KIT_VERSION
    man["repo"] = {"name": repo.name, "remote": remotes.get("origin") or next(iter(remotes.values()), None)}
    for a in adapters:
        if a not in agents.ADAPTERS:
            raise M.ManifestError(f"unknown adapter {a!r} (known: {', '.join(agents.ADAPTERS)})")
        man["adapters"][a] = True
    for p in protect:
        if p not in man["protected"]:
            man["protected"].append(p)
    written: list[str] = []
    warnings: list[str] = []

    man["ownership"][M.KIT_JSON] = {"owner": "somnus-kit", "class": M.CONFIG, "note": "manifest and ownership table; operator edits, agents never"}
    toml = repo / ".somnus.toml"
    if toml.is_file():
        man["ownership"].setdefault(".somnus.toml", {"owner": "somnus-metadata", "class": M.ADOPTED, "note": "existed before somnus-kit"})
    elif not no_metadata:
        if metadata.available():
            _write(repo, ".somnus.toml", metadata.seed_toml([".somnus.toml", ".somnus-kit/**", ".github/**", "WIKI*.md", "CLAUDE.md"]), written)
            man["ownership"][".somnus.toml"] = {"owner": "somnus-metadata", "class": M.CONFIG, "note": "seeded by somnus-kit init"}
        else:
            warnings.append("somnus-metadata not importable: .somnus.toml not seeded")
    if no_metadata:
        man["metadata"]["require_on_new"] = False
    man["ownership"]["AGENTS.md#somnus-kit"] = {"owner": "somnus-kit", "class": M.REGION, "note": "marked section only; the rest is operator text (ø)"}

    if callers:
        for src in sorted(Path(callers).glob("*.y*ml")):
            rel = f".github/workflows/{src.name}"
            if (repo / rel).is_file() and man["ownership"].get(rel, {}).get("owner") != "somnus-github-development":
                warnings.append(f"{rel} exists and is not owned by somnus-github-development; left alone")
                continue
            _write(repo, rel, src.read_text(encoding="utf-8"), written)
            man["ownership"][rel] = {"owner": "somnus-github-development", "class": M.GENERATED, "note": f"caller from {src.name}"}

    # agents section + adapters need the final ownership table, so render after it is complete
    w, warn = apply_agents(repo, man, force_adapters=force_adapters)
    warnings += warn
    w2, _ = apply_agents(repo, man, force_adapters=force_adapters)  # second pass: region now lists adapters
    for x in w + w2:
        if x not in written:
            written.append(x)
    before = (repo / M.KIT_JSON).read_text(encoding="utf-8") if existed else None
    if before != M.dumps(man):
        M.save(repo, man)
        written.append(M.KIT_JSON)

    # out-of-tree state
    state: dict = {"model": None, "debug": None}
    c = carto.Cartographer.locate(man, cartographer)
    if c.usable:
        state["model"] = {**c.build(repo, rh.models, man["cartographer"].get("profile", "auto")), "dir": str(rh.models),
                          "features": c.features}
    else:
        state["model"] = {"ok": False, "reason": c.reason}
    if debug.available() and man["debug"].get("enabled", True):
        scan = debug.scan(repo, debug.find_config(repo, man))
        (rh.debug / "init.json").write_text(json.dumps(scan, indent=1) + "\n", encoding="utf-8")
        state["debug"] = {"path": str(rh.debug / "init.json"), "summary": scan["summary"]}
    report = {"schema": "somnus-kit.init/1", "kit_version": KIT_VERSION, "repo": str(repo), "repo_id": rh.id,
              "home": str(rh.home), "reinit": existed, "written_in_tree": sorted(set(written)),
              "warnings": warnings, "out_of_tree": state,
              "at": dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")}
    (rh.logs / "init-latest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
