"""somnus-kit v2 tests: every verify check with positive and negative cases, plus brief, init placement,
hooks chaining, fleet, and CLI exit codes. Needs `somnus` (somnus-metadata) and `somnus_debug` importable;
tests that need them are skipped with the reason otherwise.

Run: python -m unittest discover -s tests -v   (from the kit dir)
"""

from __future__ import annotations

import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT))

from somnus_kit import (agents, carto, cli, debug, docgate, filetree, gitio, globs, home, hooks, initcmd,  # noqa: E402
                        libs, manifest as M, roles, session, snapshot, verify)

HAVE_META = libs.metadata() is not None
HAVE_DEBUG = libs.debug() is not None
need_meta = unittest.skipUnless(HAVE_META, "somnus-metadata not importable")
need_debug = unittest.skipUnless(HAVE_DEBUG, "somnus_debug not importable")

CORE = '''"""Core module."""


def walk(paths: list[str]) -> list[str]:
    """Return paths that are not hidden."""
    return [p for p in paths if not p.startswith(".")]
'''
OTHER = '''"""Other module."""


def size(items: list[str]) -> int:
    """Return how many items there are."""
    return len(items)
'''
TEST_CORE = '''from pkg.core import walk


def test_walk():
    assert walk([".a", "b"]) == ["b"]
'''


def sh(cwd, *args, check=True, env=None):
    p = subprocess.run(list(args), cwd=str(cwd), capture_output=True, text=True, env=env)
    if check and p.returncode != 0:
        raise AssertionError(f"{args}: {p.returncode}\n{p.stdout}\n{p.stderr}")
    return p


def commit(repo, msg="c", env=None):
    sh(repo, "git", "add", "-A")
    return sh(repo, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", msg, check=False, env=env)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kit-test-"))
        self.home = self.tmp / "home"
        self._env = os.environ.get("SOMNUS_HOME")
        os.environ["SOMNUS_HOME"] = str(self.home)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        files = {
            "AGENTS.md": "# AGENTS.md\n\nOperator rules.\n",
            "README.md": "# Demo\n\nSee [core](src/pkg/core.py).\n",
            "NOTEPAD.md": "# Notepad\n",
            "src/pkg/__init__.py": '"""Package."""\n',
            "src/pkg/core.py": CORE,
            "src/pkg/other.py": OTHER,
            "tests/test_core.py": TEST_CORE,
        }
        for rel, text in files.items():
            p = self.repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        sh(self.repo, "git", "init", "-q")
        commit(self.repo, "base")
        self.rh = home.RepoHome(self.repo)

    def tearDown(self):
        if self._env is None:
            os.environ.pop("SOMNUS_HOME", None)
        else:
            os.environ["SOMNUS_HOME"] = self._env
        shutil.rmtree(self.tmp, ignore_errors=True)

    # helpers
    def write(self, rel, text, append=False):
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if append:
            text = p.read_text(encoding="utf-8") + text
        p.write_text(text, encoding="utf-8")

    def init(self, **kw):
        args = dict(adapters=[], callers=None, protect=[], no_metadata=True, cartographer=None)
        args.update(kw)
        rep = initcmd.init(self.repo, **args)
        commit(self.repo, "init")
        return rep

    def brief(self, task="fix walk in core", scopes=None):
        return session.brief(self.repo, task, scopes or [], rh=self.rh)

    def verify_session(self, sess, operator=False):
        return verify.run(self.repo, snapshot.from_session(self.repo, sess), sess, operator=operator, rh=self.rh)

    def verify_worktree(self, operator=False):
        return verify.run(self.repo, snapshot.from_worktree(self.repo), None, operator=operator, rh=self.rh)

    def check(self, rep, cid):
        return next(c for c in rep["checks"] if c["id"] == cid)


# --------------------------------------------------------------------------- units


class UnitTests(unittest.TestCase):
    def test_remote_parsing(self):
        self.assertEqual(home.parse_remote("git@github.com:calisweetleaf/somnus-debug.git"), ("calisweetleaf", "somnus-debug"))
        self.assertEqual(home.parse_remote("https://github.com/calisweetleaf/sdgc"), ("calisweetleaf", "sdgc"))
        self.assertIsNone(home.parse_remote(""))

    def test_globs(self):
        self.assertTrue(globs.match("a/b/c.py", "**/*.py"))
        self.assertTrue(globs.match("c.py", "**/*.py"))
        self.assertFalse(globs.match("a/c.py", "*.py"))
        self.assertTrue(globs.match(".github/workflows/x.yml", ".github/workflows/**"))
        self.assertTrue(globs.match("docs/x/y.md", "docs/"))

    def test_role_convention_and_override(self):
        man = M.load(Path("/nonexistent"))
        self.assertEqual(roles.resolve("AGENTS.md", None, man).glyph, roles.OPERATOR)
        self.assertEqual(roles.resolve("docs/PLAN.md", None, man).glyph, roles.OPERATOR)
        self.assertEqual(roles.resolve("STATE.md", None, man).glyph, roles.LIVING)
        self.assertEqual(roles.resolve("README.md", None, man).glyph, roles.PUBLIC)
        self.assertIsNone(roles.resolve("src/plan.py", None, man).glyph)
        man["roles"]["overrides"] = {"docs/OPS.md": "ø", "README.md": "none"}
        self.assertEqual(roles.resolve("docs/OPS.md", None, man).source, "override")
        self.assertIsNone(roles.resolve("README.md", None, man).glyph)

    @need_meta
    def test_role_from_metadata_header(self):
        man = M.load(Path("/nonexistent"))
        hdr = lambda extra: ('---\nsomnus:\n  schema: "somnus://metadata/core/2.0.0"\n  title: "x"\n' + extra + "---\n# x\n").encode()
        tagged = hdr('  classification:\n    tags: ["boundary:operator"]\n')
        self.assertEqual(roles.resolve("docs/ops.md", tagged, man).source, "metadata-tag")
        public = hdr('  access:\n    visibility: "public"\n')
        r = roles.resolve("docs/guide.md", public, man)
        self.assertEqual((r.glyph, r.source), (roles.PUBLIC, "metadata-visibility"))
        living = hdr('  classification:\n    type: "notepad"\n')
        self.assertEqual(roles.resolve("docs/log.md", living, man).glyph, roles.LIVING)
        # the convention outranks inferred metadata (somnus-metadata stamps PLAN.md as role=working)
        plan = hdr('  classification:\n    type: "plan"\n    role: "working"\n')
        self.assertEqual(roles.resolve("PLAN.md", plan, man).glyph, roles.OPERATOR)

    def test_agents_upsert_idempotent_and_preserves_operator_text(self):
        man = M.load(Path("/nonexistent"))
        once = agents.upsert("# A\n\nkeep me\n", man)
        twice = agents.upsert(once, man)
        self.assertEqual(once, twice)
        self.assertIn("keep me", once)
        self.assertEqual(agents.strip(once), "# A\n\nkeep me\n")
        self.assertEqual(agents.extract(once), agents.render(man))

    def test_adapter_texts_are_pointers(self):
        self.assertTrue(agents.ADAPTERS["claude"]["text"].startswith("@AGENTS.md\n"))
        self.assertEqual(len([l for l in agents.ADAPTERS["claude"]["text"].splitlines() if l.strip()]), 2)
        self.assertIn("AGENTS.md", agents.ADAPTERS["copilot"]["text"])

    def test_docgate(self):
        man = M.load(Path("/nonexistent"))
        man["docgate"]["private_terms"] = ["Palimpsest"]
        tree = {"README.md", "src/a.py", "docs/x.md"}
        self.assertEqual(docgate.check("README.md", "# T\n\n[a](src/a.py) [d](docs/) [w](https://x.y)\n", tree, man), [])
        p = docgate.check("README.md", "# T\n\nTODO later [b](nope.md) Palimpsest\n", tree, man)
        self.assertTrue(any("placeholder" in x for x in p))
        self.assertTrue(any("broken link" in x for x in p))
        self.assertTrue(any("private term" in x for x in p))
        self.assertIn("no heading", docgate.check("README.md", "text only\n", tree, man))

    def test_filetree_parse_and_translation_staleness(self):
        md = "# t\n\n```text\nrepo/\n├── a.py\n└── d/\n    └── b.py      ○\n```\n"
        self.assertEqual(filetree.parse_tree(md), {"a.py", "d/b.py"})
        src = "<!-- wiki: x -->\nbody\n"
        good = next(iter(filetree.body_hashes(src)))
        files = {"WIKI.md": src, "WIKI.zh-CN.md": f"<!--\nsource_sha256: {good}\n-->\n译文\n"}
        self.assertEqual(filetree.stale_translations(files), [])
        files["WIKI.md"] = src + "more\n"
        self.assertEqual(len(filetree.stale_translations(files)), 1)

    def test_normalize_impact_hops_and_weak_docs(self):
        j = {"files": ["a.py"], "importers": [{"file": "b.py", "depth": 1}, {"file": "c.py", "depth": 2}],
             "docs": [{"path": "AGENTS.md", "matched": [{"kind": "stem"}]}, {"path": "ARCH.md", "matched": [{"kind": "module"}]}],
             "tests": [{"file": "tests/test_a.py"}], "packages": [{"name": "p"}], "trust": {}}
        n = carto.normalize_impact(j, hops=1)
        self.assertEqual(n["importers"], ["b.py"])
        self.assertEqual(n["docs"], ["ARCH.md"])
        self.assertEqual(n["dropped"]["docs_weak"], ["AGENTS.md"])
        self.assertEqual(carto.normalize_impact(j, hops=2)["importers"], ["b.py", "c.py"])

    def test_regressions(self):
        base = {"summary": {"syntax_error_files": 0, "dependency_cycles": 0, "missing_imports": 0}, "issues": {}}
        cur = {"summary": {"syntax_error_files": 0, "dependency_cycles": 1, "missing_imports": 0},
               "issues": {"a.py": ["stubs|serious|f|Function body is a stub.", "x|minor|g|m"]}}
        r = debug.regressions(base, cur, ["a.py"])
        self.assertEqual(len(r["fail"]), 2)  # cycle rise + new serious stub
        self.assertEqual(len(r["warn"]), 1)
        self.assertEqual(debug.regressions(base, base, ["a.py"])["fail"], [])


# --------------------------------------------------------------------------- brief


class BriefTests(Base):
    @need_debug
    def test_fallback_scope_and_impact(self):
        s = self.brief("fix walk in core")
        self.assertEqual(s["scope"]["seeds"], ["src/pkg/core.py"])
        self.assertIn("tests/test_core.py", s["scope"]["allowed"])
        self.assertEqual(s["scope"]["impact"]["trust"]["source"], "kit-stand-in")
        self.assertTrue((self.rh.sessions / f"{s['id']}.json").is_file())
        self.assertEqual(session.active_id(self.rh), s["id"])
        self.assertTrue(Path(s["debug_baseline"]["path"]).is_file())
        self.assertEqual(sh(self.repo, "git", "status", "--porcelain").stdout, "")  # nothing written in tree

    def test_explicit_scope_and_end(self):
        s = self.brief("anything", ["src/pkg/other.py"])
        self.assertEqual(s["scope"]["source"], "--scope")
        self.assertEqual(session.end(self.rh), s["id"])
        self.assertIsNone(session.active_id(self.rh))


# --------------------------------------------------------------------------- checks


class ScopeCheck(Base):
    def test_in_scope_passes(self):
        s = self.brief("fix walk in core", ["src/pkg/core.py"])
        self.write("src/pkg/core.py", "\n# tweak\n", append=True)
        self.assertEqual(self.check(self.verify_session(s), "a")["status"], "PASS")

    def test_new_test_for_scoped_module_allowed(self):
        s = self.brief("x", ["src/pkg/core.py"])
        self.write("tests/test_core_extra.py", "def test_x():\n    assert True\n")
        self.assertEqual(self.check(self.verify_session(s), "a")["status"], "PASS")

    def test_out_of_scope_fails_with_session(self):
        s = self.brief("x", ["src/pkg/core.py"])
        self.write("src/pkg/other.py", "\n# drift\n", append=True)
        c = self.check(self.verify_session(s), "a")
        self.assertEqual(c["status"], "FAIL")
        self.assertEqual(c["files"], ["src/pkg/other.py"])

    def test_living_doc_exempt(self):
        s = self.brief("x", ["src/pkg/core.py"])
        self.write("NOTEPAD.md", "- learned a thing\n", append=True)
        self.assertEqual(self.check(self.verify_session(s), "a")["status"], "PASS")

    def test_range_without_session_warns(self):
        base = gitio.head(self.repo)
        self.write("src/pkg/other.py", "\n# drift\n", append=True)
        commit(self.repo)
        rep = verify.run(self.repo, snapshot.from_range(self.repo, f"{base}..HEAD"), None, rh=self.rh)
        self.assertEqual(self.check(rep, "a")["status"], "WARN")
        self.assertEqual(verify.exit_code(rep), 0)


class RolesCheck(Base):
    def test_operator_doc_edit_fails(self):
        self.write("AGENTS.md", "relaxed\n", append=True)
        c = self.check(self.verify_worktree(), "b")
        self.assertEqual((c["status"], c["files"]), ("FAIL", ["AGENTS.md"]))

    def test_operator_override_downgrades(self):
        self.write("AGENTS.md", "relaxed\n", append=True)
        self.assertEqual(self.check(self.verify_worktree(operator=True), "b")["status"], "WARN")

    def test_kit_region_only_change_passes(self):
        self.init()
        man = M.load(self.repo)
        man["protected"].append("secrets/**")
        M.save(self.repo, man)
        initcmd.apply_agents(self.repo, man)
        c = self.check(self.verify_worktree(), "b")
        self.assertEqual(c["status"], "PASS", c)

    def test_public_doc_gate(self):
        self.write("README.md", "# Demo\n\nSee [gone](missing.md).\n")
        self.assertEqual(self.check(self.verify_worktree(), "b")["status"], "FAIL")
        self.write("README.md", "# Demo\n\nSee [core](src/pkg/core.py) again.\n")
        self.assertEqual(self.check(self.verify_worktree(), "b")["status"], "WARN")

    def test_living_doc_passes(self):
        self.write("NOTEPAD.md", "- note\n", append=True)
        self.assertEqual(self.check(self.verify_worktree(), "b")["status"], "PASS")


class GeneratedCheck(Base):
    def test_adapter_hand_edit_fails_and_regen_passes(self):
        self.init(adapters=["claude"])
        self.assertEqual((self.repo / "CLAUDE.md").read_text(), agents.ADAPTERS["claude"]["text"])
        self.write("CLAUDE.md", "extra\n", append=True)
        c = self.check(self.verify_worktree(), "c")
        self.assertEqual((c["status"], c["files"]), ("FAIL", ["CLAUDE.md"]))
        initcmd.apply_agents(self.repo, M.load(self.repo))
        self.assertEqual(self.check(self.verify_worktree(), "c")["status"], "PASS")

    def test_region_hand_edit_fails(self):
        self.init()
        text = (self.repo / "AGENTS.md").read_text().replace("Never push", "Feel free to push")
        self.write("AGENTS.md", text)
        self.assertEqual(self.check(self.verify_worktree(), "c")["status"], "FAIL")

    def test_declared_generated_glob_fails(self):
        self.write("docs/api/index.md", "# api\n")
        man = M.load(self.repo)
        man["generated"] = [{"glob": "docs/api/**", "owner": "pdoc"}]
        M.save(self.repo, man)
        commit(self.repo)
        self.write("docs/api/index.md", "# api\nhand\n")
        c = self.check(self.verify_worktree(), "c")
        self.assertEqual((c["status"], c["files"]), ("FAIL", ["docs/api/index.md"]))

    def test_generated_marker_warns(self):
        self.write("gen.txt", "# @generated by tool\nx\n")
        commit(self.repo)
        self.write("gen.txt", "# @generated by tool\ny\n")
        self.assertEqual(self.check(self.verify_worktree(), "c")["status"], "WARN")


@need_meta
class MetadataCheck(Base):
    def setUp(self):
        super().setUp()
        self.init(no_metadata=False)
        self.assertTrue((self.repo / ".somnus.toml").is_file())

    def stamp(self, rel):
        from somnus import config, document as D
        from somnus.commands._common import inferred_payload
        path = self.repo / rel
        doc = D.load(path)
        path.write_text(doc.render(inferred_payload(config.load(path), path, doc.lines)), encoding="utf-8")

    def test_new_file_without_header_fails(self):
        self.write("src/pkg/new.py", '"""New."""\n')
        c = self.check(self.verify_worktree(), "d")
        self.assertEqual((c["status"], c["files"]), ("FAIL", ["src/pkg/new.py"]))

    def test_stamped_new_file_passes(self):
        self.write("src/pkg/new.py", '"""New."""\n')
        self.stamp("src/pkg/new.py")
        self.assertEqual(self.check(self.verify_worktree(), "d")["status"], "PASS")

    def test_excluded_path_not_required(self):
        self.write(".github/workflows/x.yml", "on: push\n")
        self.assertEqual(self.check(self.verify_worktree(), "d")["status"], "PASS")

    def test_severity_warn_override(self):
        man = M.load(self.repo)
        man["metadata"]["severity"] = "WARN"
        M.save(self.repo, man)
        commit(self.repo, "warn")
        self.write("src/pkg/new.py", '"""New."""\n')
        self.assertEqual(self.check(self.verify_worktree(), "d")["status"], "WARN")


class MetadataSkip(Base):
    def test_no_toml_skips(self):
        self.write("src/pkg/new.py", '"""New."""\n')
        self.assertEqual(self.check(self.verify_worktree(), "d")["status"], "SKIP")


@need_debug
class DebugCheck(Base):
    def test_stub_regression_fails(self):
        s = self.brief("x", ["src/pkg/core.py"])
        self.write("src/pkg/core.py", "\n\ndef later(x: int) -> int:\n    pass\n", append=True)
        c = self.check(self.verify_session(s), "e")
        self.assertEqual(c["status"], "FAIL", c)
        self.assertIn("src/pkg/core.py", c["files"])

    def test_clean_change_passes_staged(self):
        self.write("src/pkg/core.py", CORE.replace("hidden", "hidden (dotfiles)"))
        sh(self.repo, "git", "add", "-A")
        rep = verify.run(self.repo, snapshot.from_staged(self.repo), None, rh=self.rh)
        self.assertEqual(self.check(rep, "e")["status"], "PASS", self.check(rep, "e"))

    def test_no_python_change_skips(self):
        self.write("NOTEPAD.md", "x\n", append=True)
        self.assertEqual(self.check(self.verify_worktree(), "e")["status"], "SKIP")


class MissingLibs(Base):
    def test_debug_missing_is_not_run_not_pass(self):
        from unittest import mock
        self.write("src/pkg/core.py", "\n# x\n", append=True)
        with mock.patch.object(debug, "available", return_value=False):
            rep = self.verify_worktree()
        self.assertEqual(self.check(rep, "e")["status"], "NOT_RUN")
        self.assertEqual(rep["status"], "WARN")


class DriftCheck(Base):
    def test_filetree_drift_warns(self):
        self.write("filetree.md", "# tree\n\n```text\nrepo/\n├── AGENTS.md\n└── src/\n```\n")
        commit(self.repo)
        self.write("src/pkg/extra.py", '"""x."""\n')
        c = self.check(self.verify_worktree(), "f")
        self.assertEqual((c["status"], c["files"]), ("WARN", ["src/pkg/extra.py"]))

    def test_stale_translation_warns(self):
        src = "<!-- wiki -->\nhello\n"
        sha = next(iter(filetree.body_hashes(src)))
        self.write("WIKI.md", src)
        self.write("WIKI.zh-CN.md", f"<!--\nsource_sha256: {sha}\n-->\n你好\n")
        commit(self.repo)
        self.assertEqual(self.check(self.verify_worktree(), "f")["status"], "PASS")
        self.write("WIKI.md", src + "new section\n")
        c = self.check(self.verify_worktree(), "f")
        self.assertEqual((c["status"], c["files"]), ("WARN", ["WIKI.zh-CN.md"]))

    def test_nothing_to_check_skips(self):
        self.assertEqual(self.check(self.verify_worktree(), "f")["status"], "SKIP")


class ProtectedCheck(Base):
    def test_protected_fails_and_operator_warns(self):
        self.init(protect=["deploy/**"])
        self.write("deploy/prod.env", "X=1\n")
        c = self.check(self.verify_worktree(), "g")
        self.assertEqual((c["status"], c["files"]), ("FAIL", ["deploy/prod.env"]))
        self.assertEqual(self.check(self.verify_worktree(operator=True), "g")["status"], "WARN")

    def test_kit_json_protected(self):
        self.init()
        man = M.load(self.repo)
        man["protected"] = []
        M.save(self.repo, man)
        c = self.check(self.verify_worktree(), "g")
        self.assertEqual(c["status"], "FAIL")  # old manifest still protects kit.json
        self.assertIn(M.KIT_JSON, c["files"])


# --------------------------------------------------------------------------- init / hooks / fleet / cli


class InitPlacement(Base):
    @need_meta
    def test_only_allowed_files_written(self):
        rep = initcmd.init(self.repo, adapters=["claude", "copilot"], callers=None, protect=[], no_metadata=False,
                           cartographer=None)
        status = sh(self.repo, "git", "status", "--porcelain", "--untracked-files=all").stdout.splitlines()
        paths = sorted(l[3:] for l in status)
        self.assertEqual(paths, sorted([".github/copilot-instructions.md", ".somnus-kit/kit.json", ".somnus.toml",
                                        "AGENTS.md", "CLAUDE.md"]))
        self.assertEqual(sorted(rep["written_in_tree"]), paths)
        man = M.load(self.repo)
        self.assertEqual(man["ownership"]["CLAUDE.md"]["class"], "generated")
        self.assertNotIn(str(self.tmp), (self.repo / M.KIT_JSON).read_text())  # no absolute paths committed

    def test_reinit_is_idempotent(self):
        self.init()
        rep = initcmd.init(self.repo, adapters=[], callers=None, protect=[], no_metadata=True, cartographer=None)
        self.assertEqual(rep["written_in_tree"], [])

    def test_existing_claude_md_not_clobbered(self):
        self.write("CLAUDE.md", "operator notes\n")
        commit(self.repo)
        rep = initcmd.init(self.repo, adapters=["claude"], callers=None, protect=[], no_metadata=True, cartographer=None)
        self.assertEqual((self.repo / "CLAUDE.md").read_text(), "operator notes\n")
        self.assertTrue(any("CLAUDE.md" in w for w in rep["warnings"]))

    def test_callers_installed_and_owned(self):
        callers = self.tmp / "callers"
        callers.mkdir()
        (callers / "guard.yml").write_text("on: pull_request\n")
        initcmd.init(self.repo, adapters=[], callers=callers, protect=[], no_metadata=True, cartographer=None)
        self.assertTrue((self.repo / ".github/workflows/guard.yml").is_file())
        self.assertEqual(M.load(self.repo)["ownership"][".github/workflows/guard.yml"]["owner"], "somnus-github-development")


class HooksTests(Base):
    def test_install_chains_and_blocks(self):
        self.init()
        old = self.repo / ".git" / "hooks"
        marker = self.tmp / "chained.txt"
        for name in ("pre-commit", "commit-msg"):
            p = old / name
            p.write_text(f"#!/bin/sh\necho {name} >> {marker}\nexit 0\n")
            p.chmod(p.stat().st_mode | stat.S_IXUSR)
        rep = hooks.install(self.repo, self.rh)
        self.assertEqual(rep["shims"], ["commit-msg"])
        self.assertTrue(rep["chained_pre_commit"].endswith("pre-commit"))
        self.assertTrue(hooks.status(self.repo, self.rh)["installed"])
        env = dict(os.environ, PYTHONPATH=str(KIT) + os.pathsep + os.environ.get("PYTHONPATH", ""))
        # clean commit passes and runs both chained hooks
        self.write("NOTEPAD.md", "x\n", append=True)
        r = commit(self.repo, "ok", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(marker.read_text().split(), ["pre-commit", "commit-msg"])
        # an ø edit is blocked
        head = gitio.head(self.repo)
        self.write("AGENTS.md", "rogue\n", append=True)
        r = commit(self.repo, "rogue", env=env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("commit blocked", r.stderr)
        self.assertEqual(gitio.head(self.repo), head)
        # uninstall restores the previous state (no hooksPath)
        self.assertTrue(hooks.uninstall(self.repo, self.rh)["removed"])
        self.assertIsNone(gitio.config_get(self.repo, "core.hooksPath"))

    def test_respects_existing_hooks_path(self):
        custom = self.repo / ".githooks"
        custom.mkdir()
        (custom / "pre-push").write_text("#!/bin/sh\nexit 0\n")
        (custom / "pre-push").chmod(0o755)
        sh(self.repo, "git", "config", "core.hooksPath", ".githooks")
        rep = hooks.install(self.repo, self.rh)
        self.assertEqual(rep["previous_hooks_path"], ".githooks")
        self.assertEqual(rep["shims"], ["pre-push"])
        hooks.install(self.repo, self.rh)  # reinstall keeps the original chain
        self.assertEqual(json.loads((self.rh.hooks / "chain.json").read_text())["previous_hooks_path"], ".githooks")
        hooks.uninstall(self.repo, self.rh)
        self.assertEqual(gitio.config_get(self.repo, "core.hooksPath"), ".githooks")


class FleetTests(Base):
    def test_status_and_check(self):
        self.init()
        fleet_file = self.tmp / "fleet.json"
        fleet_file.write_text(json.dumps({"repos": [{"path": str(self.repo), "profile": "generic"},
                                                    {"path": str(self.tmp / "missing")}]}))
        from somnus_kit import fleet
        st = fleet.status(str(fleet_file))
        self.assertEqual([r["state"] for r in st["repos"]], ["ATTENTION", "MISSING"])  # no hooks yet
        ck = fleet.check(str(fleet_file))
        self.assertEqual(ck["status"], "FAIL")
        fleet_file.write_text(json.dumps({"repos": [{"path": str(self.repo)}]}))
        self.assertEqual(fleet.check(str(fleet_file))["status"], "PASS")
        self.write("AGENTS.md", "rogue\n", append=True)
        ck = fleet.check(str(fleet_file))
        self.assertEqual(ck["status"], "FAIL")
        self.assertIn("b.roles", ck["repos"][0]["failed_checks"])


class CliTests(Base):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_exit_codes(self):
        self.assertEqual(self.run_cli("verify", str(self.tmp))[0], 2)  # not a git repo
        code, out, _ = self.run_cli("verify", str(self.repo), "--json")
        self.assertEqual((code, json.loads(out)["status"]), (0, "PASS"))
        self.write("AGENTS.md", "rogue\n", append=True)
        code, out, _ = self.run_cli("verify", str(self.repo), "--json")
        rep = json.loads(out)
        self.assertEqual((code, rep["status"]), (1, "FAIL"))
        self.assertEqual(set(rep["checks"][0]) >= {"id", "status", "detail", "files"}, True)
        self.write(M.KIT_JSON, "{not json")
        self.assertEqual(self.run_cli("verify", str(self.repo))[0], 2)
        self.assertEqual(self.run_cli("verify", str(self.repo), "--staged", "--range", "a..b")[0], 2)
        self.assertEqual(self.run_cli("bogus")[0], 2)

    def test_verify_writes_report_out_of_tree(self):
        self.run_cli("verify", str(self.repo))
        self.assertTrue(list(self.rh.logs.glob("verify-*.json")))
        self.assertEqual(sh(self.repo, "git", "status", "--porcelain").stdout, "")


if __name__ == "__main__":
    unittest.main()
