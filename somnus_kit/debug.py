"""somnus-debug as a library: ConfigManager + ProjectScanner, no CLI, no files written.

`ProjectScanner.scan()` is pure analysis; only `StateStore` and `setup_logging()` (CLI path) write
to disk. The CLI's `setup_logging()` opens `production_doctor.log` in the current directory
(doctor/core.py:2149), which is why the round-1 kit leaked that file. Calling the scanner directly
never installs that handler, so nothing lands in the repo.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Optional

from . import libs

SEVERE = ("critical", "serious")
GLOBAL_FAIL = ("syntax_error_files", "dependency_cycles")


def available() -> bool:
    return libs.debug() is not None


def find_config(root: Path, manifest: dict) -> Optional[Path]:
    rel = (manifest.get("debug") or {}).get("config")
    for cand in ([rel] if rel else []) + [".somnus-debug/python_doctor.yaml", "python_doctor.yaml"]:
        p = Path(root) / cand
        if p.is_file():
            return p
    return None


def identity(issue) -> str:
    """Issue identity without the line number, so moved code is not a new issue."""
    sev = getattr(issue.severity, "value", str(issue.severity))
    return "|".join([issue.category, sev, issue.symbol, issue.message])


def scan(root: Path, config: Optional[Path] = None) -> dict:
    """Scan a directory. Returns summary, per-file issue identities, and the local import graph."""
    core = libs.debug()
    if core is None:
        raise RuntimeError(f"somnus_debug not importable: {libs.error('somnus_debug.doctor.core')}")
    cm = core.ConfigManager(config)
    result = core.ProjectScanner(Path(root), cm).scan({})
    issues: dict[str, list[str]] = {}
    for f in result.files:
        if f.issues:
            issues[f.relative_path] = sorted(identity(i) for i in f.issues)
    imports: dict[str, list[str]] = {}
    for f in result.files:
        targets = sorted({e.target_file for e in f.import_edges if e.target_file})
        if targets:
            imports[f.relative_path] = targets
    s = result.summary
    return {
        "tool": "somnus_debug.doctor.core.ProjectScanner",
        "version": libs.versions().get("somnus_debug"),
        "config": str(config) if config else None,
        "config_hash": cm.config_hash,
        "summary": {k: getattr(s, k) for k in (
            "files_scanned", "total_issues", "critical_issues", "serious_issues", "minor_issues", "info_issues",
            "syntax_error_files", "missing_imports", "dependency_cycles", "production_score")},
        "files": sorted(f.relative_path for f in result.files),
        "issues": issues,
        "imports": imports,
    }


def regressions(base: dict, cur: dict, changed: list[str]) -> dict:
    """Compare two scans. FAIL on new critical/serious issues in changed files or a global rise in
    syntax errors / dependency cycles; WARN on new minor/info issues or more missing imports."""
    fail: list[str] = []
    warn: list[str] = []
    files_fail: set[str] = set()
    files_warn: set[str] = set()
    bs, cs = base["summary"], cur["summary"]
    for key in GLOBAL_FAIL:
        if cs[key] > bs[key]:
            fail.append(f"{key} {bs[key]} -> {cs[key]}")
    if cs["missing_imports"] > bs["missing_imports"]:
        warn.append(f"missing_imports {bs['missing_imports']} -> {cs['missing_imports']}")
    for path in changed:
        if not path.endswith(".py"):
            continue
        before = Counter(base["issues"].get(path, []))
        after = Counter(cur["issues"].get(path, []))
        new = after - before
        for ident, n in sorted(new.items()):
            cat, sev, sym, msg = (ident.split("|", 3) + ["", "", "", ""])[:4]
            line = f"{path}: +{n} {sev} {cat} {sym}: {msg}"
            if sev in SEVERE:
                fail.append(line)
                files_fail.add(path)
            else:
                warn.append(line)
                files_warn.add(path)
    return {"fail": fail, "warn": warn, "files_fail": sorted(files_fail), "files_warn": sorted(files_warn),
            "summary_before": bs, "summary_after": cs}
