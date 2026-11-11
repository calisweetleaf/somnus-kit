"""somnus-kit: `init` lays per-codebase state down once; `check` reruns every tool against it.

Exit codes: 0 PASS/WARN, 1 FAIL, 2 usage or precondition error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import steps
from .core import (FAIL, KIT_DIR, KIT_VERSION, MANIFEST_SCHEMA, RESULT_SCHEMA, Ledger, OwnershipError, StepResult,
                   now_iso, overall, read_json, write_json)

DEFAULT_GITHUB_ASSETS = os.environ.get(
    "SOMNUS_KIT_GITHUB_ASSETS", "/workspace/work/ghdev/somnus-github-development/somnus-github-development/assets")
DEFAULT_WIKI_TEMPLATE = os.environ.get("SOMNUS_KIT_WIKI_TEMPLATE", "/workspace/work/ghdev/wiki-template")


def _report(root: Path, mode: str, results: list[StepResult], started: str) -> dict:
    status = overall(results)
    payload = {"schema": RESULT_SCHEMA, "mode": mode, "kit_version": KIT_VERSION, "root": str(root),
               "started": started, "finished": now_iso(), "status": status,
               "steps": [r.to_json() for r in results]}
    state = root / KIT_DIR / "state"
    stamp = started.replace(":", "").replace("-", "")[:15]
    write_json(state / f"{mode}-{stamp}.json", payload)
    write_json(state / f"{mode}-latest.json", payload)
    lines = [f"# somnus-kit {mode}: {status}", "", f"- root: `{root}`", f"- started: {started}",
             f"- finished: {payload['finished']}", "", "| step | status | summary |", "| --- | --- | --- |"]
    for r in results:
        lines.append(f"| {r.step} | {r.status} | {r.summary.replace('|', '/')} |")
    lines.append("")
    for r in results:
        if r.findings:
            lines.append(f"## {r.step}")
            lines += [f"- {f}" for f in r.findings]
            lines.append("")
    lines.append("## Commands executed")
    lines.append("")
    for r in results:
        for c in r.commands:
            argv = " ".join(c.argv)
            lines.append(f"- `{argv[:160]}` -> exit {c.exit} ({c.seconds}s){' ' + c.error if c.error else ''}")
    (state / ("CHECK.md" if mode == "check" else "INIT.md")).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload


def _print(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2))
        return
    print(f"somnus-kit {payload['mode']}: {payload['status']}  ({payload['root']})")
    for s in payload["steps"]:
        print(f"  {s['status']:<8} {s['step']:<14} {s['summary']}")
        for f in s["findings"]:
            print(f"           {f}")


def cmd_init(ns: argparse.Namespace) -> int:
    root = Path(ns.repo).resolve()
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2
    kit = root / KIT_DIR
    if (kit / "kit.json").exists() and not ns.force:
        print(f"{kit}/kit.json exists; this repo is already initialised (use `check`, or --force to re-init)", file=sys.stderr)
        return 2
    started = now_iso()
    opts = {"plot_dist": ns.plot_dist, "github_assets": ns.github_assets, "wiki_template": ns.wiki_template,
            "gates": ns.gate, "github": ns.github, "no_agents_edit": ns.no_agents_edit}
    ctx = steps.Ctx(root=root, kit=kit, ledger=Ledger(root), options=opts)
    pre = StepResult("preflight")
    steps.discover_tools(ctx, pre)
    ident = steps.repo_identity(ctx, pre)
    try:
        steps.plan_ownership(ctx, ident)
    except OwnershipError as exc:
        print(f"ownership conflict, nothing written: {exc}", file=sys.stderr)
        return 2
    pre.summary = f"tools: {', '.join(sorted(ctx.tools))}; languages {ident['languages']}; {len(ctx.ledger.claims)} ownership claims, no conflicts"
    kit.mkdir(exist_ok=True)
    (kit / ".gitignore").write_text("# somnus-kit: volatile state; kit.json, baselines and maps are committable\n"
                                    "state/\nsdgc/runs/latest/\n", encoding="utf-8")
    gates = steps.detect_gates(ctx)
    adapters = {"cartographer": {"status": "configured", "name": "cartographer", "command": ns.cartographer_cmd.split(),
                                 "contract": "somnus-kit.map/1"} if ns.cartographer_cmd else
                {"status": "absent", "contract": "somnus-kit.map/1", "reference_adapter": "doctor-graph",
                 "invoke": "<command...> --root <repo> --out <dir> ; writes <dir>/map.json"}}
    ctx.manifest = {"adapters": adapters, "gates": gates}
    results = [pre]
    results.append(steps.plot_init(ctx, ident))
    results.append(steps.metadata_seed(ctx))
    results.append(steps.debug_init(ctx, ident))
    results.append(steps.sdgc_init(ctx))
    results.append(steps.github_init(ctx))
    results.append(steps.wiki_init(ctx, ident))
    results.append(steps.cartographer_run(ctx))
    results.append(steps.filetree_run(ctx, ident, seed=True))
    steps._remember_written(ctx, [f"{KIT_DIR}/filetree.generated.md", f"{KIT_DIR}/sdgc/effective-config.json"])
    ctx.ledger.record_hashes()
    manifest = {
        "schema": MANIFEST_SCHEMA, "kit_version": KIT_VERSION, "created": started,
        "repo": ident, "tools": ctx.tools,
        "order": ["preflight", "ownership-plan", "plot", "metadata-seed", "somnus-debug", "sdgc", "github", "wiki", "map", "filetree", "agents", "metadata-index", "manifest"],
        "gates": gates, "adapters": adapters,
        "baselines": {"somnus-debug": ".somnus-debug/runs/baseline.json", "sdgc": f"{KIT_DIR}/sdgc/runs/baseline",
                      "metadata": f"{KIT_DIR}/metadata/baseline.json"},
        "sources": {"github_assets": ns.github_assets, "wiki_template": ns.wiki_template},
        "ownership": ctx.ledger.to_json(),
    }
    ctx.manifest = manifest
    results.append(steps.agents_run(ctx, write=True))
    results.append(steps.metadata_init(ctx))  # last tool step: baseline sees everything the kit seeded
    write_json(kit / "kit.json", manifest)
    payload = _report(root, "init", results, started)
    _print(payload, ns.json)
    return 1 if payload["status"] == FAIL else 0


def cmd_check(ns: argparse.Namespace) -> int:
    root = Path(ns.repo).resolve()
    kit = root / KIT_DIR
    if not (kit / "kit.json").is_file():
        print(f"no {KIT_DIR}/kit.json under {root}; run `somnus-kit init {root}` first", file=sys.stderr)
        return 2
    manifest = read_json(kit / "kit.json")
    started = now_iso()
    opts = {"plot_dist": ns.plot_dist or (manifest["tools"].get("plot", {}).get("dist")),
            "gate_timeout": ns.gate_timeout}
    ctx = steps.Ctx(root=root, kit=kit, ledger=Ledger(root), options=opts, manifest=manifest)
    pre = StepResult("preflight")
    steps.discover_tools(ctx, pre)
    ident = steps.repo_identity(ctx, pre)
    drift = {k: (manifest["tools"].get(k, {}).get("version"), v.get("version")) for k, v in ctx.tools.items()
             if manifest["tools"].get(k, {}).get("version") != v.get("version")}
    if drift:
        pre.findings.append(f"[INFO] tool versions changed since init: {drift}")
    pre.summary = f"tools: {', '.join(sorted(ctx.tools))}"
    # seeded/adopted files that disappeared
    for rel, meta in manifest["ownership"].items():
        if meta["class"] in ("seeded", "adopted") and not rel.startswith("$") and "#" not in rel \
                and not (root / rel).exists():
            pre.worsen("WARN", f"{rel} ({meta['class']}, owner {meta['owner']}) no longer exists")
    selected = set(ns.only.split(",")) if ns.only else None

    def want(name: str) -> bool:
        return selected is None or name in selected

    results = [pre]
    if want("gates"):
        results.append(steps.plot_check(ctx))
    if want("metadata"):
        results.append(steps.metadata_check(ctx))
    if want("debug"):
        results.append(steps.debug_check(ctx, ident))
    if want("sdgc"):
        results.append(steps.sdgc_check(ctx))
    if want("github"):
        results.append(steps.github_check(ctx))
    if want("wiki"):
        results.append(steps.wiki_check(ctx))
    if want("map"):
        results.append(steps.cartographer_run(ctx))
    if want("filetree"):
        results.append(steps.filetree_run(ctx, ident, seed=False))
    if want("agents"):
        results.append(steps.agents_run(ctx, write=False))
    steps._remember_written(ctx, [f"{KIT_DIR}/filetree.generated.md", f"{KIT_DIR}/sdgc/effective-config.json"])
    payload = _report(root, "check", results, started)
    _print(payload, ns.json)
    return 1 if payload["status"] == FAIL else 0


def cmd_owners(ns: argparse.Namespace) -> int:
    root = Path(ns.repo).resolve()
    m = read_json(root / KIT_DIR / "kit.json")
    for rel, meta in m["ownership"].items():
        print(f"{meta['class']:<10} {meta['owner']:<46} {rel}{'/' if meta.get('tree') else ''}")
    return 0


def cmd_agents(ns: argparse.Namespace) -> int:
    """Re-render the kit region of AGENTS.md from kit.json (after editing gates or adapters there)."""
    from . import agents_section
    root = Path(ns.repo).resolve()
    m = read_json(root / KIT_DIR / "kit.json")
    print(f"AGENTS.md kit region {agents_section.upsert(root / 'AGENTS.md', agents_section.render(m))}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="somnus-kit", description=__doc__.splitlines()[0])
    ap.add_argument("--version", action="version", version=f"somnus-kit {KIT_VERSION}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init", help="lay down per-codebase state and baselines")
    i.add_argument("repo")
    i.add_argument("--gate", action="append", help="gate command run through Plot (repeatable); default: detected")
    i.add_argument("--plot-dist", help="somnus-plot checkout when `plot` is not on PATH (or $PLOT_DIST)")
    i.add_argument("--github-assets", default=DEFAULT_GITHUB_ASSETS)
    i.add_argument("--wiki-template", default=DEFAULT_WIKI_TEMPLATE)
    i.add_argument("--github", help="owner/repo when there is no git remote")
    i.add_argument("--cartographer-cmd", help="command implementing somnus-kit.map/1")
    i.add_argument("--no-agents-edit", action="store_true", help="write the AGENTS section to state/ instead of AGENTS.md")
    i.add_argument("--force", action="store_true")
    i.add_argument("--json", action="store_true")
    i.set_defaults(fn=cmd_init)
    c = sub.add_parser("check", help="rerun every tool against the recorded state")
    c.add_argument("repo", nargs="?", default=".")
    c.add_argument("--only", help="comma list: gates,metadata,debug,sdgc,github,wiki,map,filetree,agents")
    c.add_argument("--plot-dist")
    c.add_argument("--gate-timeout", type=int, default=900)
    c.add_argument("--json", action="store_true")
    c.set_defaults(fn=cmd_check)
    o = sub.add_parser("owners", help="print the one-writer-per-path table")
    o.add_argument("repo", nargs="?", default=".")
    o.set_defaults(fn=cmd_owners)
    a = sub.add_parser("agents", help="re-render the kit-owned AGENTS.md region from kit.json")
    a.add_argument("repo", nargs="?", default=".")
    a.set_defaults(fn=cmd_agents)
    ns = ap.parse_args(argv)
    return ns.fn(ns)


if __name__ == "__main__":
    raise SystemExit(main())
