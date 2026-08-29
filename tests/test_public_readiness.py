from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


class PublicReadinessTests(unittest.TestCase):
    def test_license_is_apache_2(self) -> None:
        license_text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("Apache License", license_text)
        self.assertIn("Version 2.0", license_text)
        self.assertNotIn("MIT License", license_text)

    def test_public_docs_stay_domain_neutral(self) -> None:
        docs = "\n".join(
            [
                (REPO_ROOT / "README.md").read_text(encoding="utf-8"),
                (REPO_ROOT / "SKILL.md").read_text(encoding="utf-8"),
            ]
        ).lower()
        project_specific_terms = [
            "".join(chr(code) for code in codes)
            for codes in (
                (103, 104, 105, 100, 114, 97),
                (119, 111, 114, 107, 102, 108, 111, 119, 107, 105, 116),
                (118, 111, 105, 99, 101, 115, 104, 111, 114, 116, 99, 117, 116, 115),
                (115, 104, 111, 114, 116, 99, 117, 116, 115, 32, 97, 114, 99, 104, 97, 101, 111, 108, 111, 103, 121),
                (98, 117, 103, 32, 104, 117, 110, 116, 105, 110, 103),
                (98, 117, 103, 45, 104, 117, 110, 116, 105, 110, 103),
                (98, 111, 117, 110, 116, 121),
                (101, 120, 112, 108, 111, 105, 116, 97, 98, 105, 108, 105, 116, 121),
                (112, 114, 105, 118, 97, 116, 101, 32, 97, 112, 105),
            )
        ]
        for term in project_specific_terms:
            with self.subTest(term=term):
                self.assertNotIn(term, docs)

    def test_sqlite_is_documented_as_derived_state(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8").lower()
        self.assertIn("sqlite is a derived search index only", readme)
        self.assertIn("markdown and json/jsonl remain the authoritative state", readme)

    def test_install_script_is_documented_and_dry_run_is_json(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("python3 scripts/install_skill.py --json", readme)
        self.assertIn("python3 scripts/install_skill.py --execute --host auto", readme)

        result = subprocess.run(
            [
                sys.executable,
                "scripts/install_skill.py",
                "--json",
                "--target",
                "/tmp/long-run-agent-public-readiness-target",
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["execute"])
        self.assertEqual(payload["steps"][0]["status"], "pending")
        self.assertEqual(
            payload["steps"][0]["target"],
            str(pathlib.Path("/tmp/long-run-agent-public-readiness-target").resolve()),
        )

    def test_install_script_execute_copies_skill_without_cache_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            (source / "SKILL.md").write_text("---\nname: long-run-agent\n---\n", encoding="utf-8")
            (source / "README.md").write_text("# Long Run Agent\n", encoding="utf-8")
            (source / "scripts").mkdir()
            (source / "scripts" / "tool.py").write_text("print('ok')\n", encoding="utf-8")
            (source / ".git").mkdir()
            (source / ".git" / "config").write_text("private git state\n", encoding="utf-8")
            (source / "__pycache__").mkdir()
            (source / "__pycache__" / "tool.cpython.pyc").write_text("cache\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "install_skill.py"),
                    "--json",
                    "--execute",
                    "--source",
                    str(source),
                    "--target",
                    str(target),
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=True,
            )
            payload = json.loads(result.stdout)
            self.assertTrue(payload["execute"])
            self.assertEqual(payload["steps"][0]["status"], "installed")
            self.assertTrue((target / "SKILL.md").exists())
            self.assertTrue((target / "scripts" / "tool.py").exists())
            self.assertFalse((target / ".git").exists())
            self.assertFalse((target / "__pycache__").exists())

    def test_live_md_is_the_only_documented_control_plane(self) -> None:
        docs = "\n".join(
            [
                (REPO_ROOT / "README.md").read_text(encoding="utf-8"),
                (REPO_ROOT / "SKILL.md").read_text(encoding="utf-8"),
                (REPO_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8"),
            ]
        )
        self.assertIn(".agent/live.md", docs)
        legacy_name = "".join(chr(code) for code in (67, 79, 78, 84, 82, 79, 76, 46, 109, 100))
        self.assertNotIn(legacy_name, docs)
        self.assertNotIn("sync-control", docs)

    def test_public_code_files_stay_under_line_limit(self) -> None:
        oversized = []
        for path in REPO_ROOT.rglob("*"):
            if ".git" in path.parts or "__pycache__" in path.parts:
                continue
            if path.suffix not in {".py", ".sh", ".ps1"}:
                continue
            line_count = len(path.read_text(encoding="utf-8").splitlines())
            if line_count > 1000:
                oversized.append(f"{path.relative_to(REPO_ROOT)}:{line_count}")

        self.assertEqual(oversized, [])

    def test_mcp_is_documented_without_replacing_authoritative_state(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("long-run-agent-mcp", readme)
        self.assertIn("mission_control_read", readme)
        self.assertIn("Durable truth still", readme)

    def test_terminal_state_handoff_is_documented(self) -> None:
        skill = (REPO_ROOT / "SKILL.md").read_text(encoding="utf-8")
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        normalized_skill = " ".join(skill.lower().split())
        normalized_readme = " ".join(readme.lower().split())
        self.assertIn("keep the parent turn active", normalized_skill)
        self.assertIn("completion, blocked, or question message", normalized_skill)
        self.assertIn("cannot wake a finished parent-agent turn", normalized_readme)
        self.assertIn("automatic chat notification is unavailable", normalized_readme)
