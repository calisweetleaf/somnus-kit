# Upstream proposals from somnus-kit v2

The vendor clones were not modified. Each item says what the kit does now and what the upstream fix would be.

## somnus-debug 0.1.0 (c966be1)

1. **The CLI log goes into cwd.** `setup_logging()` installs `logging.FileHandler("production_doctor.log")` (doctor/core.py:2149). Every CLI run from a repo root leaves the file behind. The repo's own test suite does the same: after `pytest` on a fresh copy, `production_doctor.log` sits at the root next to 14 `__pycache__` files (`evidence/trial/somnus-debug/a-junk-status.txt`).
   - Kit now: uses `ConfigManager` and `ProjectScanner.scan()` directly and never calls `setup_logging`.
   - Fix: log under `config.state_dir`, or to stderr unless `--log-file` is given.
2. **No `.gitignore`.** A careless `git add -A` after running the tests stages that junk. The kit's hook blocked it in the trial (`a-commit-junk.txt`).
   - Fix: add `.gitignore` with `__pycache__/`, `*.pyc`, `production_doctor.log`, `.python_doctor/`, `.pytest_cache/`.
3. **The root `python_doctor.yaml` is an example for another project.** It includes `tools/*.py` and `test/*.py`, so it scans 0 of the repo's 20 tracked Python files.
   - Kit now: trusts only `kit.json` `debug.config` or `.somnus-debug/python_doctor.yaml`.
   - Fix: rename the root file to `python_doctor.example.yaml`, or point its include patterns at `src/**/*.py`.
4. **No library entry point.** `somnus_debug.doctor` re-exports only `main`.
   - Fix: export `ConfigManager`, `ProjectScanner`, and `ProjectAnalysisResult` from `somnus_debug.doctor`, with a short note that `scan()` writes nothing.

## somnus-metadata 2.2.1 (9a197f6)

1. **A local `[index].exclude` replaces the built-in list** instead of extending it (config.py `load()`).
   - Kit now: the `.somnus.toml` that `init` seeds repeats `config.BUILTIN["index"]["exclude"]`.
   - Fix: add an `extend_exclude` key, or merge the lists.
2. **No doc-boundary field.** ø/○/♤ cannot be stated in the header. `classification.role` and `classification.type` are mostly inferred by `somnus stamp`, and the inference disagrees with the convention: PLAN.md comes out as type plan, role working.
   - Kit now: reads a `classification.tags` entry `boundary:operator|living|public`.
   - Fix: add `classification.boundary` (enum operator, living, public) to the core schema, or bless the tag convention in SPEC.
3. **Glob helpers live in `somnus.db`.** `glob_regex` and `matches_any` are public in practice but sit in the store module.
   - Fix: move them to `somnus.globs` and re-export them from `somnus.db`.

## Cartographer (lane C, evo/cartographer at 924cd38)

The kit consumes the contract shapes as delivered (`pack`, `files`, `open_next`; impact objects with `depth` and `matched`). It still accepts the pre-contract `text` and `open_next` shape. Two notes:

1. `impact.docs` carries weak bare-stem matches. For `src/sdgc/scanner.py` the list included AGENTS.md at score 0.75.
   - Kit now: drops docs whose only match kind is `stem`, so an operator doc never enters a scope through a stem hit.
   - Upstream: keep marking them, and document the `matched[].kind` values in the contract.
2. `impact.importers` includes depth 2.
   - Kit now: keeps importers with `depth <= scope.impact_hops` (default 1) and records the deeper ones as dropped.
