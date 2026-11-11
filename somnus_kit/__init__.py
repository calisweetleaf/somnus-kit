"""somnus-kit v2: repo guard for agent work (brief, verify, hooks, fleet).

Committed footprint in a target repo is limited to `.somnus-kit/kit.json`, `.somnus.toml`,
the marked AGENTS.md section, `.github/` callers, and adapter files kit.json enables.
Everything else lives under $SOMNUS_HOME.
"""

KIT_VERSION = "0.2.0"
__version__ = KIT_VERSION
