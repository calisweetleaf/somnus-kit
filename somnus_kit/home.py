"""Out-of-tree state: $SOMNUS_HOME layout and the repo-id rule.

Layout (contract "Placement rule"):
  $SOMNUS_HOME/models/<repo-id>/                 Cartographer model
  $SOMNUS_HOME/sessions/<repo-id>/<sid>.json     brief sessions (+ ACTIVE pointer)
  $SOMNUS_HOME/debug/<repo-id>/                  somnus-debug baselines
  $SOMNUS_HOME/logs/<repo-id>/                   logs, verify reports
  $SOMNUS_HOME/hooks/<repo-id>/                  kit-owned git hooks dir
  $SOMNUS_HOME/tmp/<repo-id>/                    materialized trees (deleted after use)
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from . import gitio

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_REMOTE = re.compile(r"(?:[:/])(?P<owner>[^/:]+)/(?P<repo>[^/]+?)(?:\.git)?/?$")


def somnus_home() -> Path:
    env = os.environ.get("SOMNUS_HOME")
    return Path(env).expanduser().resolve() if env else (Path.home() / ".somnus").resolve()


def sanitize(name: str) -> str:
    out = _SAFE.sub("_", name).strip("._") or "repo"
    return out


def parse_remote(url: str) -> Optional[tuple[str, str]]:
    """owner, repo from an ssh/https/file git remote url, or None."""
    url = url.strip()
    if not url:
        return None
    m = _REMOTE.search(url)
    if not m:
        return None
    return m.group("owner"), m.group("repo")


def repo_id(repo: Path) -> str:
    """`<owner>__<repo>` from the git remote (origin first), else `<dirname>-<sha256(abs path)[:8]>`."""
    repo = Path(repo).resolve()
    remotes = gitio.remotes(repo)
    for name in (["origin"] if "origin" in remotes else []) + sorted(r for r in remotes if r != "origin"):
        parsed = parse_remote(remotes[name])
        if parsed:
            return f"{sanitize(parsed[0])}__{sanitize(parsed[1])}"
    digest = hashlib.sha256(str(repo).encode("utf-8")).hexdigest()[:8]
    return f"{sanitize(repo.name)}-{digest}"


class RepoHome:
    """All out-of-tree paths for one repository."""

    def __init__(self, repo: Path, home: Optional[Path] = None):
        self.repo = Path(repo).resolve()
        self.home = Path(home).resolve() if home else somnus_home()
        self.id = repo_id(self.repo)

    def _d(self, kind: str) -> Path:
        return self.home / kind / self.id

    @property
    def models(self) -> Path:
        return self._d("models")

    @property
    def sessions(self) -> Path:
        return self._d("sessions")

    @property
    def debug(self) -> Path:
        return self._d("debug")

    @property
    def logs(self) -> Path:
        return self._d("logs")

    @property
    def hooks(self) -> Path:
        return self._d("hooks")

    @property
    def tmp(self) -> Path:
        return self._d("tmp")

    def ensure(self, *kinds: str) -> None:
        for k in kinds:
            getattr(self, k).mkdir(parents=True, exist_ok=True)

    @contextmanager
    def scratch(self) -> Iterator[Path]:
        d = self.tmp / uuid.uuid4().hex[:12]
        d.mkdir(parents=True, exist_ok=True)
        try:
            yield d
        finally:
            shutil.rmtree(d, ignore_errors=True)
