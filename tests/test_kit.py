import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from somnus_kit import agents_section, cartographer, filetree  # noqa: E402
from somnus_kit.core import GENERATED, SEEDED, Ledger, OwnershipError  # noqa: E402


class LedgerTests(unittest.TestCase):
    def test_second_writer_is_refused(self):
        L = Ledger(Path("/x"))
        L.claim("a.md", "kit", SEEDED)
        with self.assertRaises(OwnershipError):
            L.claim("a.md", "sdgc", GENERATED)

    def test_file_inside_foreign_tree_is_refused(self):
        L = Ledger(Path("/x"))
        L.claim(".plot", "somnus-plot", GENERATED, "tree")
        with self.assertRaises(OwnershipError):
            L.claim(".plot/manifest.json", "kit", GENERATED)

    def test_tree_over_foreign_file_is_refused(self):
        L = Ledger(Path("/x"))
        L.claim(".somnus-kit/sdgc/runs/x", "sdgc", GENERATED)
        with self.assertRaises(OwnershipError):
            L.claim(".somnus-kit/sdgc/runs", "kit", GENERATED, "tree")

    def test_same_owner_may_reclaim(self):
        L = Ledger(Path("/x"))
        L.claim("a", "kit", SEEDED)
        L.claim("a", "kit", SEEDED)


class FiletreeTests(unittest.TestCase):
    def test_render_parse_roundtrip(self):
        paths = ["AGENTS.md", "README.md", "src/pkg/__init__.py", "src/pkg/core.py", "tests/test_a.py", ".github/workflows/ci.yml"]
        doc = filetree.render_document(paths, "demo")
        self.assertEqual(filetree.parse_tree(doc), set(paths))

    def test_only_first_block_is_actual_tree(self):
        doc = "```text\n├── a.md\n└── b.md\n```\n\n## baseline\n```text\n└── proposal.md\n```\n"
        self.assertEqual(filetree.parse_tree(doc), {"a.md", "b.md"})

    def test_state_dirs_excluded(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            for rel in ("a.py", ".plot/.grave/x", ".somnus-kit/state/y.json", ".somnus-debug/runs/z.json",
                        ".somnus-debug/python_doctor.yaml", ".python_doctor/state.sqlite3"):
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text("x")
            self.assertEqual(filetree.list_files(root), [".somnus-debug/python_doctor.yaml", "a.py"])


class AgentsTests(unittest.TestCase):
    def test_upsert_is_idempotent_and_preserves_operator_text(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t, "AGENTS.md")
            p.write_text("# Operator\n\nkeep me\n")
            block = agents_section.render({"gates": [{"cmd": "pytest"}], "ownership": {}, "adapters": {}})
            self.assertEqual(agents_section.upsert(p, block), "appended")
            self.assertEqual(agents_section.upsert(p, block), "unchanged")
            block2 = block.replace("pytest", "pytest -x")
            self.assertEqual(agents_section.upsert(p, block2), "replaced")
            text = p.read_text()
            self.assertTrue(text.startswith("# Operator\n\nkeep me\n"))
            self.assertEqual(text.count(agents_section.END), 1)


class MapContractTests(unittest.TestCase):
    def good(self):
        return {"schema": cartographer.CONTRACT, "adapter": {"name": "t", "version": "0"}, "root": "/r",
                "generated_at": "now", "nodes": [{"id": "a.py", "kind": "file", "path": "a.py", "lang": "python"},
                                                 {"id": "b.py", "kind": "file", "path": "b.py", "lang": "python"}],
                "edges": [{"from": "a.py", "to": "b.py", "kind": "imports"}], "entrypoints": ["a.py"], "notes": []}

    def test_valid(self):
        self.assertEqual(cartographer.validate_map(self.good()), [])

    def test_dangling_edge(self):
        m = self.good()
        m["edges"].append({"from": "a.py", "to": "zzz", "kind": "imports"})
        self.assertTrue(any("unknown node" in e for e in cartographer.validate_map(m)))

    def test_wrong_schema(self):
        m = self.good()
        m["schema"] = "other/1"
        self.assertTrue(cartographer.validate_map(m))

    def test_diff(self):
        a, b = self.good(), self.good()
        b["nodes"].append({"id": "c.py", "kind": "file", "path": "c.py", "lang": "python"})
        self.assertEqual(cartographer.diff_maps(a, b)["nodes_added"], 1)
        self.assertTrue(cartographer.diff_maps(None, b)["first_run"])


if __name__ == "__main__":
    unittest.main()
