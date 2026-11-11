# somnus-kit

Repo guard for agent work. An agent opens a session before it edits, the kit records what the task is allowed to touch, and `verify` fails the change when the agent wanders: out of scope, into operator docs, into generated files, past protected paths, or into a debug regression. A pre-commit hook and the GitHub guard run the same check.

It works with any agent that reads `AGENTS.md` (Codex, Claude Code, OpenCode, Droid, Hark) and across many repos. It runs on local tools only. Nothing calls a paid service.

Version 0.2.0. Python 3.11+, stdlib only. somnus-metadata and somnus-debug are imported as libraries when present.

## What lands in a repo

Only these files are committed, and every one is recorded in the ownership table in `kit.json`:

| Path | Owner | Class |
| --- | --- | --- |
| `.somnus-kit/kit.json` | somnus-kit | config (operator edits, agents never) |
| `.somnus.toml` | somnus-metadata | config; seeded by `init` only if absent |
| `AGENTS.md` marked section | somnus-kit | region; the rest of AGENTS.md is operator text |
| `CLAUDE.md`, `.github/copilot-instructions.md` | somnus-kit | generated pointers, only when `kit.json` enables them |
| `.github/workflows/*` callers | somnus-github-development | generated, only with `init --callers DIR` |

Everything else lives under `$SOMNUS_HOME` (default `~/.somnus`), keyed by repo-id:

```text
$SOMNUS_HOME/
├── models/<repo-id>/                Cartographer model
├── sessions/<repo-id>/<sid>.json    brief sessions, plus an ACTIVE pointer
├── debug/<repo-id>/                 somnus-debug baselines
├── logs/<repo-id>/                  init and verify reports
├── hooks/<repo-id>/                 the kit's git hooks dir (core.hooksPath)
└── tmp/<repo-id>/                   materialized trees, deleted after each run
```

repo-id is `<owner>__<repo>` from the git remote (origin first). With no remote it is the sanitized directory name, a dash, and the first 8 hex chars of the sha256 of the absolute path.

## Commands

```text
somnus-kit init <repo> [--adapters claude,copilot] [--callers DIR] [--protect GLOB]... [--no-metadata] [--cartographer DIR]
somnus-kit brief <repo> --task "<text>" [--scope GLOB]... [--budget-chars N] [--json]
somnus-kit verify <repo> [--session ID | --staged | --range A..B] [--operator] [--json]
somnus-kit end <repo> [--session ID]
somnus-kit agents <repo>
somnus-kit hooks install|uninstall|status <repo>
somnus-kit fleet status|check [--fleet fleet.json] [--json]
somnus-kit where <repo>
```

Exit codes: 0 for PASS or WARN, 1 for FAIL, 2 for a usage or config error.

The loop:

```sh
somnus-kit init ~/src/sdgc --adapters claude
git -C ~/src/sdgc commit -m "somnus-kit init"     # operator commit, before hooks
somnus-kit hooks install ~/src/sdgc
somnus-kit brief ~/src/sdgc --task "fix scanner following symlinks before excludes"
# ...agent edits...
somnus-kit verify ~/src/sdgc
```

## brief

`brief` opens a session. It records HEAD, the sha256 of every tracked and untracked file, the declared scope, its impact set, and a somnus-debug baseline. Then it prints the scope, the impact set, the rules for this repo, and the context pack.

Scope comes from `--scope` globs. Without them it comes from the files the context pack names. The allowed set is the scope plus its impact: importers up to `scope.impact_hops` (default 1), tests, and docs that reference it. Doc matches on a bare file stem are weak, so they are left out. New tests named after a scoped module (`test_<stem>*.py`) are allowed too.

Cartographer is used through its CLI and feature-detected on every run:

- `model.mjs --profile auto` when `profiles/index.json` exists, otherwise `generic`
- `query.mjs impact --json`
- `context.mjs --json`

When one is missing, the kit falls back to its stand-in: task terms ranked against file paths and content, with impact taken from somnus-debug's resolved import edges. Every session records which source produced its scope.

## verify

| Check | Rule | Fails when |
| --- | --- | --- |
| (a) scope | changed files must be in scope or in the impact set; ○ living docs are exempt | out of scope with a session. Without a session (`--range`, plain `--staged`) it only warns |
| (b) roles | ø operator docs are read-only to agents; ○ living docs are editable; ♤ public docs must pass the doc gate | an ø doc changes, other than a kit-region re-render; a ♤ doc fails the gate |
| (c) generated | owned and generated files are never hand-edited | an adapter or the AGENTS.md kit region differs from what the kit renders; a `kit.json` `generated` glob is touched. A `@generated` or `do not edit` header only warns |
| (d) metadata | new files carry a valid somnus header when `.somnus.toml` exists | a new file that can carry a header has none, or an invalid one. `metadata.severity` can set this to WARN |
| (e) debug | no somnus-debug regression against the baseline | a new critical or serious issue appears in a changed file, or syntax errors or dependency cycles go up |
| (f) drift | `filetree.md` and WIKI translations stay current | never fails. Files added but not listed, or deleted but still listed, warn. A translation whose `source_sha256` no longer matches its WIKI.md body warns |
| (g) protected | `kit.json` `protected` globs (default: `kit.json`, `.somnus.toml`, `.github/workflows/**`) | any protected path changes |

Mode selection: `--session ID`, `--staged` (the index against HEAD), `--range A..B`, or no flag. With no flag the kit uses the active session, or the working tree against HEAD if there is none.

`--operator`, or `SOMNUS_KIT_OPERATOR=1`, turns ø and protected violations into WARN for the operator's own commits. An agent can set that variable too. Treat the hook as a guardrail, not a security boundary. The CI guard runs `--range` without that variable.

A check reports NOT_RUN when a library it needs is missing. NOT_RUN counts as WARN, never as a pass.

JSON output: `{"status": "PASS|WARN|FAIL", "mode", "session", "changed": [...], "checks": [{"id", "name", "status", "detail", "files", "items"}]}`.

## Roles

somnus-metadata core 2.0.0 has no field for the doc boundary. So the role of a file is the first decisive answer in this order:

1. `kit.json` `roles.overrides`: a path or glob mapped to `ø`, `○`, `♤`, or `none`
2. a somnus header tag: `boundary:operator`, `boundary:living`, or `boundary:public`
3. the doc-boundary convention by file stem:
   - ø: AGENTS, PLAN, TASK, SCOPE
   - ○: ARCHITECTURE_MAP, CONTEXT, DECISIONS, MEMORY, NOTEPAD, STATE
   - ♤: README, SPEC, ARCHITECTURE, DEVELOPMENT_SETUP, filetree
4. the header's `access.visibility`: `operator-only` means ø, `public` means ♤
5. the header's `classification.type`: notepad, state, memory, or decision-record means ○

Inferred metadata never overrides the convention. `somnus stamp` labels somnus-metadata's own PLAN.md as type plan, role working.

## Hooks

`hooks install` points `core.hooksPath` at `$SOMNUS_HOME/hooks/<repo-id>/`. The previous hooks dir, from `core.hooksPath` or `.git/hooks`, is recorded in `chain.json`. Every executable hook in it gets a forwarding shim, so `commit-msg`, `pre-push` and the rest keep running. The kit's pre-commit runs `verify --staged` first, then the previous pre-commit. `hooks uninstall` restores the previous setting.

## Fleet

`fleet.json` lists the local clones: `{"repos": [{"path", "remote", "profile"}]}`. The default location is `$SOMNUS_HOME/fleet.json`.

- `fleet status` reports, per repo: init state, hooks, active session, dirty count, and whether the remote matches.
- `fleet check` runs `verify` on every repo and exits 1 if any repo fails.

## Libraries used

The kit imports these as libraries. It does not shell out to their CLIs.

- somnus-metadata (`somnus`):
  - `config.load`, `config.BUILTIN`, `config.LOCAL_NAME`
  - `db.glob_regex`, `db.matches_any`
  - `carriers.for_text`, `carriers.SIDECAR_SUFFIX`
  - `document.from_text`
  - `model.validate.validate`
- somnus-debug (`somnus_debug.doctor.core`): `ConfigManager`, `ProjectScanner.scan`. The kit never calls `StateStore` or the CLI's `setup_logging`, so nothing is written into the repo. Round 1 leaked `production_doctor.log` that way.

## Tests

```sh
python -m unittest discover -s tests -v                       # 50 cases
PY=/path/to/python-with-libs bash trial_v2.sh                 # end-to-end on SDGC and somnus-debug copies
```

Evidence from the last run is in `evidence/`.

## Limits

- The doc gate is mechanical: placeholders, broken relative links, `docgate.private_terms`, and a heading. The evidence and voice review still belongs to somnus-doc-system. A passing ♤ edit reports WARN for that reason; set `docgate.pass_status` to change that.
- Scope from a context pack is only as good as the pack. For a precise task, pass `--scope`.
- Check (e) only sees what the doctor config includes. Python changes outside that config produce a WARN, not silence.
- `--operator` is an honor switch locally. Enforcement that cannot be bypassed is the CI guard on the PR range.
