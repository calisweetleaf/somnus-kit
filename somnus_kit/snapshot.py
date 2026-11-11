"""Trees and change sets for verify: working tree, index (--staged), or a commit (--range, session HEAD)."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import gitio


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_worktree(repo: Path, rel: str) -> Optional[bytes]:
    p = repo / rel
    if p.is_symlink():
        return os.readlink(p).encode("utf-8")
    if p.is_file():
        try:
            return p.read_bytes()
        except OSError:
            return None
    return None


class Snapshot:
    def __init__(self, repo: Path, kind: str, rev: Optional[str] = None):
        assert kind in ("worktree", "index", "rev")
        self.repo, self.kind, self.rev = repo, kind, rev
        self._files: Optional[set[str]] = None
        self._cache: dict[str, Optional[bytes]] = {}

    @property
    def label(self) -> str:
        return {"worktree": "working tree", "index": "index (staged)"}.get(self.kind) or f"commit {self.rev[:12]}"

    def files(self) -> set[str]:
        if self._files is None:
            if self.kind == "worktree":
                cand = set(gitio.tracked(self.repo)) | set(gitio.untracked(self.repo))
                self._files = {f for f in cand if (self.repo / f).is_file() or (self.repo / f).is_symlink()}
            elif self.kind == "index":
                self._files = set(gitio.tracked(self.repo))
            else:
                self._files = set(gitio.ls_tree(self.repo, self.rev))
        return self._files

    def read(self, rel: str) -> Optional[bytes]:
        if rel not in self._cache:
            if self.kind == "worktree":
                self._cache[rel] = read_worktree(self.repo, rel)
            elif self.kind == "index":
                self._cache[rel] = gitio.show(self.repo, None, rel)
            else:
                self._cache[rel] = gitio.show(self.repo, self.rev, rel)
        return self._cache[rel]

    def text(self, rel: str) -> Optional[str]:
        data = self.read(rel)
        if data is None:
            return None
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError:
            return None

    def materialize(self, scratch: Path) -> Path:
        if self.kind == "worktree":
            return self.repo
        return gitio.materialize(self.repo, None if self.kind == "index" else self.rev, scratch)


def hash_tree(repo: Path) -> dict[str, str]:
    snap = Snapshot(repo, "worktree")
    out = {}
    for rel in sorted(snap.files()):
        data = snap.read(rel)
        if data is not None:
            out[rel] = sha256_bytes(data)
    return out


@dataclass
class ChangeSet:
    mode: str  # session | staged | range | worktree
    old: Snapshot
    new: Snapshot
    changes: list[gitio.Change] = field(default_factory=list)

    @property
    def paths(self) -> list[str]:
        out = []
        for c in self.changes:
            out.append(c.path)
            if c.old_path:
                out.append(c.old_path)
        return sorted(set(out))

    def status_of(self, path: str) -> Optional[str]:
        for c in self.changes:
            if c.path == path:
                return c.status
            if c.old_path == path:
                return "D"
        return None

    def added(self) -> list[str]:
        return sorted(c.path for c in self.changes if c.status in ("A", "R", "C"))

    def deleted(self) -> list[str]:
        return sorted([c.path for c in self.changes if c.status == "D"] +
                      [c.old_path for c in self.changes if c.status == "R" and c.old_path])


def from_session(repo: Path, session: dict) -> ChangeSet:
    head = session.get("head") or gitio.EMPTY_TREE
    new = Snapshot(repo, "worktree")
    before: dict[str, str] = session["files"]
    now = hash_tree(repo)
    changes = []
    for rel in sorted(set(before) | set(now)):
        if rel not in now:
            changes.append(gitio.Change("D", rel))
        elif rel not in before:
            changes.append(gitio.Change("A", rel))
        elif before[rel] != now[rel]:
            changes.append(gitio.Change("M", rel))
    return ChangeSet("session", Snapshot(repo, "rev", head) if head != gitio.EMPTY_TREE else Snapshot(repo, "rev", gitio.EMPTY_TREE), new, changes)


def from_staged(repo: Path) -> ChangeSet:
    base = gitio.head(repo) or gitio.EMPTY_TREE
    return ChangeSet("staged", Snapshot(repo, "rev", base), Snapshot(repo, "index"),
                     gitio.diff_names(repo, "--cached", base))


def from_range(repo: Path, spec: str) -> ChangeSet:
    if ".." not in spec:
        raise gitio.GitError(f"--range wants A..B, got {spec!r}")
    a, b = spec.split("..", 1)
    a = gitio.resolve(repo, a.strip(".") or "HEAD")
    b = gitio.resolve(repo, b.strip(".") or "HEAD")
    return ChangeSet("range", Snapshot(repo, "rev", a), Snapshot(repo, "rev", b), gitio.diff_names(repo, a, b))


def from_worktree(repo: Path) -> ChangeSet:
    base = gitio.head(repo) or gitio.EMPTY_TREE
    changes = gitio.diff_names(repo, base)
    seen = {c.path for c in changes}
    changes += [gitio.Change("A", u) for u in gitio.untracked(repo) if u not in seen]
    return ChangeSet("worktree", Snapshot(repo, "rev", base), Snapshot(repo, "worktree"), changes)
