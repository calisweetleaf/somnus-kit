<!-- somnus-kit:begin v2 (generated from .somnus-kit/kit.json by `somnus-kit agents`; do not edit inside) -->
## Repo guard (somnus-kit)

Everything above this section is operator intent. This section is generated from `.somnus-kit/kit.json`.

1. Open a session before you edit: `somnus-kit brief . --task "<the task>"`, plus `--scope <glob>` when the
   operator gave one. Read the context pack it prints and open those files. Do not read the whole tree.
2. Edit inside the declared scope and its impact set (importers, tests, docs it names). Anything else is
   out of scope and fails verify.
3. Before you stop or commit: `somnus-kit verify .`. FAIL means stop and fix or report. WARN means say why.
4. The pre-commit hook runs `somnus-kit verify --staged`. Never bypass it (`--no-verify`, unsetting hooksPath).
5. New files that can carry a header get one before commit: `somnus stamp PATH --write` (paths excluded in `.somnus.toml` are exempt).
6. Session state, models, and baselines live under `$SOMNUS_HOME`. Never write kit state into this repo.
7. Never push, publish, or open a release.

Document roles:
- ø operator (AGENTS, PLAN, TASK, SCOPE): read-only to agents.
- ○ living (ARCHITECTURE_MAP, CONTEXT, DECISIONS, MEMORY, NOTEPAD, STATE): keep current from verified work.
- ♤ public (README, SPEC, ARCHITECTURE, DEVELOPMENT_SETUP, filetree): edits must pass the doc gate.
- A somnus header tag `boundary:operator|living|public` sets the role of any other file.

Role overrides from kit.json:
- none

Protected paths (operator only):
- `.github/workflows/**`
- `.somnus-kit/kit.json`
- `.somnus.toml`

Generated or owned files (never hand-edit; regenerate through the owner):
- none
<!-- somnus-kit:end -->
