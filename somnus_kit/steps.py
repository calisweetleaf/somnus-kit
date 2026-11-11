"""The ordered steps of `init` and `check`. One function pair per tool.

Order (DESIGN.md §5): preflight -> plot -> metadata -> debug -> sdgc -> github -> wiki
-> cartographer -> filetree -> agents -> manifest. Filetree runs after every seeding step
so the inventory includes what the kit laid down; the manifest is written last so a
crashed init leaves no manifest and can simply be rerun.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import shlex
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import agents_section, cartographer, filetree
from .core import (ADOPTED, EXTERNAL, FAIL, GENERATED, KIT_DIR, NOT_RUN, PASS, REGION, SEEDED, WARN,
                   Ledger, StepResult, now_iso, read_json, run, sha256_file, sha256_text, which, write_json)

# Paths every tool must skip: runtime state of the kit and of the tools themselves.
STATE_EXCLUDES = [KIT_DIR, ".plot", ".python_doctor", ".somnus-debug"]

METADATA_BUILTIN_EXCLUDES = ["archive/**", ".git/**", "node_modules/**", "**/__pycache__/**", "**/node_modules/**",
                             "**/.venv/**", "**/venv/**", "**/site-packages/**", "**/.tox/**", "**/.mypy_cache/**",
                             "build/**", "dist/**", "**/*.egg-info/**"]
# Files whose format or publication would be damaged by an embedded header (PR template body,
# wiki pages published verbatim) are excluded from stamping as well.
METADATA_KIT_EXCLUDES = [f"{p}/**" for p in STATE_EXCLUDES] + [".github/**", "WIKI.md", "WIKI.*.md", "runs/**"]


@dataclass
class Ctx:
    root: Path
    kit: Path
    ledger: Ledger
    tools: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    manifest: dict[str, Any] = field(default_factory=dict)

    def rel(self, p: Path) -> str:
        return p.relative_to(self.root).as_posix()


# ----------------------------------------------------------------------------------------- preflight

def _first_line_version(out: str) -> str | None:
    m = re.search(r"(\d+\.\d+\.\d+)", out or "")
    return m.group(1) if m else None


def _interpreter_of(script: str | None) -> str | None:
    """The Python interpreter a console script runs under (its shebang)."""
    if not script:
        return None
    try:
        first = Path(script).read_text(encoding="utf-8", errors="replace").splitlines()[0]
    except (OSError, IndexError):
        return None
    if first.startswith("#!") and "python" in first:
        return first[2:].strip().split()[0]
    return None


def discover_tools(ctx: Ctx, res: StepResult) -> None:
    t: dict[str, Any] = {}
    dist = ctx.options.get("plot_dist") or os.environ.get("PLOT_DIST")
    if which("plot"):
        t["plot"] = {"argv": ["plot"]}
    elif dist and Path(dist, "adapters/cli/plot.mjs").is_file() and which("node"):
        t["plot"] = {"argv": ["node", str(Path(dist, "adapters/cli/plot.mjs").resolve())]}
    for name, exe, ver_args in (("metadata", "somnus", ["--version"]), ("debug", "somnus-debug", ["--help"]),
                                ("sdgc", "sdgc", ["--version"])):
        path = which(exe)
        if path:
            t[name] = {"argv": [path], "python": _interpreter_of(path)}
            rec, out, err = run([path, *ver_args], ctx.root, record=res.commands, timeout=60)
            t[name]["version"] = _first_line_version(out + err)
    if "plot" in t:
        rec, out, err = run([*t["plot"]["argv"], "doctor"], ctx.root, record=res.commands, timeout=60)
        try:
            doc = json.loads(out)
            t["plot"]["dist"] = doc["checks"]["dist"]["path"]
            t["plot"]["seed_bytes"] = doc["checks"]["seed"]["bytes"]
            t["plot"]["doctor_ok"] = doc.get("ok")
            pkg = Path(doc["checks"]["dist"]["path"], "package.json")
            t["plot"]["version"] = json.loads(pkg.read_text())["version"] if pkg.is_file() else None
        except (ValueError, KeyError, TypeError):
            t["plot"]["doctor_ok"] = False
    for name in ("plot", "metadata", "debug", "sdgc"):
        if name not in t:
            res.worsen(WARN, f"tool '{name}' not found; its step will be NOT_RUN")
    ctx.tools = t
    res.data["tools"] = t


def _project_name(root: Path) -> str:
    """Declared name, first match: pyproject [project].name, package.json name, directory name."""
    import tomllib
    pp = root / "pyproject.toml"
    if pp.is_file():
        try:
            name = tomllib.loads(pp.read_text(encoding="utf-8")).get("project", {}).get("name")
            if name:
                return name
        except (tomllib.TOMLDecodeError, OSError):
            pass
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            name = json.loads(pkg.read_text(encoding="utf-8")).get("name")
            if name:
                return name
        except ValueError:
            pass
    return root.name


def repo_identity(ctx: Ctx, res: StepResult) -> dict[str, Any]:
    root = ctx.root
    ident: dict[str, Any] = {"root": str(root), "name": _project_name(root), "dir": root.name}
    ident["slug"] = re.sub(r"[^a-zA-Z0-9_-]", "-", ident["name"])[:64] or "repo"
    ident["git_sha"] = None
    ident["github"] = ctx.options.get("github")
    if (root / ".git").exists():
        rec, out, _ = run(["git", "rev-parse", "HEAD"], root, record=res.commands)
        ident["git_sha"] = out.strip() if rec.exit == 0 else None
        if not ident["github"]:
            rec, out, _ = run(["git", "remote", "get-url", "origin"], root, record=res.commands)
            m = re.search(r"github\.com[:/]([^/]+/[^/.]+)", out or "")
            ident["github"] = m.group(1) if m else None
    counts: dict[str, int] = {}
    for rel in filetree.list_files(root):
        ext = Path(rel).suffix.lower()
        lang = {".py": "python", ".mjs": "javascript", ".js": "javascript", ".ts": "typescript",
                ".rs": "rust", ".go": "go"}.get(ext)
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    ident["languages"] = counts
    return ident


def detect_gates(ctx: Ctx) -> list[dict[str, str]]:
    gates = [{"id": f"gate-{i+1}", "cmd": c, "source": "operator --gate"} for i, c in enumerate(ctx.options.get("gates") or [])]
    if gates:
        return gates
    root = ctx.root
    if (root / "pyproject.toml").is_file() and (root / "tests").is_dir():
        gates.append({"id": "pytest", "cmd": "python -m pytest -q", "source": "detected: pyproject.toml + tests/"})
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            if json.loads(pkg.read_text()).get("scripts", {}).get("test"):
                gates.append({"id": "npm-test", "cmd": "npm test", "source": "detected: package.json scripts.test"})
        except ValueError:
            pass
    return gates


def plan_ownership(ctx: Ctx, ident: dict[str, Any]) -> None:
    """Claim every path before anything is written. A double claim aborts init."""
    L, root = ctx.ledger, ctx.root
    # kit
    for rel in (f"{KIT_DIR}/kit.json", f"{KIT_DIR}/.gitignore", f"{KIT_DIR}/filetree.generated.md",
                f"{KIT_DIR}/sdgc/effective-config.json"):
        L.claim(rel, "somnus-kit", GENERATED)
    for rel in (f"{KIT_DIR}/state", f"{KIT_DIR}/metadata"):
        L.claim(rel, "somnus-kit", GENERATED, "tree")
    # plot
    L.claim(".plot", "somnus-plot", ADOPTED if (root / ".plot/manifest.json").exists() else GENERATED, "tree")
    # metadata
    L.claim(".somnus.toml", "somnus-kit(seed)" if not (root / ".somnus.toml").exists() else "operator",
            SEEDED if not (root / ".somnus.toml").exists() else ADOPTED)
    L.claim("$SOMNUS_HOME/somnus.db", "somnus-metadata", EXTERNAL)
    # debug
    cfg = root / ".somnus-debug/python_doctor.yaml"
    L.claim(".somnus-debug/python_doctor.yaml", "somnus-kit(seed)" if not cfg.exists() else "operator",
            SEEDED if not cfg.exists() else ADOPTED)
    L.claim(".somnus-debug/runs", "somnus-debug", GENERATED, "tree")
    L.claim(".somnus-debug/production_doctor.log", "somnus-debug", GENERATED)
    L.claim(".python_doctor", "somnus-debug", GENERATED, "tree")
    # sdgc
    has_cfg = (root / "sdgc.yaml").exists() or (root / "sdgc.yml").exists()
    L.claim("sdgc.yaml" if not (root / "sdgc.yml").exists() else "sdgc.yml",
            "operator" if has_cfg else "sdgc(seed: sdgc init)", ADOPTED if has_cfg else SEEDED)
    L.claim(f"{KIT_DIR}/sdgc/runs", "sdgc", GENERATED, "tree")
    # github (per file, from the skill's assets)
    assets = Path(ctx.options["github_assets"]) / ".github"
    if assets.is_dir():
        for p in sorted(assets.rglob("*")):
            if p.is_file():
                rel = ".github/" + p.relative_to(assets).as_posix()
                L.claim(rel, "operator" if (root / rel).exists() else "somnus-kit(seed: somnus-github-development)",
                        ADOPTED if (root / rel).exists() else SEEDED)
    # wiki
    for name in ("WIKI.md", "WIKI.zh-CN.md"):
        L.claim(name, "operator" if (root / name).exists() else "somnus-kit(seed: wiki-template)",
                ADOPTED if (root / name).exists() else SEEDED)
    # filetree
    L.claim("filetree.md", "operator" if (root / "filetree.md").exists() else "somnus-kit(seed)",
            ADOPTED if (root / "filetree.md").exists() else SEEDED)
    # cartographer adapter output
    L.claim(f"{KIT_DIR}/map", "cartographer-adapter", GENERATED, "tree")
    # AGENTS.md: kit owns only its marked region
    L.claim("AGENTS.md#somnus-kit", "somnus-kit", REGION)


# ----------------------------------------------------------------------------------------- plot

def _plot(ctx: Ctx, args: list[str], res: StepResult, timeout: int = 600):
    env = {}
    return run([*ctx.tools["plot"]["argv"], *args], ctx.root, env=env, record=res.commands, timeout=timeout)


def plot_init(ctx: Ctx, ident: dict) -> StepResult:
    res = StepResult("plot")
    if "plot" not in ctx.tools:
        res.worsen(NOT_RUN, "plot CLI not found (install: npm install -g ./adapters from somnus-plot, or pass --plot-dist)")
        return res
    if (ctx.root / ".plot/manifest.json").exists():
        rec, out, err = _plot(ctx, ["adopt", str(ctx.root), "--name", ident["slug"]], res)
        verb = "adopted existing .plot root"
    else:
        rec, out, err = _plot(ctx, ["init", str(ctx.root), "--name", ident["slug"]], res)
        verb = "created .plot root"
    if rec.exit != 0:
        res.worsen(FAIL, f"plot root setup failed: {err.strip()[:300]}")
        return res
    rec, out, err = _plot(ctx, ["env", "make", "gates", '{"use":[]}'], res)
    if rec.exit != 0:
        res.worsen(FAIL, f"plot env make gates failed: {err.strip()[:300]}")
        return res
    res.summary = f"{verb}; view 'gates' provisioned (empty capability set, shallow host tenant)"
    res.wrote += [".plot/manifest.json", ".plot/.grave/"]
    return res


def plot_check(ctx: Ctx) -> StepResult:
    res = StepResult("gates(plot)")
    if "plot" not in ctx.tools:
        res.worsen(NOT_RUN, "plot CLI not found")
        return res
    gates = ctx.manifest.get("gates", [])
    if not gates:
        res.worsen(WARN, "no gates declared")
        return res
    results = []
    for g in gates:
        script = f"cd {shlex.quote(str(ctx.root))} && {g['cmd']}"
        rec, out, err = _plot(ctx, ["run", "gates", "sh", "-c", script], res, timeout=ctx.options.get("gate_timeout", 900))
        results.append({"id": g["id"], "cmd": g["cmd"], "exit": rec.exit, "seconds": rec.seconds,
                        "tail": (out.strip().splitlines() or [""])[-1][:200]})
        if rec.exit != 0:
            res.worsen(FAIL, f"gate {g['id']} (`{g['cmd']}`) exit={rec.exit}: {(err or out).strip().splitlines()[-1:] }")
    res.data["gates"] = results
    res.summary = ", ".join(f"{r['id']}=exit {r['exit']}" for r in results)
    return res


# ----------------------------------------------------------------------------------------- metadata

def _toml_list(items: list[str]) -> str:
    return "[" + ", ".join(json.dumps(i) for i in items) + "]"


def _somnus(ctx: Ctx, args: list[str], res: StepResult, timeout: int = 600):
    return run([*ctx.tools["metadata"]["argv"], "--no-color", *args], ctx.root, record=res.commands, timeout=timeout)


_INDEX_RE = re.compile(r"scanned (\d+), indexed (\d+), unchanged (\d+), new revisions (\d+), unmanaged (\d+), "
                       r"examples (\d+), invalid (\d+), missing (\d+), conflicts opened (\d+), resolved (\d+)")
_INDEX_KEYS = ["scanned", "indexed", "unchanged", "new_revisions", "unmanaged", "examples", "invalid", "missing",
               "conflicts_opened", "resolved"]


def _parse_index(text: str) -> dict[str, int] | None:
    m = _INDEX_RE.search(text or "")
    return dict(zip(_INDEX_KEYS, map(int, m.groups()))) if m else None


def metadata_seed(ctx: Ctx) -> StepResult:
    """Seed .somnus.toml early so every later step's files are judged by the same exclusions."""
    res = StepResult("metadata-seed")
    toml = ctx.root / ".somnus.toml"
    if ctx.ledger.claims[".somnus.toml"]["class"] == SEEDED:
        toml.write_text(
            "# Seeded by somnus-kit (once). Operator-owned from here; somnus-kit never rewrites it.\n"
            "# A local [index].exclude REPLACES the global list, so the built-ins are repeated here.\n"
            "[index]\n"
            f"exclude = {_toml_list(METADATA_BUILTIN_EXCLUDES + METADATA_KIT_EXCLUDES)}\n", encoding="utf-8")
        res.wrote.append(".somnus.toml")
        res.summary = "seeded .somnus.toml (index exclusions for tool state, .github/, WIKI*)"
    else:
        res.summary = ".somnus.toml existed; adopted unchanged"
        res.findings.append("[INFO] verify the adopted .somnus.toml excludes kit state dirs")
    return res


def metadata_init(ctx: Ctx) -> StepResult:
    """Register the root and record the index baseline. Runs AFTER all seeding so the baseline
    counts the files the kit itself laid down."""
    res = StepResult("metadata")
    if "metadata" not in ctx.tools:
        res.worsen(NOT_RUN, "somnus (somnus-metadata) not found; install: pip install somnus-metadata")
        return res
    rec, out, err = _somnus(ctx, ["init", "--root", str(ctx.root)], res)
    if rec.exit != 0:
        res.worsen(FAIL, f"somnus init failed: {err.strip()[:300]}")
        return res
    rec, out, err = _somnus(ctx, ["index", str(ctx.root)], res)
    counts = _parse_index(out + err)
    if rec.exit not in (0, 1) or counts is None:
        res.worsen(FAIL, f"somnus index produced no summary (exit {rec.exit})")
        return res
    write_json(ctx.kit / "metadata/baseline.json", {"recorded": now_iso(), "index": counts,
                                                     "somnus_home": os.environ.get("SOMNUS_HOME", "~/.somnus")})
    res.wrote.append(f"{KIT_DIR}/metadata/baseline.json")
    res.data["index"] = counts
    res.summary = f"registered root; indexed {counts['indexed']} managed, {counts['unmanaged']} unmanaged (not stamped: stamping is an explicit agent act)"
    return res


def metadata_check(ctx: Ctx) -> StepResult:
    res = StepResult("metadata")
    if "metadata" not in ctx.tools:
        res.worsen(NOT_RUN, "somnus not found")
        return res
    rec, out, err = _somnus(ctx, ["index", str(ctx.root)], res)
    counts = _parse_index(out + err)
    if counts is None:
        res.worsen(FAIL, f"somnus index produced no summary (exit {rec.exit}): {err.strip()[:200]}")
        return res
    res.data["index"] = counts
    base = ctx.kit / "metadata/baseline.json"
    b = read_json(base)["index"] if base.exists() else None
    if counts["invalid"]:
        res.worsen(FAIL, f"{counts['invalid']} managed files have invalid headers")
    if counts["conflicts_opened"]:
        res.worsen(FAIL, f"{counts['conflicts_opened']} registry conflicts opened")
    if b and counts["unmanaged"] > b["unmanaged"]:
        res.worsen(WARN, f"unmanaged files rose {b['unmanaged']} -> {counts['unmanaged']}: new files were not stamped")
    # header validation of managed files only (check on unmanaged files is just W_UNMANAGED noise)
    rec, out, err = _somnus(ctx, ["--tsv", "ls"], res)
    res.data["ls_rows"] = len([l for l in out.splitlines() if l.strip()]) if rec.exit == 0 else None
    res.summary = f"managed {counts['indexed'] + counts['unchanged']}, unmanaged {counts['unmanaged']}, invalid {counts['invalid']}, conflicts {counts['conflicts_opened']}"
    return res


# ----------------------------------------------------------------------------------------- somnus-debug

DOCTOR_KIT_IGNORES = [".plot/*", ".somnus-kit/*", ".somnus-debug/*", "runs/*"]


def _has_python(ctx: Ctx, ident: dict) -> bool:
    return ident.get("languages", {}).get("python", 0) > 0


def _doctor_scan(ctx: Ctx, res: StepResult, name: str) -> dict | None:
    d = ctx.root / ".somnus-debug"
    rec, out, err = run([*ctx.tools["debug"]["argv"], "doctor", "scan", str(ctx.root), "-c", "python_doctor.yaml",
                         "-o", f"runs/{name}.md", "--json-output", f"runs/{name}.json"], d, record=res.commands)
    # doctor exits 1 when it finds serious issues; a JSON report is the success condition.
    js = d / f"runs/{name}.json"
    if rec.exit not in (0, 1) or not js.is_file():
        res.worsen(FAIL, f"doctor scan failed (exit {rec.exit}): {(err or out).strip()[-300:]}")
        return None
    res.wrote += [f".somnus-debug/runs/{name}.md", f".somnus-debug/runs/{name}.json"]
    return read_json(js)


def debug_init(ctx: Ctx, ident: dict) -> StepResult:
    res = StepResult("somnus-debug")
    if not _has_python(ctx, ident):
        res.worsen(NOT_RUN, "no Python files; somnus-debug is Python-only")
        return res
    if "debug" not in ctx.tools:
        res.worsen(NOT_RUN, "somnus-debug not found; install: pip install somnus-debug")
        return res
    d = ctx.root / ".somnus-debug"
    (d / "runs").mkdir(parents=True, exist_ok=True)
    cfg = d / "python_doctor.yaml"
    if ctx.ledger.claims[".somnus-debug/python_doctor.yaml"]["class"] == SEEDED:
        with tempfile.TemporaryDirectory() as tmp:
            rec, out, err = run([*ctx.tools["debug"]["argv"], "doctor", "init-config", "-o", f"{tmp}/c.yaml"], Path(tmp),
                                record=res.commands)
            if rec.exit != 0:
                res.worsen(FAIL, f"doctor init-config failed: {err.strip()[:300]}")
                return res
            text = Path(tmp, "c.yaml").read_text(encoding="utf-8")
        lines = text.splitlines()
        try:
            i = next(n for n, l in enumerate(lines) if l.startswith("ignore_patterns:"))
        except StopIteration:
            res.worsen(FAIL, "doctor default config has no ignore_patterns key; refusing to guess its format")
            return res
        lines[i + 1:i + 1] = [f'  - "{p}"  # somnus-kit: tool state' for p in DOCTOR_KIT_IGNORES]
        cfg.write_text("# Seeded by somnus-kit from `somnus-debug doctor init-config` + kit state ignores. Operator-owned.\n"
                       + "\n".join(lines) + "\n", encoding="utf-8")
        res.wrote.append(".somnus-debug/python_doctor.yaml")
    data = _doctor_scan(ctx, res, "baseline")
    if data:
        s = data["summary"]
        res.data["summary"] = s
        res.summary = (f"baseline: files={s['files_scanned']} issues={s['total_issues']} critical={s['critical_issues']} "
                       f"serious={s['serious_issues']} cycles={s['dependency_cycles']}")
    return res


REGRESSION_KEYS = ["critical_issues", "serious_issues", "syntax_error_files", "dependency_cycles"]


def debug_check(ctx: Ctx, ident: dict) -> StepResult:
    res = StepResult("somnus-debug")
    if not (ctx.root / ".somnus-debug/runs/baseline.json").is_file():
        res.worsen(NOT_RUN, "no baseline (init skipped somnus-debug)")
        return res
    if "debug" not in ctx.tools:
        res.worsen(NOT_RUN, "somnus-debug not found")
        return res
    base = read_json(ctx.root / ".somnus-debug/runs/baseline.json")["summary"]
    data = _doctor_scan(ctx, res, "latest")
    if not data:
        return res
    s = data["summary"]
    delta = {k: s.get(k, 0) - base.get(k, 0) for k in base if isinstance(base.get(k), (int, float))}
    res.data["summary"], res.data["delta_vs_baseline"] = s, delta
    for k in REGRESSION_KEYS:
        if delta.get(k, 0) > 0:
            res.worsen(FAIL, f"{k} rose by {delta[k]} vs baseline")
    if delta.get("total_issues", 0) > 0:
        res.worsen(WARN, f"total_issues rose by {delta['total_issues']} vs baseline")
    res.summary = (f"issues={s['total_issues']} ({delta.get('total_issues', 0):+d}) critical={s['critical_issues']} "
                   f"serious={s['serious_issues']} ({delta.get('serious_issues', 0):+d})")
    return res


# ----------------------------------------------------------------------------------------- sdgc

def _sdgc_effective_config(ctx: Ctx, res: StepResult) -> Path | None:
    """Derive .somnus-kit/sdgc/effective-config.json = operator's sdgc.yaml + kit state exclusions.

    Parsed with SDGC's own loader (so YAML semantics match exactly); sdgc.yaml is never edited.
    """
    src = ctx.root / ("sdgc.yml" if (ctx.root / "sdgc.yml").exists() else "sdgc.yaml")
    py = ctx.tools["sdgc"].get("python")
    if not py:
        res.worsen(FAIL, "cannot locate the interpreter sdgc runs under")
        return None
    code = ("import json,sys;from pathlib import Path;from sdgc.utils import load_yaml_like;"
            "print(json.dumps(load_yaml_like(Path(sys.argv[1]))))")
    rec, out, err = run([py, "-c", code, str(src)], ctx.root, record=res.commands)
    if rec.exit != 0:
        res.worsen(FAIL, f"could not parse {src.name} with sdgc's loader: {err.strip()[-300:]}")
        return None
    data = json.loads(out)
    ex = list(data.get("excluded_paths") or [])
    added = [p for p in STATE_EXCLUDES if p not in ex]
    data["excluded_paths"] = ex + added
    dest = ctx.kit / "sdgc/effective-config.json"
    old = sha256_file(dest)
    _guard_generated(ctx, res, f"{KIT_DIR}/sdgc/effective-config.json")
    write_json(dest, data)
    res.data["kit_added_exclusions"] = added
    if old != sha256_file(dest):
        res.wrote.append(f"{KIT_DIR}/sdgc/effective-config.json")
    return dest


def _sdgc(ctx: Ctx, args: list[str], res: StepResult, timeout: int = 900):
    return run([*ctx.tools["sdgc"]["argv"], *args], ctx.root, record=res.commands, timeout=timeout)


def sdgc_init(ctx: Ctx) -> StepResult:
    res = StepResult("sdgc")
    if "sdgc" not in ctx.tools:
        res.worsen(NOT_RUN, "sdgc not found (install from the SDGC checkout: pip install <sdgc>)")
        return res
    if ctx.ledger.claims.get("sdgc.yaml", {}).get("class") == SEEDED:
        rec, out, err = _sdgc(ctx, ["init", "--path", str(ctx.root / "sdgc.yaml")], res)
        if rec.exit != 0:
            res.worsen(FAIL, f"sdgc init failed: {err.strip()[:300]}")
            return res
        res.wrote.append("sdgc.yaml")
        res.findings.append("[WARN] sdgc.yaml seeded by `sdgc init` with example values: operator must fill title, concepts, boundaries")
        res.worsen(WARN)
    cfg = _sdgc_effective_config(ctx, res)
    if not cfg:
        return res
    out_dir = ctx.kit / "sdgc/runs/baseline"
    rec, out, err = _sdgc(ctx, ["compile", str(ctx.root), "--out", str(out_dir), "--config", str(cfg), "--force"], res)
    if rec.exit != 0:
        res.worsen(FAIL, f"sdgc compile failed (exit {rec.exit}): {(err or out).strip()[-300:]}")
        return res
    rec, vout, verr = _sdgc(ctx, ["validate", str(out_dir)], res)
    if rec.exit != 0:
        res.worsen(WARN, f"baseline sdgc validate exit {rec.exit}: {(vout + verr).strip().splitlines()[-1:]}")
    res.wrote.append(f"{KIT_DIR}/sdgc/runs/baseline/")
    snap = out_dir / "run_snapshot.json"
    if snap.is_file():
        res.data["snapshot_keys"] = sorted(read_json(snap))[:20]
    res.summary = f"compiled baseline run; validate exit {rec.exit}"
    return res


def sdgc_check(ctx: Ctx) -> StepResult:
    res = StepResult("sdgc")
    base = ctx.kit / "sdgc/runs/baseline"
    if "sdgc" not in ctx.tools:
        res.worsen(NOT_RUN, "sdgc not found")
        return res
    if not base.is_dir():
        res.worsen(NOT_RUN, "no sdgc baseline run (init did not compile one)")
        return res
    cfg = _sdgc_effective_config(ctx, res)
    if not cfg:
        return res
    latest = ctx.kit / "sdgc/runs/latest"
    rec, out, err = _sdgc(ctx, ["compile", str(ctx.root), "--out", str(latest), "--config", str(cfg), "--force"], res)
    if rec.exit != 0:
        res.worsen(FAIL, f"sdgc compile failed (exit {rec.exit}): {(err or out).strip()[-300:]}")
        return res
    rec_v, vout, verr = _sdgc(ctx, ["validate", str(latest)], res)
    if rec_v.exit != 0:
        res.worsen(FAIL, f"sdgc validate exit {rec_v.exit}")
    rec_c, cout, cerr = _sdgc(ctx, ["compare", str(base), str(latest), "--json", "--fail-on-regression"], res)
    try:
        cmp = json.loads(cout)
        res.data["compare"] = {k: cmp[k] for k in list(cmp)[:12]}
    except ValueError:
        res.data["compare_raw_tail"] = cout.strip()[-400:]
    if rec_c.exit != 0:
        res.worsen(FAIL, f"sdgc compare --fail-on-regression exit {rec_c.exit}")
    res.summary = f"compile ok; validate exit {rec_v.exit}; compare exit {rec_c.exit}"
    return res


# ----------------------------------------------------------------------------------------- .github

def github_init(ctx: Ctx) -> StepResult:
    res = StepResult("github")
    assets = Path(ctx.options["github_assets"]) / ".github"
    if not assets.is_dir():
        res.worsen(NOT_RUN, f"skill assets not found at {assets}")
        return res
    seeded, adopted = [], []
    for p in sorted(assets.rglob("*")):
        if not p.is_file():
            continue
        rel = ".github/" + p.relative_to(assets).as_posix()
        if ctx.ledger.claims[rel]["class"] == SEEDED:
            dest = ctx.root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)
            seeded.append(rel)
        else:
            adopted.append(rel)
    res.wrote += seeded
    res.data.update(seeded=seeded, adopted=adopted)
    res.summary = f"seeded {len(seeded)} missing .github files from somnus-github-development; adopted {len(adopted)} existing"
    res.findings += [f"[TODO] adapt seeded workflows to this repo's real commands (skill step 4)"] if seeded else []
    return res


def github_check(ctx: Ctx) -> StepResult:
    res = StepResult("github")
    files = [r for r, m in ctx.manifest.get("ownership", {}).items() if r.startswith(".github/")]
    missing = [r for r in files if not (ctx.root / r).is_file()]
    for r in missing:
        res.worsen(WARN, f"{r} is gone (was seeded/adopted at init)")
    try:
        import yaml  # type: ignore
    except ImportError:
        yaml = None
        res.findings.append("[NOT_RUN] YAML parse skipped: PyYAML not importable by the kit interpreter")
    for r in files:
        p = ctx.root / r
        if not p.is_file() or p.suffix not in (".yml", ".yaml"):
            continue
        text = p.read_text(encoding="utf-8")
        if yaml is not None:
            try:
                yaml.safe_load(text)
            except Exception as exc:  # noqa: BLE001 - report any parser failure verbatim
                res.worsen(FAIL, f"{r} does not parse: {exc}")
        for ref in sorted(set(re.findall(r"-r\s+(\S+\.txt)", text))):
            if not (ctx.root / ref).exists():
                res.worsen(WARN, f"{r} installs from {ref}, which does not exist in this repo")
    res.summary = f"{len(files) - len(missing)}/{len(files)} .github files present"
    return res


# ----------------------------------------------------------------------------------------- wiki

def _wiki_body(text: str) -> str:
    i = text.find("-->")
    return text[i + 3:] if i >= 0 else text


def wiki_init(ctx: Ctx, ident: dict) -> StepResult:
    res = StepResult("wiki")
    tpl = Path(ctx.options["wiki_template"])
    if not (tpl / "WIKI.md").is_file():
        res.worsen(NOT_RUN, f"wiki template not found at {tpl}")
        return res
    if ctx.ledger.claims["WIKI.md"]["class"] == ADOPTED:
        res.summary = "WIKI.md existed; adopted unchanged"
        return res
    owner, repo = (ident["github"].split("/", 1) if ident.get("github") else (None, None))
    today = _dt.date.today().isoformat()

    def fill(text: str) -> str:
        text = text.replace("{{PROJECT_NAME}}", ident["name"])
        if owner:
            text = text.replace("{{OWNER}}", owner).replace("{{REPO}}", repo)
        head_end = text.find("-->")
        head, body = text[:head_end], text[head_end:]
        head = head.replace("{{YYYY-MM-DD}}", today)
        if ident.get("git_sha"):
            head = head.replace("{{GIT_SHA}}", ident["git_sha"])
        return head + body

    en = fill((tpl / "WIKI.md").read_text(encoding="utf-8"))
    (ctx.root / "WIKI.md").write_text(en, encoding="utf-8")
    res.wrote.append("WIKI.md")
    if (tpl / "WIKI.zh-CN.md").is_file() and ctx.ledger.claims["WIKI.zh-CN.md"]["class"] == SEEDED:
        zh = fill((tpl / "WIKI.zh-CN.md").read_text(encoding="utf-8"))
        zh = re.sub(r"source_sha256:\s*[0-9a-f]{64}", f"source_sha256: {sha256_text(_wiki_body(en))}", zh, count=1)
        (ctx.root / "WIKI.zh-CN.md").write_text(zh, encoding="utf-8")
        res.wrote.append("WIKI.zh-CN.md")
    left = len(re.findall(r"\{\{[^}]+\}\}", en))
    res.summary = f"seeded WIKI.md + WIKI.zh-CN.md; {left} placeholders left for the agent"
    if not owner:
        res.findings.append("[INFO] no GitHub owner/repo known (no git remote, no --github); {{OWNER}}/{{REPO}} left unfilled")
    return res


def wiki_check(ctx: Ctx) -> StepResult:
    res = StepResult("wiki")
    en_p, zh_p = ctx.root / "WIKI.md", ctx.root / "WIKI.zh-CN.md"
    if not en_p.is_file():
        res.worsen(WARN, "WIKI.md missing")
        return res
    en = en_p.read_text(encoding="utf-8")
    left = len(re.findall(r"\{\{[^}]+\}\}", en))
    if left:
        res.worsen(WARN, f"WIKI.md still has {left} template placeholders")
    if zh_p.is_file():
        m = re.search(r"source_sha256:\s*([0-9a-f]{64})", zh_p.read_text(encoding="utf-8"))
        cur = sha256_text(_wiki_body(en))
        res.data["translation"] = {"recorded": m.group(1) if m else None, "current": cur}
        if not m or m.group(1) != cur:
            res.worsen(WARN, "WIKI.zh-CN.md is stale: source_sha256 does not match the WIKI.md body")
    else:
        res.worsen(WARN, "WIKI.zh-CN.md missing")
    res.summary = f"placeholders={left}; translation {'fresh' if res.data.get('translation', {}).get('recorded') == res.data.get('translation', {}).get('current') else 'stale/missing'}"
    return res


# ----------------------------------------------------------------------------------------- cartographer slot

def _adapter_argv(ctx: Ctx) -> tuple[str, list[str]]:
    a = ctx.manifest.get("adapters", {}).get("cartographer", {})
    if a.get("status") == "configured" and a.get("command"):
        return a.get("name", "cartographer"), list(a["command"])
    # reference adapter, run with the kit's own interpreter
    import sys
    return "doctor-graph", [sys.executable, "-m", "somnus_kit.cartographer"]


def cartographer_run(ctx: Ctx) -> StepResult:
    res = StepResult("map")
    name, argv = _adapter_argv(ctx)
    out_dir = ctx.kit / "map" / name
    prev = None
    if (out_dir / "map.json").is_file():
        prev = read_json(out_dir / "map.json")
    kit_src = str(Path(__file__).resolve().parents[1])
    env = {"PYTHONPATH": kit_src + os.pathsep + os.environ.get("PYTHONPATH", "")}
    rec, out, err = run([*argv, "--root", str(ctx.root), "--out", str(out_dir)], ctx.root, env=env, record=res.commands)
    if rec.exit != 0:
        res.worsen(WARN if name == "doctor-graph" else FAIL, f"adapter {name} exit {rec.exit}: {(err or out).strip()[-200:]}")
        return res
    data = read_json(out_dir / "map.json")
    errs = cartographer.validate_map(data)
    if errs:
        res.worsen(FAIL, f"adapter {name} broke contract {cartographer.CONTRACT}: {errs[:5]}")
        return res
    d = cartographer.diff_maps(prev, data)
    res.data.update(adapter=name, nodes=len(data["nodes"]), edges=len(data["edges"]), diff=d)
    res.wrote.append(f"{KIT_DIR}/map/{name}/map.json")
    status = ctx.manifest.get("adapters", {}).get("cartographer", {}).get("status", "absent")
    res.summary = f"adapter={name} ({'reference stand-in; Cartographer not registered' if status != 'configured' else 'registered'}): {len(data['nodes'])} nodes, {len(data['edges'])} edges"
    return res


# ----------------------------------------------------------------------------------------- filetree

def _guard_generated(ctx: Ctx, res: StepResult, rel: str) -> None:
    """Warn if a kit-generated file changed since the kit last wrote it (someone else wrote it)."""
    ledger_path = ctx.kit / "state/written.json"
    rec = read_json(ledger_path) if ledger_path.is_file() else {}
    cur = sha256_file(ctx.root / rel)
    if rel in rec and cur is not None and rec[rel] != cur:
        res.worsen(WARN, f"{rel} was modified outside its owner (somnus-kit); regenerated")


def _remember_written(ctx: Ctx, rels: list[str]) -> None:
    ledger_path = ctx.kit / "state/written.json"
    rec = read_json(ledger_path) if ledger_path.is_file() else {}
    for rel in rels:
        rec[rel] = sha256_file(ctx.root / rel)
    write_json(ledger_path, rec)


def filetree_run(ctx: Ctx, ident: dict, *, seed: bool) -> StepResult:
    res = StepResult("filetree")
    # the inventory always lists its own map, even on the run that seeds it
    paths = sorted(set(filetree.list_files(ctx.root)) | {"filetree.md"})
    gen_rel = f"{KIT_DIR}/filetree.generated.md"
    _guard_generated(ctx, res, gen_rel)
    doc = filetree.render_document(paths, ident["name"])
    (ctx.kit / "filetree.generated.md").write_text(doc, encoding="utf-8")
    res.wrote.append(gen_rel)
    root_ft = ctx.root / "filetree.md"
    if seed and ctx.ledger.claims["filetree.md"]["class"] == SEEDED:
        root_ft.write_text(doc, encoding="utf-8")
        res.wrote.append("filetree.md")
    d = filetree.drift(ctx.root, root_ft)
    res.data.update(files=len(paths), drift={k: (v if isinstance(v, int) else v[:25]) for k, v in d.items()},
                    drift_counts={"missing_from_filetree": len(d["missing_from_filetree"]),
                                  "listed_but_absent": len(d["listed_but_absent"])})
    if not root_ft.exists():
        res.worsen(WARN, "root filetree.md missing")
    elif d["missing_from_filetree"] or d["listed_but_absent"]:
        res.worsen(WARN, f"filetree.md drift: {len(d['missing_from_filetree'])} unlisted, {len(d['listed_but_absent'])} stale entries")
    res.summary = f"{len(paths)} inventory files; drift unlisted={len(d['missing_from_filetree'])} stale={len(d['listed_but_absent'])}"
    return res


# ----------------------------------------------------------------------------------------- AGENTS.md

def agents_run(ctx: Ctx, *, write: bool) -> StepResult:
    res = StepResult("agents")
    p = ctx.root / "AGENTS.md"
    if write:
        if ctx.options.get("no_agents_edit"):
            (ctx.kit / "state").mkdir(parents=True, exist_ok=True)
            (ctx.kit / "state/AGENTS.section.md").write_text(agents_section.render(ctx.manifest) + "\n", encoding="utf-8")
            res.summary = "AGENTS.md untouched (--no-agents-edit); section written to .somnus-kit/state/AGENTS.section.md"
            res.wrote.append(f"{KIT_DIR}/state/AGENTS.section.md")
            return res
        how = agents_section.upsert(p, agents_section.render(ctx.manifest))
        res.summary = f"AGENTS.md kit region {how}"
        res.wrote.append("AGENTS.md#somnus-kit")
        return res
    if not agents_section.present(p):
        res.worsen(WARN, "AGENTS.md has no somnus-kit region")
    else:
        current = agents_section.render(ctx.manifest)
        if current not in p.read_text(encoding="utf-8"):
            res.worsen(WARN, "AGENTS.md kit region differs from the manifest (edited by hand or manifest changed); run `somnus-kit agents`")
    res.summary = "region present" if res.status == PASS else res.summary
    return res
