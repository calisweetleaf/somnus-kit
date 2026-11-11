"""Codebase-map adapter slot (contract somnus-kit.map/1).

The kit never links to a mapper. It runs a registered command:

    <command...> --root <repo> --out <dir>

which must exit 0 and write <dir>/map.json:

    {
      "schema": "somnus-kit.map/1",
      "adapter": {"name": str, "version": str},
      "root": str,                      # absolute repo root the map describes
      "generated_at": str,              # ISO-8601 with offset
      "nodes": [{"id": str, "kind": "file|module|package|symbol", "path": str|null, "lang": str|null}],
      "edges": [{"from": str, "to": str, "kind": "imports|calls|contains|references"}],
      "entrypoints": [str],             # node ids
      "notes": [str]                    # limits the adapter itself declares
    }

Node ids are stable strings (repo-relative path for files, dotted name for modules).
The kit validates shape, stores the map at .somnus-kit/map/<adapter>/map.json, and
diffs node/edge sets against the previous map. Daeron's Cartographer plugs in by
registering its command in kit.json -> adapters.cartographer.command; nothing else
in the kit changes.

`doctor-graph` below is a REFERENCE adapter only: it converts somnus-debug's import
graph into the contract so the slot is exercised with real data. It is not Cartographer.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

CONTRACT = "somnus-kit.map/1"
NODE_KINDS = {"file", "module", "package", "symbol"}
EDGE_KINDS = {"imports", "calls", "contains", "references"}


def validate_map(data: dict) -> list[str]:
    errs: list[str] = []
    if data.get("schema") != CONTRACT:
        errs.append(f"schema must be {CONTRACT}")
    for key, typ in (("adapter", dict), ("root", str), ("generated_at", str), ("nodes", list),
                     ("edges", list), ("entrypoints", list), ("notes", list)):
        if not isinstance(data.get(key), typ):
            errs.append(f"{key} must be {typ.__name__}")
    if errs:
        return errs
    ids = set()
    for i, n in enumerate(data["nodes"]):
        if not isinstance(n, dict) or not isinstance(n.get("id"), str):
            errs.append(f"nodes[{i}].id missing")
            continue
        if n.get("kind") not in NODE_KINDS:
            errs.append(f"nodes[{i}].kind invalid: {n.get('kind')}")
        ids.add(n["id"])
    for i, e in enumerate(data["edges"]):
        if not isinstance(e, dict) or e.get("kind") not in EDGE_KINDS:
            errs.append(f"edges[{i}].kind invalid")
            continue
        for end in ("from", "to"):
            if e.get(end) not in ids:
                errs.append(f"edges[{i}].{end} references unknown node {e.get(end)!r}")
    for ep in data["entrypoints"]:
        if ep not in ids:
            errs.append(f"entrypoint {ep!r} is not a node")
    return errs[:50]


def diff_maps(old: dict | None, new: dict) -> dict:
    def nodes(m):
        return {n["id"] for n in m.get("nodes", [])} if m else set()

    def edges(m):
        return {(e["from"], e["to"], e["kind"]) for e in m.get("edges", [])} if m else set()

    on, nn, oe, ne = nodes(old), nodes(new), edges(old), edges(new)
    return {"nodes_added": len(nn - on), "nodes_removed": len(on - nn),
            "edges_added": len(ne - oe), "edges_removed": len(oe - ne),
            "first_run": old is None}


# --- reference adapter: somnus-debug import graph -> somnus-kit.map/1 -----------------------

def doctor_graph_main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="somnus-kit-adapter-doctor-graph")
    ap.add_argument("--root", required=True)
    ap.add_argument("--out", required=True)
    ns = ap.parse_args(argv)
    root = Path(ns.root).resolve()
    src = None
    for cand in (root / ".somnus-debug/runs/latest.json", root / ".somnus-debug/runs/baseline.json"):
        if cand.is_file():
            src = cand
            break
    if src is None:
        print("doctor-graph: no somnus-debug JSON report under .somnus-debug/runs/", file=sys.stderr)
        return 3
    graph = json.loads(src.read_text(encoding="utf-8")).get("dependency_graph") or {}
    nodes = {p: {"id": p, "kind": "file", "path": p, "lang": "python"} for p in graph.get("nodes", [])}
    edges = []
    for e in graph.get("edges", []):
        s, t = e.get("source_file"), e.get("target_file")
        if s in nodes and t in nodes and e.get("classification") == "local":
            edges.append({"from": s, "to": t, "kind": "imports"})
    edges = [dict(t) for t in sorted({tuple(sorted(x.items())) for x in edges})]
    entry = sorted(p for p in nodes if p.endswith("__main__.py") or p.endswith("/cli.py"))
    out = {
        "schema": CONTRACT,
        "adapter": {"name": "doctor-graph", "version": "0.1.0"},
        "root": str(root),
        "generated_at": _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds"),
        "nodes": sorted(nodes.values(), key=lambda n: n["id"]),
        "edges": edges,
        "entrypoints": entry,
        "notes": [f"derived from {src.relative_to(root)} (somnus-debug static import graph)",
                  "Python files only; local import edges only; no call graph; reference adapter, not Cartographer"],
    }
    outdir = Path(ns.out)
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "map.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"doctor-graph: {len(out['nodes'])} nodes, {len(out['edges'])} edges -> {outdir / 'map.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(doctor_graph_main())
