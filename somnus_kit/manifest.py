"""`.somnus-kit/kit.json`: the committed manifest and ownership table.

It holds no absolute paths and no timestamps that change per run, so it is stable in git.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from . import KIT_VERSION

SCHEMA = "somnus-kit.manifest/2"
KIT_JSON = ".somnus-kit/kit.json"

# ownership classes
GENERATED = "generated"  # rewritten by its owner; a hand edit is a violation
REGION = "region"        # owner controls a marked block inside another file
CONFIG = "config"        # operator configuration owned by a tool; protected from agents
SEEDED = "seeded"        # written once if absent, then belongs to the operator
ADOPTED = "adopted"      # existed before the kit; validated, never written

DEFAULT_PROTECTED = [KIT_JSON, ".somnus.toml", ".github/workflows/**"]

DEFAULTS: dict[str, Any] = {
    "schema": SCHEMA,
    "kit_version": KIT_VERSION,
    "repo": {"name": None, "remote": None},
    "roles": {"overrides": {}},
    "scope": {"always_allowed": [], "impact_hops": 1},
    "protected": list(DEFAULT_PROTECTED),
    "generated": [],
    "metadata": {"require_on_new": True, "severity": "FAIL"},
    "debug": {"enabled": True, "config": None},
    "docgate": {"private_terms": [], "max_placeholder": 0, "pass_status": "WARN"},
    "adapters": {"claude": False, "copilot": False},
    "cartographer": {"profile": "auto", "path": None},
    "ownership": {},
}


class ManifestError(ValueError):
    pass


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load(repo: Path, *, required: bool = False) -> dict:
    path = Path(repo) / KIT_JSON
    if not path.is_file():
        if required:
            raise ManifestError(f"{KIT_JSON} not found; run `somnus-kit init {repo}` first")
        return copy.deepcopy(DEFAULTS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"{KIT_JSON}: {exc}") from exc
    return from_data(data)


def from_data(data: Any) -> dict:
    if not isinstance(data, dict):
        raise ManifestError(f"{KIT_JSON}: root must be an object")
    schema = data.get("schema")
    if schema not in (SCHEMA, None):
        raise ManifestError(f"{KIT_JSON}: unsupported schema {schema!r} (expected {SCHEMA})")
    merged = _merge(DEFAULTS, data)
    for role in merged["roles"]["overrides"].values():
        if role not in ("ø", "○", "♤", "none"):
            raise ManifestError(f"{KIT_JSON}: roles.overrides values must be one of ø ○ ♤ none, got {role!r}")
    if merged["docgate"].get("pass_status") not in ("PASS", "WARN"):
        raise ManifestError(f"{KIT_JSON}: docgate.pass_status must be PASS or WARN")
    if merged["metadata"]["severity"] not in ("FAIL", "WARN"):
        raise ManifestError(f"{KIT_JSON}: metadata.severity must be FAIL or WARN")
    for key in ("protected", "generated"):
        if not isinstance(merged[key], list):
            raise ManifestError(f"{KIT_JSON}: {key} must be a list")
    for g in merged["generated"]:
        if not isinstance(g, dict) or "glob" not in g:
            raise ManifestError(f"{KIT_JSON}: generated entries need a glob")
    return merged


def dumps(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def save(repo: Path, data: dict) -> Path:
    path = Path(repo) / KIT_JSON
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(data), encoding="utf-8")
    return path


def owned(manifest: dict, cls: str) -> dict[str, dict]:
    return {p: m for p, m in manifest.get("ownership", {}).items() if m.get("class") == cls}
