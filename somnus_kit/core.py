"""Shared machinery: command runner, ownership ledger, step results.

Every external tool call goes through `run()`, which records argv, cwd, exit code,
duration and a bounded tail of output. A step result is built only from those
records; nothing is reported as passed that did not execute.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

KIT_VERSION = "0.1.0"
MANIFEST_SCHEMA = "somnus-kit.manifest/1"
RESULT_SCHEMA = "somnus-kit.check/1"
KIT_DIR = ".somnus-kit"

# Ownership classes (see DESIGN.md §4)
GENERATED = "generated"  # the owner rewrites it on every run; nobody else edits it
SEEDED = "seeded"        # written once if absent; afterwards operator/agent owned, never rewritten
REGION = "region"        # owner controls only a delimited region inside someone else's file
ADOPTED = "adopted"      # existed before the kit; read and validated, never written
EXTERNAL = "external"    # lives outside the repo (registries); listed for completeness

# Status vocabulary. NOT_RUN is used whenever a tool or precondition is missing.
PASS, WARN, FAIL, NOT_RUN = "PASS", "WARN", "FAIL", "NOT_RUN"
_RANK = {PASS: 0, NOT_RUN: 1, WARN: 1, FAIL: 2}


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def tail(text: str, lines: int = 12) -> str:
    parts = (text or "").rstrip().splitlines()
    return "\n".join(parts[-lines:])


@dataclass
class CmdRecord:
    argv: list[str]
    cwd: str
    exit: int | None
    seconds: float
    stdout_tail: str = ""
    stderr_tail: str = ""
    error: str | None = None


def run(argv: list[str], cwd: Path, *, env: dict[str, str] | None = None, timeout: int = 600,
        record: list[CmdRecord] | None = None, tail_lines: int = 12) -> tuple[CmdRecord, str, str]:
    """Run a command, never raising for a nonzero exit. Missing executables become exit=None."""
    start = time.monotonic()
    merged = dict(os.environ)
    if env:
        merged.update(env)
    try:
        proc = subprocess.run(argv, cwd=str(cwd), env=merged, capture_output=True, text=True, timeout=timeout)
        rec = CmdRecord(argv, str(cwd), proc.returncode, round(time.monotonic() - start, 3),
                        tail(proc.stdout, tail_lines), tail(proc.stderr, tail_lines))
        out, err = proc.stdout, proc.stderr
    except FileNotFoundError as exc:
        rec = CmdRecord(argv, str(cwd), None, round(time.monotonic() - start, 3), error=f"not found: {exc}")
        out, err = "", str(exc)
    except subprocess.TimeoutExpired as exc:
        rec = CmdRecord(argv, str(cwd), None, round(time.monotonic() - start, 3), error=f"timeout after {timeout}s")
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        err = exc.stderr if isinstance(exc.stderr, str) else ""
    if record is not None:
        record.append(rec)
    return rec, out, err


def which(name: str) -> str | None:
    return shutil.which(name)


@dataclass
class StepResult:
    step: str
    status: str = PASS
    summary: str = ""
    findings: list[str] = field(default_factory=list)
    commands: list[CmdRecord] = field(default_factory=list)
    wrote: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    def worsen(self, status: str, finding: str | None = None) -> None:
        if _RANK[status] > _RANK[self.status] or (status == NOT_RUN and self.status == PASS):
            self.status = status
        if finding:
            self.findings.append(f"[{status}] {finding}")

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def overall(results: list[StepResult]) -> str:
    worst = PASS
    for r in results:
        if _RANK[r.status] > _RANK[worst]:
            worst = r.status
        elif r.status == NOT_RUN and worst == PASS:
            worst = WARN
    return worst


class OwnershipError(RuntimeError):
    pass


class Ledger:
    """One writer per path. Steps declare claims before writing; a second claim is a hard error."""

    def __init__(self, root: Path):
        self.root = root
        self.claims: dict[str, dict[str, Any]] = {}

    def claim(self, rel: str, owner: str, cls: str, note: str = "") -> None:
        rel = rel.rstrip("/")
        existing = self.claims.get(rel)
        if existing and existing["owner"] != owner:
            raise OwnershipError(f"{rel}: already owned by {existing['owner']} ({existing['class']}); {owner} may not write it")
        # A claimed directory owns everything below it: refuse overlapping claims by another owner.
        for other, meta in self.claims.items():
            if meta["owner"] == owner:
                continue
            if rel.startswith(other + "/") and meta.get("tree"):
                raise OwnershipError(f"{rel}: inside {other}/ owned by {meta['owner']}")
            if other.startswith(rel + "/") and note == "tree":
                raise OwnershipError(f"{rel}/ would contain {other} owned by {meta['owner']}")
        self.claims[rel] = {"owner": owner, "class": cls, "tree": note == "tree"}

    def owner_of(self, rel: str) -> str | None:
        meta = self.claims.get(rel)
        return meta["owner"] if meta else None

    def record_hashes(self) -> None:
        for rel, meta in self.claims.items():
            p = self.root / rel
            meta["sha256"] = sha256_file(p) if p.is_file() else None

    def to_json(self) -> dict[str, Any]:
        return dict(sorted(self.claims.items()))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))
