"""filetree.md generation and drift detection (stdlib only).

Generated tree uses the same box-drawing format as Daeron's filetree.md files, so a
curated root filetree.md can be parsed back into a path set and compared.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
from pathlib import Path

ROLE_GLYPHS = {
    # ø operator, ○ living memory, ♤ reader-facing (somnus-github-development convention)
    "AGENTS.md": "ø", "PLAN.md": "ø", "TASK.md": "ø", "SCOPE.md": "ø",
    "ARCHITECTURE_MAP.md": "○", "CONTEXT.md": "○", "DECISIONS.md": "○", "MEMORY.md": "○",
    "NOTEPAD.md": "○", "STATE.md": "○",
    "README.md": "♤", "SPEC.md": "♤", "ARCHITECTURE.md": "♤", "DEVELOPMENT_SETUP.md": "♤",
    "filetree.md": "♤", "WIKI.md": "♤",
}

DEFAULT_EXCLUDES = [
    ".git", ".git/**", "**/__pycache__", "**/__pycache__/**", "*.pyc", "**/*.pyc", ".venv", ".venv/**",
    "venv/**", "node_modules", "**/node_modules/**", "*.egg-info", "**/*.egg-info/**", "build/**",
    "dist/**", ".pytest_cache/**", ".mypy_cache/**", ".ruff_cache/**", "runs/**",
    # runtime state of the kit and its tools: volatile, never part of the visible inventory
    ".somnus-kit/**", ".plot/**", ".python_doctor/**", ".somnus-debug/runs/**", "production_doctor.log",
    "**/production_doctor.log",
]


def _excluded(rel: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if fnmatch.fnmatch(rel, pat):
            return True
        if pat.endswith("/**") and (rel == pat[:-3] or rel.startswith(pat[:-3] + "/")):
            return True
    return False


def list_files(root: Path, excludes: list[str] | None = None) -> list[str]:
    patterns = DEFAULT_EXCLUDES + list(excludes or [])
    files: list[str] = []
    if (root / ".git").exists():
        proc = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                              cwd=root, capture_output=True, text=True)
        if proc.returncode == 0:
            files = [ln for ln in proc.stdout.splitlines() if ln]
    if not files:
        for p in sorted(root.rglob("*")):
            if p.is_file() or p.is_symlink():
                files.append(p.relative_to(root).as_posix())
    return sorted({f for f in files if not _excluded(f, patterns)})


def render_tree(paths: list[str], *, glyphs: bool = True) -> str:
    tree: dict = {}
    for rel in paths:
        node = tree
        parts = rel.split("/")
        for part in parts[:-1]:
            node = node.setdefault(part + "/", {})
        node.setdefault(parts[-1], None)

    lines: list[str] = []

    def key(name: str):
        return (0 if name.endswith("/") else 1, name.lower())

    def walk(node: dict, prefix: str, depth: int) -> None:
        names = sorted(node, key=key)
        for i, name in enumerate(names):
            last = i == len(names) - 1
            branch = "└── " if last else "├── "
            label = name
            if glyphs and depth == 0 and name in ROLE_GLYPHS:
                label = f"{name:<26}{ROLE_GLYPHS[name]}"
            lines.append(prefix + branch + label)
            child = node[name]
            if isinstance(child, dict):
                walk(child, prefix + ("    " if last else "│   "), depth + 1)

    walk(tree, "", 0)
    return "\n".join(lines)


def render_document(paths: list[str], project: str) -> str:
    return (
        f"# {project} file tree\n\n"
        "Visible inventory of material that belongs in this repository. Volatile runtime state "
        "(`.somnus-kit/`, `.plot/`, `.python_doctor/`, doctor runs) is excluded.\n\n"
        "```text\n" + render_tree(paths) + "\n```\n\n"
        "- **ø** Operator documents. Return through `AGENTS.md` as the semantic router for each work loop.\n"
        "- **○** Living repository memory.\n"
        "- **♤** Reader-facing documentation.\n"
    )


_ENTRY = re.compile(r"^(?P<indent>(?:│   |    |\|   )*)(?:├── |└── |\|-- |`-- )(?P<name>.+?)\s*$")


def parse_tree(markdown: str) -> set[str]:
    """Extract paths from the FIRST fenced tree block of a filetree.md (the actual-tree block).

    Later blocks in Daeron's files hold baselines/proposals and are not the actual tree.
    """
    m = re.search(r"```(?:text)?\n(.*?)```", markdown, re.S)
    if not m:
        return set()
    stack: list[str] = []
    out: set[str] = set()
    for line in m.group(1).splitlines():
        em = _ENTRY.match(line)
        if not em:
            continue
        depth = len(em.group("indent")) // 4
        name = em.group("name")
        name = re.split(r"\s{2,}|\s+[øo○♤#]", name)[0].strip()
        stack = stack[:depth]
        if name.endswith("/"):
            stack.append(name.rstrip("/"))
        else:
            out.add("/".join(stack + [name]))
    return out


def drift(root: Path, curated: Path, excludes: list[str] | None = None) -> dict:
    actual = set(list_files(root, excludes))
    listed = parse_tree(curated.read_text(encoding="utf-8")) if curated.exists() else set()
    return {
        "listed": len(listed),
        "actual": len(actual),
        "missing_from_filetree": sorted(actual - listed),
        "listed_but_absent": sorted(listed - actual),
    }
