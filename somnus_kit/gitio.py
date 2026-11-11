"""Thin git wrapper (subprocess, no dependencies). Never raises on a nonzero exit unless asked."""

from __future__ import annotations

import subprocess
import tarfile
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


class GitError(RuntimeError):
    pass


def git(repo: Path, *args: str, check: bool = True, binary: bool = False, input: Optional[bytes] = None):
    proc = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, input=input)
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {proc.stderr.decode('utf-8', 'replace').strip()}")
    if binary:
        return proc.returncode, proc.stdout
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def toplevel(path: Path) -> Optional[Path]:
    code, out = git(path, "rev-parse", "--show-toplevel", check=False)
    return Path(out.strip()).resolve() if code == 0 and out.strip() else None


def git_dir(repo: Path) -> Path:
    _, out = git(repo, "rev-parse", "--absolute-git-dir")
    return Path(out.strip())


def head(repo: Path) -> Optional[str]:
    code, out = git(repo, "rev-parse", "--verify", "-q", "HEAD", check=False)
    return out.strip() if code == 0 and out.strip() else None


def resolve(repo: Path, rev: str) -> str:
    code, out = git(repo, "rev-parse", "--verify", "-q", rev + "^{commit}", check=False)
    if code != 0 or not out.strip():
        raise GitError(f"unknown revision {rev!r}")
    return out.strip()


def branch(repo: Path) -> Optional[str]:
    code, out = git(repo, "symbolic-ref", "--short", "-q", "HEAD", check=False)
    return out.strip() if code == 0 else None


def remotes(repo: Path) -> dict[str, str]:
    code, out = git(repo, "remote", "-v", check=False)
    found: dict[str, str] = {}
    if code != 0:
        return found
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] not in found:
            found[parts[0]] = parts[1]
    return found


def _z(out: str) -> list[str]:
    return [p for p in out.split("\0") if p]


def tracked(repo: Path) -> list[str]:
    _, out = git(repo, "ls-files", "-z", "--cached")
    return sorted(set(_z(out)))


def untracked(repo: Path) -> list[str]:
    _, out = git(repo, "ls-files", "-z", "--others", "--exclude-standard")
    return sorted(set(_z(out)))


def ls_tree(repo: Path, rev: str) -> list[str]:
    if rev == EMPTY_TREE:
        return []
    _, out = git(repo, "ls-tree", "-r", "-z", "--name-only", rev)
    return sorted(_z(out))


def show(repo: Path, rev: Optional[str], path: str) -> Optional[bytes]:
    """Content of `path` at `rev` (rev None or ':' = index). None when absent."""
    if rev == EMPTY_TREE:
        return None
    spec = f":{path}" if rev in (None, ":") else f"{rev}:{path}"
    code, out = git(repo, "show", spec, check=False, binary=True)
    return out if code == 0 else None


@dataclass
class Change:
    status: str  # A, M, D, R, T, C
    path: str
    old_path: Optional[str] = None


def diff_names(repo: Path, *args: str) -> list[Change]:
    """`git diff --name-status -z <args>` parsed. Renames come back as R with old_path."""
    _, out = git(repo, "diff", "--name-status", "-z", "-M", *args)
    parts = _z(out)
    changes: list[Change] = []
    i = 0
    while i < len(parts):
        status = parts[i]
        code = status[0]
        if code in ("R", "C"):
            changes.append(Change(code, parts[i + 2], parts[i + 1]))
            i += 3
        else:
            changes.append(Change(code, parts[i + 1]))
            i += 2
    return changes


def config_get(repo: Path, key: str) -> Optional[str]:
    code, out = git(repo, "config", "--get", key, check=False)
    return out.strip() if code == 0 and out.strip() else None


def config_set(repo: Path, key: str, value: str) -> None:
    git(repo, "config", key, value)


def config_unset(repo: Path, key: str) -> None:
    git(repo, "config", "--unset", key, check=False)


def materialize(repo: Path, rev: Optional[str], dest: Path) -> Path:
    """Write the tree of `rev` (None = the index) into `dest`, for tools that need a directory."""
    dest.mkdir(parents=True, exist_ok=True)
    if rev == EMPTY_TREE:
        return dest
    if rev is None:
        git(repo, "checkout-index", "-a", "-f", f"--prefix={str(dest).rstrip('/')}/")
        return dest
    _, data = git(repo, "archive", "--format=tar", rev, binary=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        tar.extractall(dest, filter="data")
    return dest
