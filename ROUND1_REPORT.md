# somnus-kit: repo setup kit, design and prototype report

> **Status: superseded (2026-11-11).** This documents the Round 1 prototype at
> `/workspace/work/ghdev/research/setup-kit/` (host: Linux arm64, somnus-kit 0.1.0).
> The current system is **somnus-kit 0.2.0** in this directory: see `README.md`,
> `pyproject.toml`, and the v2 trial evidence in `evidence/trial/` (42/42 expectations
> on fresh SDGC and somnus-debug copies, plus `trial_v2.sh`). Paths in this report
> (`/workspace/work/...`, `targets/sdgc-run/`) no longer exist. Kept as the historical
> record of the design decisions and the upstream findings in §4; the kit now
> implements those workarounds directly (see `README.md` -> "Libraries used").

Date: 2026-11-10 (America/Chicago). Host: sandbox Linux **arm64**, Node 22.23.3, Python 3.12.15, **no C compiler**.
Scope: everything lives under `/workspace/work/ghdev/research/setup-kit/`. Nothing was pushed. No global
registry was touched: Plot and somnus-metadata ran with `PLOT_HOME` / `SOMNUS_HOME` under `.isohome/`.

## 0. Result in one paragraph

`somnus-kit init <repo>` and `somnus-kit check [repo]` work end to end against a disposable git copy of SDGC.
Every step calls the real tools: Plot (`plot init`, `plot env make`, `plot run`), somnus-metadata
(`somnus init`, `somnus index`, `somnus ls`), somnus-debug (`doctor init-config`, `doctor scan`), and SDGC
(`compile`, `validate`, `compare --fail-on-regression`). It also seeds `.github/` from the skill, seeds WIKI.md
and its zh-CN translation from the template, and writes `filetree.md`. It records a one-writer-per-path
ownership table and appends a kit-owned region to AGENTS.md.

Three checks were run. A clean check gave WARN, exit 0, and the WARNs are real repo facts. A check with
induced regressions gave **FAIL, exit 1**, and each regression was caught by the tool that owns it. After
reverting, the check went back to WARN, exit 0. Not built: the Tiniest Cloud preview step, metadata
stamping during init, and the real Cartographer adapter. The adapter slot is defined and exercised by a
reference stand-in.

Reproduce: `bash /workspace/work/ghdev/research/setup-kit/run_trial.sh` (≈15 s). Full log: `evidence/run_trial.log`.

## 1. The tools: what each is, its surface, install, and test results

Cloned with `--depth 1` into `vendor/` (read-only use; the only test-side file mutations were reverted with `git checkout`).

| Tool (commit) | What it does | CLI / API surface | Install | Tests run here |
| --- | --- | --- | --- | --- |
| **somnus-plot** v0.7.0 (`778c9d4`) | Node-stdlib seed (9,061-byte `main.js`, 9,063 ceiling) that builds environments by composition: owned acquisitions into `.grave/`, transactional views, literal native steps; acquired resident supervisor, Node dependency planner (`capabilities/cartographer.mjs`), offline assembler, host bootstrap. Trusted execution, **not a sandbox**. | `adapters/cli/plot.mjs` → `plot init/adopt/roots/which/doctor/maintain`, `plot env list/show/make`, `plot run [VIEW] CMD…`, `plot job …`, `plot session …`, `plot cart/asm/host …`; JSON on stdout, `{"error"}` + exit 1 on failure. `plot-mcp` stdio server (19 tools). | No npm deps. `npm install -g ./adapters` (or `PLOT_DIST=<checkout>`) | `tests/cartographer.mjs` **12/12, exit 0**. `adapters/test/cli.mjs` **17/17, exit 0**. `tests/integration.mjs` **15/15 passed, then aborted, exit 1** (`spawnSync cc ENOENT` while building the C-extension wheel fixture; the suite has 24). `tests/assembler.mjs` **13/15, exit 1**: (a) `fixture prerequisite missing: cc`; (b) `closure target linux/x64 is not this host linux/arm64`. Both failures are environmental and NOT VERIFIABLE here, not Plot regressions (Plot AGENTS.md law 5). **Not run (heavy or needs cc):** `scripts/gate.sh`, resident 41, resident-env 21, bootstrap 21, e2e 12, package-smoke, adapters/test/mcp 10. |
| **somnus-debug** 0.1.0 (`c966be1`) | Python production-readiness diagnostics (AST): stubs, placeholder returns, silent excepts, cycles, coverage, security patterns; structure indexer; pycache cleaner; test-harness scaffold | `somnus-debug doctor scan/init-config/history/rollback/self-check`, `structure`, `pycache-clean`, `init-test-harness`; aliases `somnus-doctor` etc. `doctor scan` exits **1 when serious issues exist** (findings, not crash) | `pip install somnus-debug` (dep: pyyaml) | `pytest -q tests`: **11 passed, exit 0** |
| **somnus-metadata** 2.2.1 (`9a197f6`) | Metadata standard core 2.0.0: one `somnus:` header per file (frontmatter / comment envelope / sidecar), global SQLite registry with FTS, watcher, TUI, MCP | `somnus init/new/stamp/check/index/q/ls/status/bump/set/fork/regions/migrate/stale/schema/brief/edit/watch/daemon/mcp`. Exit 0 ok / 1 validation / 2 usage | `pip install somnus-metadata` (no deps, py≥3.11) | `PYTHONPATH=src python -m unittest discover -s tests`: **579 tests OK, exit 0** |
| **somnus-metadata-plugin** 1.3.0 (`5514312`) | Host adapter (Codex, Factory, Antigravity, OpenCode): installs the shared skill and registers `somnus --actor … mcp`; holds no metadata logic | `somnus-plugin` (Node) + native plugin payload; `mcp.json` | `codex plugin add …` / `droid plugin install …`, or direct adapter | `npm test` (native-payload check + `node --test`): **28/28 pass, exit 0** |
| **SDGC** 0.2.0 (`/workspace/work/ghdev/sdgc`) | Compiles a repo into a discovery surface: inventory, concept graph, generated docs, scholarly metadata, provenance, run snapshot; `compare` detects regressions | `sdgc init/scan/compile/validate/report/compare/selftest` | `pip install <checkout>` (no deps) | `sdgc selftest` **PASS, exit 0**; `pytest`: **16 collected, 16 passed, exit 0** |
| **somnus-github-development** skill | 6-step procedure plus `assets/.github` (bug/feature issue forms, PR template, ci/cd/dependency-rev workflows) and a `filetree.md` template with ø/○/♤ roles | Procedure only; no CLI | — | n/a |

Raw outputs: `evidence/{plot-*,debug-pytest,metadata-unittest,plugin-npmtest,sdgc-selftest,sdgc-pytest}.txt`.

## 2. Design

### 2.1 Commands

```
somnus-kit init <repo> [--gate CMD]... [--plot-dist DIR] [--github owner/repo]
                       [--cartographer-cmd CMD] [--no-agents-edit] [--force] [--json]
somnus-kit check [repo] [--only gates,metadata,debug,sdgc,github,wiki,map,filetree,agents] [--json]
somnus-kit owners [repo]      # the one-writer-per-path table
somnus-kit agents [repo]      # re-render the kit-owned AGENTS.md region from kit.json
```
Exit codes: 0 PASS/WARN, 1 FAIL, 2 usage or precondition (e.g. already initialised, ownership conflict; nothing is written).
Status vocabulary: PASS / WARN / FAIL / NOT_RUN. A missing tool or precondition is always NOT_RUN with the reason,
never a pass.

### 2.2 Per-codebase layout (as laid down on the SDGC copy)

```
<repo>/
├── AGENTS.md                      operator file; kit owns only <!-- somnus-kit:begin … --> … <!-- somnus-kit:end -->
├── filetree.md                    seeded once from the generated tree (ø ○ ♤ glyphs), then the agent maintains it
├── WIKI.md, WIKI.zh-CN.md         seeded once from wiki-template; zh source_sha256 = sha256(WIKI.md body after first "-->")
├── sdgc.yaml                      operator (adopted); seeded by `sdgc init` only if absent
├── .somnus.toml                   seeded once: [index].exclude (built-ins repeated + tool state, .github/**, WIKI*)
├── .github/…                      each missing file seeded individually from the skill's assets; existing files adopted
├── .plot/                         Plot project root (manifest.json + .grave/); view "gates"
├── .somnus-debug/
│   ├── python_doctor.yaml         seeded: doctor init-config output + 4 kit ignore patterns
│   ├── runs/baseline.{md,json}    accepted diagnostic state (doctor writes)
│   └── runs/latest.{md,json}      each check (doctor writes)
├── .python_doctor/                doctor state DB (doctor writes)
└── .somnus-kit/
    ├── kit.json                   manifest (schema somnus-kit.manifest/1), written last by init
    ├── .gitignore                 state/, sdgc/runs/latest/
    ├── filetree.generated.md      regenerated every run
    ├── metadata/baseline.json     index counts at init
    ├── sdgc/effective-config.json operator sdgc.yaml (parsed with SDGC's own loader) + kit state exclusions
    ├── sdgc/runs/{baseline,latest}/  SDGC writes
    ├── map/<adapter>/map.json     codebase map (contract somnus-kit.map/1)
    └── state/                     INIT.md, CHECK.md, {init,check}-<ts>.json, *-latest.json, written.json
```
This follows the skill's supplied baseline tree (`.somnus-debug/python_doctor.yaml`, `.somnus-debug/runs/`,
`.python_doctor/`). The kit adds only `.somnus-kit/`, `.somnus.toml` and `.plot/`.

### 2.3 Manifest (`.somnus-kit/kit.json`, JSON so the kit stays stdlib)

Keys: `schema`, `kit_version`, `created`, `repo` {root, name, dir, slug, git_sha, github, languages}, `tools`
{plot|metadata|debug|sdgc: argv, python, version, …}, `order`, `gates` [{id, cmd, source}], `adapters`
{cartographer: {status absent|configured, contract, command|reference_adapter}}, `baselines`, `sources`,
`ownership` {path: {owner, class, tree, sha256}}. The real manifest from the trial is
`targets/sdgc-run/.somnus-kit/kit.json`. Check results use schema `somnus-kit.check/1` and record every
command's argv, cwd, exit code, seconds and output tail.

### 2.4 Order of operations

**init:** preflight (discover tools + versions, repo identity, languages) → **ownership plan** (claim every path
first; a second writer aborts with exit 2 before anything is written) → plot (`plot init|adopt`, `plot env make gates`)
→ metadata-seed (`.somnus.toml`) → somnus-debug (seed config, baseline scan) → sdgc (`sdgc init` if needed, effective
config, baseline compile + validate) → github (seed missing files) → wiki (seed + translation hash) → map (adapter)
→ filetree (generated + seed root) → agents (region) → **metadata-index** (`somnus init --root`, `somnus index`,
run last so the baseline counts what the kit seeded) → manifest (written last, so a crashed init has no
manifest and can be rerun).

**check:** preflight (tool version drift, seeded/adopted files that disappeared) → gates via `plot run gates sh -c
'cd <repo> && CMD'` → metadata index vs baseline → doctor latest vs baseline → sdgc compile latest, validate, compare
`--fail-on-regression` → .github presence + YAML parse + dependency-input sanity → wiki placeholders + translation
freshness → map rerun + contract validation + diff → filetree regenerate + drift vs root → AGENTS region present/current.

FAIL rules: gate exit ≠ 0; doctor `critical|serious|syntax_error_files|dependency_cycles` up vs baseline; sdgc
validate/compare nonzero; invalid metadata headers or registry conflicts; a workflow that does not parse; an adapter
that breaks the map contract. Everything else that drifts is WARN.

### 2.5 Ownership: one writer per file

Classes: **generated** (owner rewrites every run; nobody else edits), **seeded** (written once if absent, then
operator/agent-owned; the kit never rewrites it), **region** (the owner controls a delimited block inside someone
else's file), **adopted** (existed before; read/validated only), **external** (outside the repo).

| Path | Writer | Class |
| --- | --- | --- |
| `.plot/` | somnus-plot | generated (adopted if `.plot/manifest.json` pre-exists) |
| `.somnus-debug/runs/`, `.somnus-debug/production_doctor.log`, `.python_doctor/` | somnus-debug | generated |
| `.somnus-kit/sdgc/runs/` | sdgc | generated |
| `.somnus-kit/map/` | the registered map adapter | generated |
| `.somnus-kit/{kit.json,.gitignore,filetree.generated.md,sdgc/effective-config.json,metadata/,state/}` | somnus-kit | generated |
| `.somnus.toml`, `.somnus-debug/python_doctor.yaml`, `filetree.md`, `WIKI.md`, `WIKI.zh-CN.md`, `.github/*` | somnus-kit seed → operator/agent | seeded (adopted if present) |
| `sdgc.yaml` | `sdgc init` seed → operator | seeded / adopted |
| `AGENTS.md#somnus-kit` | somnus-kit | region |
| embedded `somnus:` header in any managed file | somnus-metadata | region (the rest of the file belongs to its owner) |
| `$SOMNUS_HOME/somnus.db`, `$PLOT_HOME/registry.json` | metadata / plot | external |

Enforcement in the prototype:
- `Ledger.claim()` refuses a second owner of a path, and refuses a claim inside a directory another owner holds,
  or over one. This is unit tested.
- Kit-generated files are sha-tracked in `state/written.json`. A hand edit is reported (shown in check 2).
- SDGC never touches `sdgc.yaml`, because the kit feeds it a derived config.
- Doctor's log lands in `.somnus-debug/`, because the kit runs it with that cwd.
- Metadata is configured not to stamp `.github/**` or `WIKI*`. A PR template would render frontmatter in every
  PR body, and wiki pages are published verbatim.

### 2.6 How agents are instructed

`init` appends one marked region to AGENTS.md (text in `kit/somnus_kit/agents_section.py`; rendered copy in
`targets/sdgc-run/AGENTS.md`). It contains a 10-step loop:

1. Enter via `somnus-kit check`, then read CHECK.md.
2. Run gates only through Plot.
3. Stamp new files with `somnus stamp`, and search with `somnus q --text`.
4. Treat a doctor baseline regression as a failure.
5. Keep SDGC output in its run dir and never copy it over root docs.
6. Reconcile filetree.md against the generated tree, keeping ø/○/♤.
7. Edit WIKI.md first, then re-translate and update `source_sha256`.
8. Use the map adapter's output (contract name and status are given).
9. Follow the ownership list, never editing generated files.
10. Never push or publish.

The rest of AGENTS.md is untouched; the trial diff is **41 insertions, 0 deletions**. `--no-agents-edit`
writes the section to `state/` instead, for repos whose AGENTS.md must not change without the operator.

### 2.7 Cartographer adapter interface (`somnus-kit.map/1`)

Invocation: `<command…> --root <repo> --out <dir>`. The adapter exits 0 and writes `<dir>/map.json`:

```json
{ "schema": "somnus-kit.map/1",
  "adapter": {"name": "...", "version": "..."},
  "root": "/abs/repo", "generated_at": "ISO-8601+offset",
  "nodes": [{"id": "src/x.py", "kind": "file|module|package|symbol", "path": "src/x.py", "lang": "python"}],
  "edges": [{"from": "id", "to": "id", "kind": "imports|calls|contains|references"}],
  "entrypoints": ["id"], "notes": ["limits the adapter declares"] }
```

- The kit validates the shape: schema, types, node kinds, dangling edges and entrypoints. It stores the map
  under `.somnus-kit/map/<adapter>/` and diffs node and edge sets against the previous run.
- Registering Cartographer means putting its command in `kit.json → adapters.cartographer`
  (`{"status":"configured","command":[…]}`, or `init --cartographer-cmd`). No kit code changes.
- Until then, the reference adapter `doctor-graph` fills the slot. It converts somnus-debug's static import
  graph into the contract (42 nodes / 77 local import edges on SDGC). It is labelled everywhere as a stand-in,
  not Cartographer.
- Naming hazard: Plot already ships a different `capabilities/cartographer.mjs` (a Node dependency-closure
  planner, `plot cart`). Keep Daeron's Cartographer behind the adapter name so the two never get confused.

### 2.8 Tiniest Cloud (designed, not built)

The intended step is `somnus-kit preview`. It renders `.somnus-kit/state/check-latest.json` plus the doctor and
SDGC reports into a static internal dashboard, then deploys it through the Tiniest Cloud MCP server, internal
only. It would be read-only and would never run in `check`. It is not built here: this sandbox has no Tiniest
Cloud MCP connection, and `init` + `check` were the asked deliverable.

## 3. Prototype: what ran (evidence)

Code: `kit/somnus_kit/{core,steps,cli,filetree,cartographer,agents_section}.py`, `kit/bin/somnus-kit`, about 1.6k
lines, stdlib only (PyYAML is used for workflow parsing only if importable; otherwise that check is NOT_RUN).
Kit unit tests: `kit/tests/test_kit.py`, **12 tests OK, exit 0** (`evidence/kit-unittest.txt`). They cover
ledger conflicts, the filetree render/parse round trip, state-dir exclusion, AGENTS upsert idempotence with
operator text preserved, and map contract validation and diff.

Trial target: `targets/sdgc-run/`, a copy of `/workspace/work/ghdev/sdgc` with a local git baseline commit so the
kit's footprint is visible. Run by `run_trial.sh`.

| Run | Status / exit | What it showed |
| --- | --- | --- |
| `init` (`evidence/kit-init.txt`) | PASS / 0, about 3 s | 26 ownership claims, no conflicts. Plot root + `gates` view created. Doctor baseline: 42 files, 295 issues, 0 critical, 30 serious, 0 cycles. SDGC baseline compiled, validate exit 0. 6 `.github` files seeded. WIKI pair seeded with fresh translation hash. Map: 42 nodes / 77 edges. filetree 71 files, 0 drift. AGENTS region appended. Metadata: 0 managed / 62 unmanaged. |
| `check` clean (`evidence/kit-check-1-clean.txt`) | WARN / 0, about 5 s | Gates through Plot: pytest exit 0 (16 tests), `sdgc selftest` exit 0. Doctor +0. SDGC compare exit 0. WARNs are true: the skill's `ci.yml` and `dependency-rev.yml` install from `requirements.txt`, which SDGC does not have; WIKI.md has 104 placeholders left. |
| `check` with induced regressions (`evidence/kit-check-2-regressions.txt`) | **FAIL / 1** | A stub module was added: doctor **serious +3, total +10 → FAIL**, metadata unmanaged 62→63 → WARN, filetree drift 1 unlisted → WARN. Editing the WIKI body made the zh-CN translation **stale** → WARN. A hand edit to `.somnus-kit/filetree.generated.md` was detected as *modified outside its owner* → WARN. |
| `check` after revert (`evidence/kit-check-3-reverted.txt`) | WARN / 0 | Back to the clean state with the same counts. |

Footprint on the target (`evidence/kit-target-gitstatus.txt`): the only tracked file modified is `AGENTS.md`
(append-only). Everything else is new: `.github/ .plot/ .python_doctor/ .somnus-debug/ .somnus-kit/
.somnus.toml WIKI.md WIKI.zh-CN.md filetree.md`. `sdgc.yaml` is unchanged.

### Stubbed or not done (honest list)
- **Metadata stamping is not done by init.** Stamping writes headers into about 62 files, which is an explicit act
  for the agent or operator. Init registers and indexes, and check counts unmanaged growth. So "managed 0" is the
  real state of this repo.
- **Cartographer is absent.** The slot and contract are real, and the filled-in map comes from the `doctor-graph`
  reference adapter.
- **Tiniest Cloud preview is design only** (§2.8).
- **The seeded `.github` workflows are not adapted** to SDGC (no `requirements.txt`; ruff is assumed). Check flags
  this. Adapting them is step 4 of the skill and agent work.
- **WIKI content is not written.** Only facts the kit can prove are filled: name, source commit, and date in the
  header. Owner/repo are left blank because the copy has no git remote and I did not invent one.
- **Plot gates use an empty capability set on the host's shallow Python.** Plot does not provision the Python
  toolchain for gates yet; the gates run under the venv on PATH.
- **Gate auto-detection** is only pytest (pyproject + tests/) and `npm test`. The trial passed SDGC's two required
  commands via `--gate`.

## 4. Things found in the tools that the kit has to work around (worth fixing upstream)

1. **somnus-debug** writes `production_doctor.log` into the *current directory*, wherever that is
   (`logging.FileHandler("production_doctor.log")`, doctor/core.py:2149). The kit pins cwd to `.somnus-debug/`.
   Upstream fix: log under `state_dir`.
2. **somnus-metadata**: a local `.somnus.toml` `[index].exclude` *replaces* the built-in list instead of extending
   it (config.py:253–254). The kit repeats the built-ins. Unmanaged files also produce warnings only on `check`, so
   the kit reads `index` counts instead.
3. **Plot** `plot run` executes with cwd = the `.plot/` root, not the project (verified: `os.getcwd()` →
   `/tmp/ptest/.plot`). So gates are wrapped in `sh -c 'cd <repo> && …'`. Root names live in the global
   registry, which is keyed by name, so two repos with the same declared project name could collide. Not tested
   here.
4. **SDGC** compiles whatever is in the tree, including kit-seeded WIKI/filetree/workflows (they show up as
   added concept coverage in `compare`). That is correct behaviour; it is why the baseline is compiled *after*
   seeding.
5. **Skill assets**: `ci.yml` and `dependency-rev.yml` hard-code `requirements.txt` and ruff (the Python 3.11
   profile the skill says to use "only when it fits"). Check surfaces it per repo.

## 5. Scope incidents
- During setup, `pip install -e /workspace/work/ghdev/sdgc` created
  `/workspace/work/ghdev/sdgc/src/somnus_discovery_graph_compiler.egg-info/`. That is outside my write scope. I
  deleted it immediately and installed SDGC from a copy (`pkgsrc/sdgc`). Afterwards, `diff -rq` of the original
  against `targets/sdgc` showed no differences, and `src/` again contains only `sdgc/`.
- Running Plot's cartographer and assembler tests at the same time raced on `tests/fixtures/closure/sample-plan.json`.
  The assembler was rerun alone (the 13/15 above is the solo run), and the vendor tree was reset with `git checkout`.

## 6. Files
- `REPORT.md`: this report.
- `run_trial.sh`: reproducible trial, using an isolated `PLOT_HOME` / `SOMNUS_HOME`.
- `kit/`: the prototype (`bin/somnus-kit`, `somnus_kit/`, `tests/`).
- `evidence/`: every test output and kit run, each ending with `exit=N`.
- `targets/sdgc/`: pristine copy of SDGC. `targets/sdgc-run/`: the initialised trial repo (inspect `.somnus-kit/kit.json`, `state/CHECK.md`, `AGENTS.md`).
- `vendor/`: the four public repos (depth-1 clones). `pkgsrc/sdgc`: install source. `.venv/`: tool environment. `.isohome/`: isolated registries.

## 7. Next independent lanes
1. Wire Daeron's Cartographer: implement `--root/--out → map.json` on its side and register the command. No kit change.
2. `somnus-kit stamp` (opt-in): run `somnus stamp --write` over the inventory minus the exclusions, then record a
   managed baseline.
3. Gate capabilities in Plot: declare the repo's Python deps as a `python-packages` tenant so gates stop relying on
   the host venv. This needs network or a wheel bank, plus a cc for C wheels.
4. `somnus-kit preview` on Tiniest Cloud (internal).
5. Upstream fixes from §4 (items 1 and 2 are a few lines each).
