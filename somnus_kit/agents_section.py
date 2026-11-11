"""The kit-owned region of AGENTS.md. The kit writes only between the markers."""

from __future__ import annotations

from pathlib import Path

BEGIN = "<!-- somnus-kit:begin v1 (owned by somnus-kit; edits inside are overwritten by `somnus-kit agents`) -->"
END = "<!-- somnus-kit:end -->"


def render(manifest: dict) -> str:
    gates = manifest.get("gates", [])
    gate_lines = "\n".join(f"   - `{g['cmd']}`" for g in gates) or "   - (none declared; add to `.somnus-kit/kit.json` gates, then `somnus-kit agents`)"
    gen = [p for p, m in manifest.get("ownership", {}).items() if m["class"] == "generated"]
    gen_lines = "\n".join(f"- `{p}` ({manifest['ownership'][p]['owner']})" for p in sorted(gen))
    carto = manifest.get("adapters", {}).get("cartographer", {})
    carto_line = (f"registered: `{' '.join(carto['command'])}`" if carto.get("status") == "configured"
                  else "not registered; the `doctor-graph` reference adapter fills the slot until Cartographer is wired")
    return f"""{BEGIN}
## Repository maintenance loop (somnus-kit)

This repository is maintained with Daeron's own tools. Run the loop; do not hand-roll replacements.

1. **Enter**: `somnus-kit check` from the repo root. Read `.somnus-kit/state/CHECK.md` first. A FAIL there is the first
   job of the session; a WARN is work to schedule, not to ignore.
2. **Gates** run through Somnus Plot (`.plot/`, view `gates`), never ad hoc:
{gate_lines}
   Reproduce one gate by hand with `plot run gates sh -c 'cd <repo> && <cmd>'`.
3. **Metadata**: every new file that can carry a header gets one: `somnus stamp PATH` (preview) then
   `somnus stamp PATH --write`. Excluded paths live in `.somnus.toml` `[index].exclude`; do not stamp those.
   Use `somnus q --text "..."` before reading the tree by hand.
4. **Diagnostics**: `.somnus-debug/runs/baseline.json` is the accepted somnus-debug state. A rise in critical or
   serious issues, syntax errors or dependency cycles is a regression; fix it or record why in the operator docs.
5. **Discovery graph**: SDGC compiles into `.somnus-kit/sdgc/runs/`; `sdgc compare` against `baseline` must not
   regress. SDGC output stays in its run directory; never copy generated README/AGENTS over root files.
6. **Topology**: after adding, moving or deleting files, reconcile root `filetree.md` against
   `.somnus-kit/filetree.generated.md` (keep ø ○ ♤ roles; keep proposals out of the actual-tree block).
7. **Wiki**: edit `WIKI.md` first; then re-translate `WIKI.zh-CN.md` and set its `source_sha256` to the sha256 of
   the WIKI.md body (everything after the first `-->`). Every claim needs an evidence pointer.
8. **Codebase map**: {carto_line}. Map at `.somnus-kit/map/<adapter>/map.json` (contract `somnus-kit.map/1`).
9. **Ownership**: one writer per file (`somnus-kit owners`). Never hand-edit generated files:
{gen_lines}
   Seeded files (WIKI*, filetree.md, `.github/`, `.somnus.toml`, `.somnus-debug/python_doctor.yaml`) are yours after
   seeding; the kit never rewrites them.
10. **Publication**: never push, tag or publish from this loop. Release is an operator act.
{END}"""


def upsert(path: Path, block: str) -> str:
    """Return 'created' | 'appended' | 'replaced' | 'unchanged'."""
    if not path.exists():
        path.write_text(f"# AGENTS.md\n\n{block}\n", encoding="utf-8")
        return "created"
    text = path.read_text(encoding="utf-8")
    if BEGIN.split(" v1")[0] in text and END in text:
        start = text.index(BEGIN.split(" v1")[0])
        stop = text.index(END, start) + len(END)
        new = text[:start] + block + text[stop:]
        if new == text:
            return "unchanged"
        path.write_text(new, encoding="utf-8")
        return "replaced"
    sep = "" if text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
    path.write_text(text + sep + block + "\n", encoding="utf-8")
    return "appended"


def present(path: Path) -> bool:
    if not path.exists():
        return False
    t = path.read_text(encoding="utf-8")
    return BEGIN.split(" v1")[0] in t and END in t
