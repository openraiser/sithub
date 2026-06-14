from __future__ import annotations

import contextlib
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from sit.cli import main


class CliTest(unittest.TestCase):
    def test_package_declares_pep561_type_marker(self) -> None:
        root = Path(__file__).resolve().parents[1]
        pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")

        self.assertTrue((root / "sit" / "py.typed").exists())
        self.assertIn("[tool.setuptools.package-data]", pyproject)
        self.assertIn('sit = ["py.typed"]', pyproject)

    def test_status_validate_and_test_pass_for_minimal_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp), version="0.1.0")

            status_code, status_output = _run_cli(["status", str(package)])
            validate_code, validate_output = _run_cli(["validate", str(package)])
            test_code, test_output = _run_cli(["test", str(package)])

            self.assertEqual(status_code, 0)
            self.assertIn("Package: sample-skill", status_output)
            self.assertEqual(validate_code, 0)
            self.assertIn("OK  schema.output JSON schema valid", validate_output)
            self.assertEqual(test_code, 0)
            self.assertIn("SUMMARY 1/1 golden cases passed", test_output)

    def test_status_shows_gate_preview_for_worktree_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "status-skill", version="0.1.0")
            _git(package, "init")
            _git(package, "config", "user.email", "sit@example.test")
            _git(package, "config", "user.name", "SIT Test")
            _git(package, "add", ".")
            _git(package, "commit", "-m", "feat: initial skill")
            (package / "prompts" / "extraction.md").write_text("Extract with care.", encoding="utf-8")

            code, output = _run_cli(["status", str(package)])

            self.assertEqual(code, 0)
            self.assertIn("Gate preview:", output)
            self.assertIn("block: required=minor", output)

    def test_verbose_status_shows_gate_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "verbose-status-skill", version="0.1.0")
            _git(package, "init")
            _git(package, "config", "user.email", "sit@example.test")
            _git(package, "config", "user.name", "SIT Test")
            _git(package, "add", ".")
            _git(package, "commit", "-m", "feat: initial skill")
            (package / "prompts" / "extraction.md").write_text("Extract with care.", encoding="utf-8")

            code, output = _run_cli(["--verbose", "status", str(package)])

            self.assertEqual(code, 0)
            self.assertIn("message: commit version gate blocked", output)

    def test_diff_reports_schema_and_prompt_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = _write_package(root / "old", version="0.1.0")
            new = _write_package(
                root / "new",
                version="0.2.0",
                prompt="Extract the answer and cite evidence.",
                extra_required={"confidence": {"type": "string"}},
            )

            code, output = _run_cli(["diff", str(old), str(new)])

            self.assertEqual(code, 0)
            self.assertIn("PROMPT changed extraction", output)
            self.assertIn("SCHEMA output property added confidence (required)", output)
            self.assertIn("RISK breaking-change", output)

    def test_diff_reports_non_golden_test_content_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = _write_package(root / "old", version="0.1.0")
            new = _write_package(root / "new", version="0.1.0")
            for package, content in ((old, "runner v1\n"), (new, "runner v2\n")):
                manifest = package / "skill.yaml"
                manifest.write_text(
                    manifest.read_text(encoding="utf-8").replace(
                        "  golden: tests/golden.jsonl\n",
                        "  golden: tests/golden.jsonl\n  runner: tests/runner.txt\n",
                    ),
                    encoding="utf-8",
                )
                (package / "tests" / "runner.txt").write_text(content, encoding="utf-8")

            code, output = _run_cli(["diff", str(old), str(new)])

            self.assertEqual(code, 0)
            self.assertIn("TEST changed runner", output)

    def test_diff_reports_resource_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = _write_package(root / "old", version="0.1.0")
            new = _write_package(root / "new", version="0.1.0")
            old_script = "\n".join(
                [
                    '"""Scan paper inputs."""',
                    "import argparse",
                    "parser = argparse.ArgumentParser()",
                    "parser.add_argument('--input')",
                    "def scan():",
                    "    return 'old'",
                    "",
                ]
            )
            new_script = "\n".join(
                [
                    '"""Scan paper inputs and PDF assets."""',
                    "import argparse",
                    "import subprocess",
                    "parser = argparse.ArgumentParser()",
                    "parser.add_argument('--input')",
                    "parser.add_argument('--pdf')",
                    "def scan():",
                    "    return 'new'",
                    "",
                ]
            )
            _write_resource_files(old, script=old_script, asset="template-v1\n", reference="guide v1\n")
            _write_resource_files(new, script=new_script, asset="template-v2\n", reference="guide v2\n")

            text_code, text_output = _run_cli(["diff", str(old), str(new)])
            json_code, json_output = _run_cli(["diff", str(old), str(new), "--format", "json"])
            report_code, report_output = _run_cli(["report", str(new), "--compare", str(old)])
            summary_code, summary_output = _run_cli(["pr-summary", str(old), str(new)])
            ci_code, ci_output = _run_cli(["ci-summary", str(new), "--compare", str(old)])

            self.assertEqual(text_code, 0)
            self.assertIn("SCRIPT changed scripts/scan.py", text_output)
            self.assertIn("summary: changed python script", text_output)
            self.assertIn("changes: added CLI args: --pdf", text_output)
            self.assertIn("ASSET changed assets/template.html", text_output)
            self.assertIn("REFERENCE changed references/guide.md", text_output)
            self.assertIn("RISK review-required", text_output)

            self.assertEqual(json_code, 0)
            payload = json.loads(json_output)
            self.assertEqual(payload["risk"], "review-required")
            self.assertEqual(payload["suggested_bump"], "minor")
            script_events = [event for event in payload["events"] if event["category"] == "script" and "scripts/scan.py" in event["message"]]
            self.assertTrue(script_events)
            self.assertEqual(script_events[0]["details"]["kind"], "script_summary")
            self.assertIn("--pdf", script_events[0]["details"]["cli_args"])
            self.assertIn("added CLI args: --pdf", script_events[0]["details"]["changes"])
            self.assertTrue(any(event["category"] == "asset" and "assets/template.html" in event["message"] for event in payload["events"]))
            self.assertTrue(any(event["category"] == "reference" and "references/guide.md" in event["message"] for event in payload["events"]))

            self.assertEqual(report_code, 0)
            self.assertIn("SCRIPT changed scripts/scan.py", report_output)
            self.assertIn("changes: added CLI args: --pdf", report_output)
            self.assertEqual(summary_code, 0)
            self.assertIn("ASSET changed assets/template.html", summary_output)
            self.assertIn("summary: changed python script", summary_output)
            self.assertEqual(ci_code, 0)
            self.assertIn("REFERENCE changed references/guide.md", ci_output)
            self.assertIn("changes: added CLI args: --pdf", ci_output)

    def test_diff_reports_recursive_schema_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = _write_package(root / "old", version="0.1.0")
            new = _write_package(root / "new", version="0.2.0")
            _write_nested_schema(old, enum_values=["paper", "web"], tag_values=["a", "b"], require_confidence=False, closed=False)
            _write_nested_schema(new, enum_values=["paper"], tag_values=["a", "b", "c"], require_confidence=True, closed=True)

            code, output = _run_cli(["diff", str(old), str(new)])

            self.assertEqual(code, 0)
            self.assertIn("SCHEMA output property added metadata.confidence (required)", output)
            self.assertIn("SCHEMA output enum removed values metadata.source: 'web'", output)
            self.assertIn("SCHEMA output enum added values metadata.tags[]: 'c'", output)
            self.assertIn("SCHEMA output additionalProperties restricted metadata", output)
            self.assertIn("RISK breaking-change", output)

    def test_diff_reports_complex_schema_ref_and_combinator_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = _write_package(root / "old", version="0.1.0")
            new = _write_package(root / "new", version="0.2.0")
            _write_complex_schema(old, include_link_payload=True, add_confidence_constraint=False)
            _write_complex_schema(new, include_link_payload=False, add_confidence_constraint=True)

            code, output = _run_cli(["diff", str(old), str(new)])

            self.assertEqual(code, 0)
            self.assertIn("SCHEMA output enum removed values classification: 'dataset'", output)
            self.assertIn("SCHEMA output oneOf branch removed payload.oneOf[1]", output)
            self.assertIn("SCHEMA output allOf branch added metadata.allOf[1]", output)
            self.assertIn("RISK breaking-change", output)

    def test_diff_reports_complex_schema_ref_target_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = _write_package(root / "old", version="0.1.0")
            new = _write_package(root / "new", version="0.2.0")
            _write_ref_target_schema(old, ref="#/definitions/reviewer")
            _write_ref_target_schema(new, ref="#/$defs/reviewer")

            code, output = _run_cli(["diff", str(old), str(new)])

            self.assertEqual(code, 0)
            self.assertIn("SCHEMA output $ref target changed reviewer: #/definitions/reviewer -> #/$defs/reviewer", output)
            self.assertIn("SCHEMA output enum added reviewer: 'lead', 'peer'", output)
            self.assertIn("RISK breaking-change", output)

    def test_diff_reports_anyof_branch_removal_as_breaking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = _write_package(root / "old", version="0.1.0")
            new = _write_package(root / "new", version="0.2.0")
            _write_anyof_schema(old, include_number=True)
            _write_anyof_schema(new, include_number=False)

            code, output = _run_cli(["diff", str(old), str(new)])

            self.assertEqual(code, 0)
            self.assertIn("SCHEMA output anyOf branch removed choice.anyOf[1]", output)
            self.assertIn("RISK breaking-change", output)

    def test_diff_supports_json_and_markdown_formats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = _write_package(root / "old", version="0.1.0")
            new = _write_package(root / "new", version="0.2.0", extra_required={"confidence": {"type": "string"}})

            text_code, text_output = _run_cli(["diff", str(old), str(new)])
            plain_code, plain_output = _run_cli(["diff", str(old), str(new), "--format", "plain"])
            json_code, json_output = _run_cli(["diff", str(old), str(new), "--format", "json"])
            markdown_code, markdown_output = _run_cli(["diff", str(old), str(new), "--format", "markdown"])

            self.assertEqual(text_code, 0)
            self.assertIn("Skill Diff", text_output)
            self.assertIn("[schema]", text_output)
            self.assertEqual(plain_code, 0)
            self.assertTrue(plain_output.startswith("PACKAGE sample-skill"))
            self.assertNotIn("Skill Diff", plain_output)
            self.assertEqual(json_code, 0)
            payload = json.loads(json_output)
            self.assertEqual(payload["schema_version"], "sit.diff.v1")
            self.assertEqual(payload["old"]["source"], str(old))
            self.assertEqual(payload["new"]["source"], str(new))
            self.assertEqual(payload["risk"], "breaking-change")
            self.assertEqual(payload["suggested_bump"], "major")
            self.assertTrue(any(event["breaking"] for event in payload["events"]))
            self.assertEqual(markdown_code, 0)
            self.assertIn("## Skill Diff", markdown_output)
            self.assertIn("Risk: `breaking-change`", markdown_output)

    def test_diff_prompt_flag_outputs_text_diff_details(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = _write_package(root / "old", version="0.1.0", prompt="# Task\nExtract {text}.\n")
            new = _write_package(
                root / "new",
                version="0.1.0",
                prompt="# Task\nExtract {text}.\n## Output\nCite {evidence}.\n",
            )

            default_code, default_output = _run_cli(["diff", str(old), str(new)])
            prompt_code, prompt_output = _run_cli(["diff", str(old), str(new), "--prompt"])
            json_code, json_output = _run_cli(["diff", str(old), str(new), "--prompt", "--format", "json"])
            summary_code, summary_output = _run_cli(["pr-summary", str(old), str(new)])

            self.assertEqual(default_code, 0)
            self.assertIn("PROMPT changed extraction", default_output)
            self.assertIn("+2 -0", default_output)
            self.assertIn("headings: Task, Output", default_output)
            self.assertIn("vars: text, evidence", default_output)
            self.assertNotIn("Prompt/Reference Unified Diff", default_output)

            self.assertEqual(prompt_code, 0)
            self.assertIn("Prompt/Reference Unified Diff", prompt_output)
            self.assertIn("+## Output", prompt_output)
            self.assertIn("+Cite {evidence}.", prompt_output)

            self.assertEqual(json_code, 0)
            payload = json.loads(json_output)
            self.assertEqual(payload["text_diffs"][0]["added_lines"], 2)
            self.assertIn("+## Output", payload["text_diffs"][0]["lines"])

            self.assertEqual(summary_code, 0)
            self.assertIn("### Prompt/Reference Text Summary", summary_output)
            self.assertIn("PROMPT summary extraction", summary_output)

    def test_git_range_can_compare_head_to_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "worktree-skill", version="0.1.0")
            _git(package, "init")
            _git(package, "config", "user.email", "sit@example.test")
            _git(package, "config", "user.name", "SIT Test")
            _git(package, "add", ".")
            _git(package, "commit", "-m", "feat: initial skill")
            (package / "prompts" / "extraction.md").write_text(
                "Extract the answer and cite evidence.",
                encoding="utf-8",
            )

            diff_code, diff_output = _run_cli_in(package, ["diff", "HEAD..WORKTREE"])
            report_code, report_output = _run_cli_in(package, ["report", ".", "--compare", "HEAD..WORKTREE"])

            self.assertEqual(diff_code, 0)
            self.assertIn("PROMPT changed extraction", diff_output)
            self.assertIn("Baseline: sample-skill@0.1.0 (HEAD)", diff_output)
            self.assertIn("Current: sample-skill@0.1.0 (WORKTREE)", diff_output)
            self.assertEqual(report_code, 0)
            self.assertIn("PROMPT changed extraction", report_output)

    def test_report_writes_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = _write_package(root / "pkg", version="0.1.0")
            output_path = root / "report.md"

            code, output = _run_cli(["report", str(package), "--output", str(output_path)])

            self.assertEqual(code, 0)
            self.assertIn("Wrote report:", output)
            report = output_path.read_text(encoding="utf-8")
            self.assertIn("# sample-skill 0.1.0 SIT Report", report)
            self.assertIn("## Golden Tests", report)
            self.assertIn("SUMMARY 1/1 golden cases passed", report)

    def test_test_command_supports_json_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "pkg", version="0.1.0")

            code, output = _run_cli(["test", str(package), "--format", "json"])

            self.assertEqual(code, 0)
            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "sit.test.v1")
            self.assertEqual(payload["package"]["name"], "sample-skill")
            self.assertEqual(payload["validation"]["status"], "pass")
            self.assertEqual(payload["golden_tests"]["status"], "pass")
            self.assertEqual(payload["golden_tests"]["passed"], 1)
            self.assertEqual(payload["golden_tests"]["total"], 1)

    def test_report_supports_json_format_with_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = _write_package(root / "old", version="0.1.0")
            new = _write_package(root / "new", version="0.2.0", extra_required={"confidence": {"type": "string"}})

            code, output = _run_cli(["report", str(new), "--compare", str(old), "--format", "json"])

            self.assertEqual(code, 0)
            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "sit.report.v1")
            self.assertEqual(payload["package"]["version"], "0.2.0")
            self.assertEqual(payload["validation"]["status"], "pass")
            self.assertEqual(payload["golden_tests"]["status"], "pass")
            self.assertEqual(payload["diff"]["risk"], "breaking-change")
            self.assertEqual(payload["diff"]["suggested_bump"], "major")
            self.assertIn("python3 -m sit.cli diff", payload["reproducibility"]["diff"])

    def test_report_supports_html_format_with_visual_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = _write_package(root / "old", version="0.1.0")
            new = _write_package(root / "new", version="0.2.0", extra_required={"confidence": {"type": "string"}})
            output_path = root / "visual.html"

            code, output = _run_cli(
                ["report", str(new), "--compare", str(old), "--format", "html", "--output", str(output_path)]
            )

            self.assertEqual(code, 0)
            self.assertIn("Wrote report:", output)
            html = output_path.read_text(encoding="utf-8")
            self.assertIn("<!doctype html>", html)
            self.assertIn("SitHub semantic control report", html)
            self.assertIn("Diff risk", html)
            self.assertIn("breaking-change", html)
            self.assertIn("Suggested bump", html)
            self.assertIn("SCHEMA output property added confidence", html)
            self.assertIn('data-filter="breaking"', html)
            self.assertIn("Expand long diff", html)
            self.assertIn('class="schema-path"', html)
            self.assertIn("document.querySelectorAll", html)

    def test_ci_summary_outputs_markdown_with_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = _write_package(root / "old", version="0.1.0")
            new = _write_package(root / "new", version="0.2.0", extra_required={"confidence": {"type": "string"}})

            code, output = _run_cli(["ci-summary", str(new), "--compare", str(old)])

            self.assertEqual(code, 0)
            self.assertIn("## SitHub CI Summary", output)
            self.assertIn("Package: `sample-skill@0.2.0`", output)
            self.assertIn("Diff risk: **breaking-change**", output)
            self.assertIn("Suggested version bump: `major`", output)
            self.assertIn("### Semantic Diff", output)

    def test_ci_summary_can_write_and_append_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = _write_package(root / "pkg", version="0.1.0")
            output_path = root / "summary.md"

            first_code, first_output = _run_cli(["ci-summary", str(package), "--output", str(output_path)])
            second_code, second_output = _run_cli(["ci-summary", str(package), "--output", str(output_path), "--append"])

            self.assertEqual(first_code, 0)
            self.assertEqual(second_code, 0)
            self.assertIn("Wrote CI summary:", first_output)
            self.assertIn("Wrote CI summary:", second_output)
            content = output_path.read_text(encoding="utf-8")
            self.assertEqual(content.count("## SitHub CI Summary"), 2)

    def test_ci_summary_supports_refs_package_dir_and_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            package = _write_package(repo / "skills" / "sample", version="0.1.0")
            _git(repo, "init")
            _git(repo, "config", "user.email", "sit@example.test")
            _git(repo, "config", "user.name", "SIT Test")
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "feat: initial skill")

            _upgrade_package_to_v2(package)
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "feat: require confidence")

            code, output = _run_cli_in(
                repo,
                [
                    "ci-summary",
                    "--package-dir",
                    "skills/sample",
                    "--baseline-ref",
                    "HEAD~1",
                    "--head-ref",
                    "HEAD",
                    "--artifact-dir",
                    "reports/ci",
                ],
            )

            self.assertEqual(code, 0)
            self.assertIn("Package: `sample-skill@0.2.0`", output)
            self.assertIn("Diff risk: **breaking-change**", output)
            artifact_dir = repo / "reports" / "ci"
            self.assertTrue((artifact_dir / "sit-summary.md").exists())
            self.assertTrue((artifact_dir / "sit-report.json").exists())
            self.assertTrue((artifact_dir / "sit-report.md").exists())
            self.assertTrue((artifact_dir / "sit-report.html").exists())
            payload = json.loads((artifact_dir / "sit-report.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["diff"]["risk"], "breaking-change")
            self.assertEqual(payload["package"]["name"], "sample-skill")
            self.assertEqual(payload["package"]["version"], "0.2.0")

    def test_init_creates_valid_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "new-skill"

            code, output = _run_cli(["init", "new-skill", "--path", str(package), "--no-git"])
            validate_code, validate_output = _run_cli(["validate", str(package)])
            test_code, test_output = _run_cli(["test", str(package)])

            self.assertEqual(code, 0)
            self.assertIn("Initialized Skill Package:", output)
            self.assertTrue((package / "skill.yaml").exists())
            self.assertTrue((package / ".github" / "workflows" / "sit-ci.yaml").exists())
            workflow = (package / ".github" / "workflows" / "sit-ci.yaml").read_text(encoding="utf-8")
            self.assertIn("python -m pip install git+https://github.com/OpenRaiser/Sit.git", workflow)
            self.assertIn("SIT_PACKAGE_DIR", workflow)
            self.assertIn("--baseline-ref \"$SIT_BASELINE_REF\"", workflow)
            self.assertIn("--artifact-dir \"$SIT_ARTIFACT_DIR\"", workflow)
            self.assertIn("actions/checkout@v5", workflow)
            self.assertIn("actions/upload-artifact@v6", workflow)
            self.assertIn("GITHUB_STEP_SUMMARY", workflow)
            self.assertEqual(validate_code, 0)
            self.assertIn("OK  schema.output JSON schema valid", validate_output)
            self.assertEqual(test_code, 0)
            self.assertIn("SUMMARY 1/1 golden cases passed", test_output)

    def test_pr_summary_outputs_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = _write_package(root / "old", version="0.1.0")
            new = _write_package(
                root / "new",
                version="0.2.0",
                prompt="Extract the answer and cite evidence.",
                extra_required={"confidence": {"type": "string"}},
            )

            code, output = _run_cli(["pr-summary", str(old), str(new)])

            self.assertEqual(code, 0)
            self.assertIn("## Skill Change Summary", output)
            self.assertIn("Suggested version bump: `major`", output)
            self.assertIn("SCHEMA output property added confidence (required)", output)

    def test_pr_summary_supports_json_and_text_formats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = _write_package(root / "old", version="0.1.0")
            new = _write_package(root / "new", version="0.2.0", extra_required={"confidence": {"type": "string"}})

            json_code, json_output = _run_cli(["pr-summary", str(old), str(new), "--format", "json"])
            text_code, text_output = _run_cli(["pr-summary", str(old), str(new), "--format", "text"])

            self.assertEqual(json_code, 0)
            payload = json.loads(json_output)
            self.assertEqual(payload["schema_version"], "sit.pr_summary.v1")
            self.assertEqual(payload["baseline"]["source"], str(old))
            self.assertEqual(payload["current"]["source"], str(new))
            self.assertEqual(payload["validation"]["status"], "pass")
            self.assertEqual(payload["golden_tests"]["status"], "pass")
            self.assertEqual(payload["risk"], "breaking-change")
            self.assertEqual(payload["diff"]["suggested_bump"], "major")
            self.assertEqual(text_code, 0)
            self.assertIn("Suggested version bump: major", text_output)
            self.assertIn("Semantic Diff:", text_output)

    def test_review_outputs_pr_ready_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = _write_package(root / "old", version="0.1.0")
            new = _write_package(
                root / "new",
                version="0.2.0",
                prompt="Extract the answer and cite evidence.",
                extra_required={"confidence": {"type": "string"}},
            )

            code, output = _run_cli(["review", str(old), str(new)])

            self.assertEqual(code, 0)
            self.assertIn("## SitHub Skill Review", output)
            self.assertIn("Status: **needs-maintainer-review**", output)
            self.assertIn("Recommendation: **Require maintainer approval and a major version bump before merge.**", output)
            self.assertIn("| Diff risk | **breaking-change** |", output)
            self.assertIn("| Suggested bump | `major` |", output)
            self.assertIn("- `prompt`: 1 event(s)", output)
            self.assertIn("- `schema`: 2 event(s)", output)
            self.assertNotIn("- `package`:", output)
            self.assertNotIn("- `risk`:", output)
            self.assertIn("SCHEMA output property added confidence (required)", output)
            self.assertNotIn("PACKAGE sample-skill", output)
            self.assertNotIn("RISK breaking-change", output)
            self.assertIn("sit validate", output)
            self.assertIn("sit test", output)
            self.assertIn("sit diff", output)

    def test_review_supports_json_and_output_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = _write_package(root / "old", version="0.1.0")
            new = _write_package(root / "new", version="0.2.0", extra_required={"confidence": {"type": "string"}})
            output_path = root / "review.md"

            json_code, json_output = _run_cli(["review", str(old), str(new), "--format", "json"])
            file_code, file_output = _run_cli(["review", str(old), str(new), "--output", str(output_path)])

            self.assertEqual(json_code, 0)
            payload = json.loads(json_output)
            self.assertEqual(payload["schema_version"], "sit.review.v1")
            self.assertEqual(payload["review"]["status"], "needs-maintainer-review")
            self.assertGreaterEqual(payload["artifact_summary"]["total_events"], 1)
            categories = {item["category"]: item["count"] for item in payload["artifact_summary"]["categories"]}
            self.assertEqual(categories["schema"], 2)
            self.assertNotIn("package", categories)
            self.assertNotIn("risk", categories)
            self.assertEqual(file_code, 0)
            self.assertIn("Wrote Skill review:", file_output)
            review = output_path.read_text(encoding="utf-8")
            self.assertIn("## SitHub Skill Review", review)
            self.assertIn("Status: **needs-maintainer-review**", review)

    def test_manifest_status_validates_and_diff_reports_lifecycle_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            active = _write_package(root / "active", version="0.1.0")
            deprecated = _write_package(root / "deprecated", version="0.1.0")
            retired = _write_package(root / "retired", version="0.1.0")
            invalid = _write_package(root / "invalid", version="0.1.0")
            _set_manifest_status(deprecated, "deprecated")
            _set_manifest_status(retired, "retired")
            _set_manifest_status(invalid, "paused")

            validate_code, validate_output = _run_cli(["validate", str(active)])
            invalid_code, invalid_output = _run_cli(["validate", str(invalid)])
            deprecated_code, deprecated_output = _run_cli(["diff", str(active), str(deprecated), "--format", "json"])
            retired_code, retired_output = _run_cli(["diff", str(deprecated), str(retired), "--format", "json"])

            self.assertEqual(validate_code, 0)
            self.assertIn("OK  status: active", validate_output)
            self.assertEqual(invalid_code, 1)
            self.assertIn("skill.yaml field 'status' must be one of", invalid_output)

            self.assertEqual(deprecated_code, 0)
            deprecated_payload = json.loads(deprecated_output)
            self.assertEqual(deprecated_payload["risk"], "review-required")
            self.assertEqual(deprecated_payload["suggested_bump"], "minor")
            self.assertTrue(
                any(
                    event["category"] == "status"
                    and event["message"] == "STATUS changed active -> deprecated"
                    and not event["breaking"]
                    for event in deprecated_payload["events"]
                )
            )

            self.assertEqual(retired_code, 0)
            retired_payload = json.loads(retired_output)
            self.assertEqual(retired_payload["risk"], "breaking-change")
            self.assertEqual(retired_payload["suggested_bump"], "major")
            self.assertTrue(
                any(
                    event["category"] == "status"
                    and event["message"] == "STATUS changed deprecated -> retired"
                    and event["breaking"]
                    for event in retired_payload["events"]
                )
            )

    def test_info_outputs_json_snapshot_without_git_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "pkg", version="0.1.0")

            code, output = _run_cli(["info", str(package), "--format", "json"])

            self.assertEqual(code, 0)
            payload = json.loads(output)
            self.assertEqual(payload["schema_version"], "sit.info.v1")
            self.assertEqual(payload["package"]["name"], "sample-skill")
            self.assertEqual(payload["package"]["version"], "0.1.0")
            self.assertFalse(payload["git"]["available"])
            self.assertEqual(payload["validation"]["status"], "pass")
            self.assertEqual(payload["golden_tests"]["status"], "pass")
            self.assertEqual(payload["golden_tests"]["passed"], 1)
            self.assertEqual(payload["golden_tests"]["total"], 1)
            self.assertTrue(payload["files"]["prompts"]["extraction"]["exists"])
            self.assertTrue(payload["reports"]["exists"])

    def test_info_text_includes_git_and_dirty_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "git-info-skill", version="0.1.0")
            _git(package, "init")
            _git(package, "config", "user.email", "sit@example.test")
            _git(package, "config", "user.name", "SIT Test")
            _git(package, "add", ".")
            _git(package, "commit", "-m", "feat: initial skill")
            (package / "prompts" / "extraction.md").write_text("Extract the final answer.", encoding="utf-8")

            code, output = _run_cli_in(package, ["info"])

            self.assertEqual(code, 0)
            self.assertIn("Skill Package Info", output)
            self.assertIn("Package: sample-skill", output)
            self.assertIn("Git:", output)
            self.assertIn("Dirty: yes", output)
            self.assertIn("Validation: pass", output)
            self.assertIn("Golden summary: SUMMARY 1/1 golden cases passed", output)

    def test_doctor_reports_onboarding_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "doctor-skill", version="0.1.0")
            (package / ".github" / "workflows").mkdir(parents=True)
            (package / ".github" / "workflows" / "sit-ci.yaml").write_text(
                "name: Skill CI\nsteps:\n  - run: sit validate\n  - run: sit test\n  - run: sit ci-summary\n",
                encoding="utf-8",
            )
            (package / "reports" / "onboarding.md").write_text("# report\n", encoding="utf-8")
            _git(package, "init")
            _git(package, "config", "user.email", "sit@example.test")
            _git(package, "config", "user.name", "SIT Test")
            _git(package, "remote", "add", "origin", "https://github.com/example/doctor-skill.git")
            _git(package, "add", ".")
            _git(package, "commit", "-m", "feat: initial skill")

            text_code, text_output = _run_cli_in(package, ["doctor"])
            json_code, json_output = _run_cli_in(package, ["doctor", "--format", "json"])

            self.assertEqual(text_code, 0)
            self.assertIn("SitHub Doctor", text_output)
            self.assertIn("OK git: Git repository detected", text_output)
            self.assertIn("OK github_remote: GitHub remote found", text_output)
            self.assertIn("WARN git_hooks: sit pre-commit hook is not installed", text_output)
            self.assertIn("sit install-hooks .", text_output)
            self.assertIn("OK github_actions: SitHub GitHub Actions workflow found", text_output)
            self.assertEqual(json_code, 0)
            payload = json.loads(json_output)
            self.assertEqual(payload["schema_version"], "sit.doctor.v1")
            self.assertEqual(payload["status"], "warn")
            self.assertEqual(payload["package"]["name"], "sample-skill")
            hook_check = next(check for check in payload["checks"] if check["name"] == "git_hooks")
            self.assertEqual(hook_check["status"], "warn")

    def test_doctor_reports_installed_sit_hook(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "doctor-hook-skill", version="0.1.0")
            _git(package, "init")
            _git(package, "config", "user.email", "sit@example.test")
            _git(package, "config", "user.name", "SIT Test")
            _git(package, "add", ".")
            _git(package, "commit", "-m", "feat: initial skill")
            install_code, install_output = _run_cli_in(package, ["install-hooks", "."])

            code, output = _run_cli_in(package, ["doctor", "--format", "json"])

            self.assertEqual(install_code, 0, install_output)
            self.assertEqual(code, 0, output)
            payload = json.loads(output)
            hook_check = next(check for check in payload["checks"] if check["name"] == "git_hooks")
            self.assertEqual(hook_check["status"], "pass")
            self.assertEqual(hook_check["message"], "sit pre-commit hook installed")

    def test_doctor_fails_without_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            code, output = _run_cli(["doctor", str(root)])

            self.assertEqual(code, 1)
            self.assertIn("ERR manifest:", output)
            self.assertIn("Missing skill.yaml", output)

    def test_deps_check_reports_local_version_and_schema_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_package(root / "upstream-old", version="0.1.0")
            upstream_new = _write_package(root / "upstream-new", version="0.1.0")
            _upgrade_package_to_v2(upstream_new)
            downstream = _write_package(root / "downstream", version="0.1.0")
            _write_deps_yaml(
                downstream,
                """
dependencies:
  - name: upstream
    path: ../upstream-new
    version: ">=0.1.0,<1.0.0"
    baseline_path: ../upstream-old
""",
            )

            code, output = _run_cli(["deps", "check", str(downstream)])
            json_code, json_output = _run_cli(["deps", "check", str(downstream), "--format", "json"])

            self.assertEqual(code, 0)
            self.assertIn("Status: warn", output)
            self.assertIn("schema risk: breaking-change", output)
            self.assertIn("WARN upstream schema diff is breaking-change", output)
            self.assertEqual(json_code, 0)
            payload = json.loads(json_output)
            self.assertEqual(payload["schema_version"], "sit.deps.v1")
            self.assertEqual(payload["status"], "warn")
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["dependencies"][0]["risk"], "breaking-change")

    def test_deps_check_fails_for_version_range_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            upstream = _write_package(root / "upstream", version="0.2.0")
            downstream = _write_package(root / "downstream", version="0.1.0")
            _write_deps_yaml(
                downstream,
                """
dependencies:
  - name: upstream
    path: ../upstream
    version: "<0.2.0"
""",
            )

            code, output = _run_cli(["deps", "check", str(downstream)])

            self.assertEqual(code, 1)
            self.assertIn("Status: fail", output)
            self.assertIn("ERR version 0.2.0 does not satisfy <0.2.0", output)
            self.assertTrue(upstream.exists())

    def test_deps_check_warns_for_deprecated_or_retired_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            upstream = _write_package(root / "upstream", version="0.1.0")
            _set_manifest_status(upstream, "retired")
            downstream = _write_package(root / "downstream", version="0.1.0")
            _write_deps_yaml(
                downstream,
                """
dependencies:
  - name: upstream
    path: ../upstream
    version: ">=0.1.0"
""",
            )

            code, output = _run_cli(["deps", "check", str(downstream)])
            json_code, json_output = _run_cli(["deps", "check", str(downstream), "--format", "json"])

            self.assertEqual(code, 0)
            self.assertIn("Status: warn", output)
            self.assertIn("status: retired", output)
            self.assertIn("WARN upstream status is retired", output)
            self.assertEqual(json_code, 0)
            payload = json.loads(json_output)
            self.assertEqual(payload["dependencies"][0]["status"], "retired")
            self.assertIn("upstream status is retired", payload["warnings"])

    def test_commit_prints_dependency_warning_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            upstream_old = _write_package(root / "upstream-old", version="0.1.0")
            upstream_new = _write_package(root / "upstream-new", version="0.1.0")
            _upgrade_package_to_v2(upstream_new)
            downstream = _write_package(root / "downstream", version="0.1.0")
            _write_deps_yaml(
                downstream,
                """
dependencies:
  - name: upstream
    path: ../upstream-new
    version: ">=0.1.0,<1.0.0"
    baseline_path: ../upstream-old
""",
            )
            _git(downstream, "init")
            _git(downstream, "config", "user.email", "sit@example.test")
            _git(downstream, "config", "user.name", "SIT Test")
            _git(downstream, "add", ".")
            _git(downstream, "commit", "-m", "feat: initial skill")
            (downstream / "prompts" / "extraction.md").write_text("Extract the answer carefully.", encoding="utf-8")
            _git(downstream, "add", ".")

            code, stdout, stderr = _run_cli_capture(["commit", "-m", "chore: update prompt", "--no-version-gate"], cwd=downstream)

            self.assertEqual(code, 0)
            self.assertIn("WARN dependency warning: upstream schema diff is breaking-change", stdout)
            self.assertEqual(stderr, "")
            self.assertTrue(upstream_old.exists())

    def test_onboard_existing_skill_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "paper-webpage-builder"
            (root / "references").mkdir(parents=True)
            (root / "SKILL.md").write_text("# Paper Webpage Builder\n\nBuild a polished paper webpage.\n", encoding="utf-8")
            (root / "references" / "design_principles.md").write_text("Design guidance.\n", encoding="utf-8")

            code, output = _run_cli(
                [
                    "onboard",
                    str(root),
                    "--remote",
                    "https://github.com/example/paper-webpage-builder.git",
                ]
            )
            validate_code, validate_output = _run_cli(["validate", str(root)])
            test_code, test_output = _run_cli(["test", str(root)])
            doctor_code, doctor_output = _run_cli(["doctor", str(root)])

            self.assertEqual(code, 0)
            self.assertIn("SitHub Onboard", output)
            self.assertIn("Doctor status: warn", output)
            self.assertTrue((root / "skill.yaml").exists())
            self.assertTrue((root / "schemas" / "input.schema.json").exists())
            self.assertTrue((root / "schemas" / "output.schema.json").exists())
            self.assertTrue((root / "tests" / "golden.jsonl").exists())
            self.assertTrue((root / ".github" / "workflows" / "sit-ci.yaml").exists())
            self.assertTrue((root / "reports" / "sithub-onboarding.md").exists())
            self.assertTrue((root / "reports" / "sithub-onboarding.html").exists())
            manifest = (root / "skill.yaml").read_text(encoding="utf-8")
            self.assertIn("name: paper-webpage-builder", manifest)
            self.assertIn("skill: SKILL.md", manifest)
            self.assertIn("design_principles: references/design_principles.md", manifest)
            self.assertEqual(validate_code, 0)
            self.assertIn("OK  schema.output JSON schema valid", validate_output)
            self.assertEqual(test_code, 0)
            self.assertIn("SUMMARY 1/1 golden cases passed", test_output)
            self.assertEqual(doctor_code, 0)
            self.assertIn("OK github_remote: GitHub remote found", doctor_output)

    def test_onboard_agent_generates_codex_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _write_package(Path(tmp) / "agent-skill", version="0.1.0")

            code, output = _run_cli(["onboard", "--agent", str(root)])
            rerun_code, rerun_output = _run_cli(["onboard", "--agent", str(root)])
            json_code, json_output = _run_cli(["onboard", "--agent", "--force", "--format", "json", str(root)])

            self.assertEqual(code, 0)
            self.assertIn("Codex will read AGENTS.md", output)
            self.assertTrue((root / ".mcp.json").exists())
            agents_md = (root / "AGENTS.md").read_text(encoding="utf-8")
            self.assertIn("## Codex workflow", agents_md)
            self.assertIn("git status --short", agents_md)
            self.assertIn("sit diff HEAD..WORKTREE", agents_md)
            self.assertIn("use `sit commit` instead of `git commit`", agents_md)
            self.assertEqual(rerun_code, 0)
            self.assertIn("Skipped (already exists):", rerun_output)
            self.assertEqual(json_code, 0)
            payload = json.loads(json_output)
            self.assertEqual(payload["schema_version"], "sit.agent_setup.v1")
            self.assertIn("AGENTS.md", payload["updated"])

    def test_install_hooks_writes_pre_commit_hook(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "hook-skill", version="0.1.0")
            _git(package, "init")

            code, output = _run_cli(["install-hooks", str(package)])

            self.assertEqual(code, 0)
            self.assertIn("Installed sit pre-commit hook", output)
            hook = package / ".git" / "hooks" / "pre-commit"
            self.assertTrue(hook.exists())
            hook_text = hook.read_text(encoding="utf-8")
            self.assertIn("Generated by sit install-hooks", hook_text)
            self.assertIn("sit _hook-pre-commit", hook_text)

    def test_hook_pre_commit_blocks_unbumped_semantic_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "hook-block-skill", version="0.1.0")
            _git(package, "init")
            _git(package, "config", "user.email", "sit@example.test")
            _git(package, "config", "user.name", "SIT Test")
            _git(package, "add", ".")
            _git(package, "commit", "-m", "feat: initial skill")
            (package / "prompts" / "extraction.md").write_text("Extract with care.", encoding="utf-8")
            _git(package, "add", ".")

            code, stdout, stderr = _run_cli_capture(["_hook-pre-commit", "--package", str(package)], cwd=package)

            self.assertEqual(code, 2)
            self.assertIn("commit version gate blocked", stdout)
            self.assertIn("pre-commit blocked", stderr)

    def test_onboard_does_not_overwrite_existing_files_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "existing-skill"
            root.mkdir()
            (root / "SKILL.md").write_text("# Existing Skill\n", encoding="utf-8")
            (root / "skill.yaml").write_text(
                "\n".join(
                    [
                        "name: custom-skill",
                        "version: 2.0.0",
                        "prompts:",
                        "  skill: SKILL.md",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            code, output = _run_cli(["onboard", str(root)])

            self.assertEqual(code, 0)
            self.assertIn("Updated files:", output)
            self.assertIn("skill.yaml", output)
            manifest = (root / "skill.yaml").read_text(encoding="utf-8")
            self.assertIn("version: 2.0.0", manifest)
            self.assertIn("schemas:", manifest)
            self.assertIn("tests:", manifest)

    def test_onboard_fails_without_skill_md(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "not-a-skill"
            root.mkdir()

            code, stdout, stderr = _run_cli_capture(["onboard", str(root)])

            self.assertEqual(code, 2)
            self.assertEqual(stdout, "")
            self.assertIn("Missing SKILL.md", stderr)

    def test_standardize_prompt_only_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "prompt-only"
            root.mkdir()
            (root / "prompt.md").write_text("# Classifier\n\nClassify the input text.\n", encoding="utf-8")

            code, output = _run_cli(["standardize", str(root)])
            rerun_code, rerun_output = _run_cli(["standardize", str(root)])
            validate_code, validate_output = _run_cli(["validate", str(root)])
            test_code, test_output = _run_cli(["test", str(root)])

            self.assertEqual(code, 0)
            self.assertIn("SitHub Standardize", output)
            self.assertEqual(rerun_code, 0)
            self.assertNotIn("prompts/prompt_2.md", rerun_output)
            self.assertTrue((root / "skill.yaml").exists())
            self.assertTrue((root / "prompts" / "prompt.md").exists())
            self.assertFalse((root / "prompts" / "prompt_2.md").exists())
            self.assertTrue((root / "schemas" / "input.schema.json").exists())
            self.assertTrue((root / "schemas" / "output.schema.json").exists())
            self.assertTrue((root / "tests" / "golden.jsonl").exists())
            self.assertTrue((root / "reports" / "sithub-standardization.md").exists())
            manifest = (root / "skill.yaml").read_text(encoding="utf-8")
            self.assertIn("name: prompt-only", manifest)
            self.assertIn("prompt: prompts/prompt.md", manifest)
            self.assertEqual(validate_code, 0)
            self.assertIn("OK  schema.output JSON schema valid", validate_output)
            self.assertEqual(test_code, 0)
            self.assertIn("SUMMARY 1/1 golden cases passed", test_output)

            json_root = Path(tmp) / "prompt-json"
            json_root.mkdir()
            (json_root / "prompt.txt").write_text("Return a compact JSON summary.\n", encoding="utf-8")
            json_code, json_output = _run_cli(["standardize", str(json_root), "--format", "json"])
            self.assertEqual(json_code, 0)
            json_payload = json.loads(json_output)
            self.assertEqual(json_payload["schema_version"], "sit.standardize.v1")
            self.assertEqual(json_payload["root"], str(json_root.resolve()))

            no_git_root = Path(tmp) / "prompt-no-git"
            no_git_root.mkdir()
            (no_git_root / "prompt.md").write_text("Return JSON.\n", encoding="utf-8")
            no_git_code, no_git_output = _run_cli(["standardize", str(no_git_root), "--no-git"])
            self.assertEqual(no_git_code, 0)
            self.assertIn("Doctor status: fail", no_git_output)
            self.assertTrue((no_git_root / "skill.yaml").exists())

    def test_standardize_skill_md_copies_prompt_into_prompts_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "existing-skill"
            root.mkdir()
            (root / "SKILL.md").write_text("# Existing Skill\n\nFollow the existing instructions.\n", encoding="utf-8")

            code, output = _run_cli(["standardize", str(root)])
            json_code, json_output = _run_cli(["standardize", str(root), "--format", "json"])

            self.assertEqual(code, 0)
            self.assertIn("prompts/skill.md", output)
            self.assertFalse((root / "prompts" / "skill_2.md").exists())
            self.assertTrue((root / "prompts" / "skill.md").exists())
            manifest = (root / "skill.yaml").read_text(encoding="utf-8")
            self.assertIn("skill: prompts/skill.md", manifest)
            self.assertEqual(json_code, 0)
            payload = json.loads(json_output)
            self.assertEqual(payload["schema_version"], "sit.standardize.v1")
            self.assertEqual(payload["root"], str(root.resolve()))

    def test_golden_match_modes_pass_and_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "pkg", version="0.1.0")
            records = [
                {"case_id": "schema", "input": {}, "expected": {"answer": "ok"}, "match_mode": "schema_only"},
                {"case_id": "exact", "input": {}, "expected": {"answer": "ok"}, "actual": {"answer": "ok"}, "match_mode": "exact"},
                {"case_id": "partial", "input": {}, "expected": {"answer": "ok"}, "actual": {"answer": "ok"}, "match_mode": "partial"},
                {
                    "case_id": "contains",
                    "input": {},
                    "expected": {"answer": "ok"},
                    "actual": {"answer": "ok with context"},
                    "match_mode": "contains",
                },
                {
                    "case_id": "contains-fail",
                    "input": {},
                    "expected": {"answer": "missing"},
                    "actual": {"answer": "ok with context"},
                    "match_mode": "contains",
                },
            ]
            (package / "tests" / "golden.jsonl").write_text(
                "\n".join(json.dumps(record) for record in records) + "\n",
                encoding="utf-8",
            )

            code, output = _run_cli(["test", str(package)])

            self.assertEqual(code, 1)
            self.assertIn("schema: schema_only match passed", output)
            self.assertIn("exact: exact match passed", output)
            self.assertIn("partial: partial match passed", output)
            self.assertIn("contains: contains match passed", output)
            self.assertIn("contains-fail: contains mismatch at answer", output)
            self.assertIn("SUMMARY 4/5 golden cases passed", output)

    def test_test_run_uses_manifest_runner_to_generate_actual(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "runner-skill", version="0.1.0")
            _write_runner_script(package, answer_expr="payload.get('text', '')")
            _append_runner_command(package)
            record = {
                "case_id": "runner-exact",
                "input": {"text": "hello"},
                "expected": {"answer": "hello"},
                "match_mode": "exact",
            }
            (package / "tests" / "golden.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")

            code, output = _run_cli(["test", str(package), "--run"])
            json_code, json_output = _run_cli(["test", str(package), "--run", "--format", "json"])

            self.assertEqual(code, 0)
            self.assertIn("runner-exact: runner produced actual", output)
            self.assertIn("runner-exact: exact match passed", output)
            self.assertIn("SUMMARY 1/1 golden cases passed", output)
            self.assertEqual(json_code, 0)
            payload = json.loads(json_output)
            self.assertEqual(payload["execution"]["mode"], "runner")
            self.assertIn("scripts/run_case.py", payload["execution"]["runner"])
            self.assertEqual(payload["golden_tests"]["passed"], 1)

    def test_test_run_can_use_cli_runner_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "runner-override-skill", version="0.1.0")
            _write_runner_script(package, answer_expr="'override'")
            record = {
                "case_id": "runner-override",
                "input": {"text": "hello"},
                "expected": {"answer": "override"},
                "match_mode": "partial",
            }
            (package / "tests" / "golden.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
            command = f"{sys.executable} scripts/run_case.py --input {{input}} --output {{output}}"

            code, output = _run_cli(["test", str(package), "--run", "--runner", command])

            self.assertEqual(code, 0)
            self.assertIn("runner-override: runner produced actual", output)
            self.assertIn("runner-override: partial match passed", output)

    def test_test_run_reports_runner_regression(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "runner-regression-skill", version="0.1.0")
            _write_runner_script(package, answer_expr="'wrong'")
            _append_runner_command(package)
            record = {
                "case_id": "runner-fail",
                "input": {"text": "hello"},
                "expected": {"answer": "hello"},
                "match_mode": "exact",
            }
            (package / "tests" / "golden.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")

            code, output = _run_cli(["test", str(package), "--run"])

            self.assertEqual(code, 1)
            self.assertIn("runner-fail: runner produced actual", output)
            self.assertIn("runner-fail: exact mismatch", output)
            self.assertIn("SUMMARY 0/1 golden cases passed", output)

    def test_test_run_requires_runner_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "missing-runner-skill", version="0.1.0")

            code, stdout, stderr = _run_cli_capture(["test", str(package), "--run"])

            self.assertEqual(code, 2)
            self.assertEqual(stdout, "")
            self.assertIn("No test runner configured", stderr)

    def test_test_run_does_not_shell_execute_case_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = _write_package(root / "runner-injection-skill", version="0.1.0")
            marker = root / "runner-injection-marker"
            _write_case_id_runner_script(package)
            command = f"{sys.executable} scripts/run_case.py --input {{input}} --output {{output}} --case-id {{case_id}}"
            _append_runner_command(package, command=command)
            record = {
                "case_id": f"case; touch {marker}",
                "input": {"text": "hello"},
                "expected": {"answer": "ok"},
                "match_mode": "exact",
            }
            (package / "tests" / "golden.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")

            code, output = _run_cli(["test", str(package), "--run"])

            self.assertEqual(code, 0)
            self.assertIn("exact match passed", output)
            self.assertFalse(marker.exists())

    def test_git_range_diff_and_pr_summary_use_committed_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "git-range-skill", version="0.1.0")
            _git(package, "init")
            _git(package, "config", "user.email", "sit@example.test")
            _git(package, "config", "user.name", "SIT Test")
            _git(package, "add", ".")
            _git(package, "commit", "-m", "feat: initial skill")

            _upgrade_package_to_v2(package)
            _git(package, "add", ".")
            _git(package, "commit", "-m", "feat: require confidence")

            diff_code, diff_output = _run_cli_in(package, ["diff", "HEAD~1..HEAD"])
            summary_code, summary_output = _run_cli_in(package, ["pr-summary", "HEAD~1..HEAD"])
            json_code, json_output = _run_cli_in(package, ["diff", "HEAD~1..HEAD", "--format", "json"])

            self.assertEqual(diff_code, 0)
            self.assertIn("SCHEMA output property added confidence (required)", diff_output)
            self.assertIn("RISK breaking-change", diff_output)
            self.assertEqual(summary_code, 0)
            self.assertIn("Suggested version bump: `major`", summary_output)
            self.assertIn("sit diff HEAD~1..HEAD", summary_output)
            self.assertEqual(json_code, 0)
            payload = json.loads(json_output)
            self.assertEqual(payload["old"]["source"], "HEAD~1")
            self.assertEqual(payload["new"]["source"], "HEAD")

    def test_git_range_diff_reports_script_resource_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "git-script-range-skill", version="0.1.0")
            _write_resource_files(package, script="print('old')\n", asset="template\n", reference="guide\n")
            _git(package, "init")
            _git(package, "config", "user.email", "sit@example.test")
            _git(package, "config", "user.name", "SIT Test")
            _git(package, "add", ".")
            _git(package, "commit", "-m", "feat: initial skill")

            (package / "scripts" / "scan.py").write_text("print('new')\n", encoding="utf-8")
            _git(package, "add", ".")
            _git(package, "commit", "-m", "fix: update scanner")

            code, output = _run_cli_in(package, ["diff", "HEAD~1..HEAD", "--format", "json"])

            self.assertEqual(code, 0)
            payload = json.loads(output)
            self.assertEqual(payload["risk"], "review-required")
            self.assertEqual(payload["suggested_bump"], "minor")
            self.assertTrue(any(event["category"] == "script" and "scripts/scan.py" in event["message"] for event in payload["events"]))

    def test_git_range_works_from_package_subdirectory_in_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            package = _write_package(repo / "skills" / "sample", version="0.1.0")
            _git(repo, "init")
            _git(repo, "config", "user.email", "sit@example.test")
            _git(repo, "config", "user.name", "SIT Test")
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "feat: initial skill")

            _upgrade_package_to_v2(package)
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "feat: require confidence")

            code, output = _run_cli_in(package, ["diff", "HEAD~1..HEAD"])

            self.assertEqual(code, 0)
            self.assertIn("SCHEMA output property added confidence (required)", output)
            self.assertIn("RISK breaking-change", output)

    def test_report_compare_accepts_git_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "git-report-skill", version="0.1.0")
            _git(package, "init")
            _git(package, "config", "user.email", "sit@example.test")
            _git(package, "config", "user.name", "SIT Test")
            _git(package, "add", ".")
            _git(package, "commit", "-m", "feat: initial skill")

            _upgrade_package_to_v2(package)
            _git(package, "add", ".")
            _git(package, "commit", "-m", "feat: require confidence")

            code, output = _run_cli_in(package, ["report", "--compare", "HEAD~1..HEAD"])
            json_code, json_output = _run_cli_in(package, ["report", "--compare", "HEAD~1..HEAD", "--format", "json"])
            html_code, html_output = _run_cli_in(package, ["report", "--compare", "HEAD~1..HEAD", "--format", "html"])

            self.assertEqual(code, 0)
            self.assertIn("# sample-skill 0.2.0 SIT Report", output)
            self.assertIn("- Source: `HEAD`", output)
            self.assertIn("## Diff", output)
            self.assertIn("SCHEMA output property added confidence (required)", output)
            self.assertIn("python3 -m sit.cli diff HEAD~1..HEAD", output)
            self.assertNotIn("/tmp/sit-ref-", output)
            self.assertEqual(json_code, 0)
            payload = json.loads(json_output)
            self.assertEqual(payload["package"]["source"], "HEAD")
            self.assertEqual(payload["diff"]["old"]["source"], "HEAD~1")
            self.assertEqual(payload["diff"]["new"]["source"], "HEAD")
            self.assertIn("sit-ref-", payload["package"]["root"])
            self.assertEqual(html_code, 0)
            self.assertNotIn("/tmp/sit-ref-", html_output)
            self.assertIn("python3 -m sit.cli diff HEAD~1..HEAD", html_output)

    def test_release_bumps_version_and_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "pkg", version="0.1.0")

            code, output = _run_cli(["release", "minor", str(package), "--no-git-tag"])

            self.assertEqual(code, 0)
            self.assertIn("Released sample-skill@0.2.0", output)
            self.assertIn("git tag: skipped", output)
            manifest = (package / "skill.yaml").read_text(encoding="utf-8")
            self.assertIn("version: 0.2.0", manifest)
            self.assertTrue((package / "reports" / "release-v0.2.0.md").exists())

    def test_release_quiet_suppresses_success_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "quiet-release-skill", version="0.1.0")

            code, stdout, stderr = _run_cli_capture(["--quiet", "release", "patch", str(package), "--no-git-tag"])

            self.assertEqual(code, 0)
            self.assertEqual(stdout, "")
            self.assertEqual(stderr, "")
            self.assertIn("version: 0.1.1", (package / "skill.yaml").read_text(encoding="utf-8"))

    def test_release_bundle_is_reproducible_package_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = _write_package(root / "pkg", version="0.1.0")
            _write_resource_files(package, script="print('scan')\n", asset="<main></main>\n", reference="# Guide\n")

            code, output = _run_cli(["release", "minor", str(package), "--no-git-tag", "--bundle"])

            self.assertEqual(code, 0)
            self.assertIn("bundle:", output)
            bundle_path = package / "dist" / "sample-skill-v0.2.0.tar.gz"
            self.assertTrue(bundle_path.exists())

            extract_dir = root / "extract"
            with tarfile.open(bundle_path, "r:gz") as archive:
                archive.extractall(extract_dir)

            unpacked = extract_dir / "sample-skill-v0.2.0"
            self.assertTrue((unpacked / "skill.yaml").exists())
            self.assertTrue((unpacked / "manifest.json").exists())
            self.assertTrue((unpacked / "reproduce.sh").exists())
            self.assertTrue((unpacked / "scripts" / "scan.py").exists())
            manifest = json.loads((unpacked / "manifest.json").read_text(encoding="utf-8"))
            script_entry = next(item for item in manifest["files"] if item["path"] == "scripts/scan.py")
            script_bytes = (unpacked / "scripts" / "scan.py").read_bytes()
            self.assertEqual(script_entry["sha256"], hashlib.sha256(script_bytes).hexdigest())

            validate_code, validate_output = _run_cli(["validate", str(unpacked)])
            test_code, test_output = _run_cli(["test", str(unpacked)])
            self.assertEqual(validate_code, 0)
            self.assertIn("OK  schema.output JSON schema valid", validate_output)
            self.assertEqual(test_code, 0)
            self.assertIn("SUMMARY 1/1 golden cases passed", test_output)

    def test_release_reports_reverse_dependency_compatibility(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            upstream = _write_package(root / "upstream", version="0.1.0")
            compatible = _write_package(root / "downstream-compatible", version="0.1.0")
            incompatible = _write_package(root / "downstream-incompatible", version="0.1.0")
            _write_deps_yaml(
                compatible,
                """
dependencies:
  - name: upstream
    path: ../upstream
    version: ">=0.2.0,<1.0.0"
""",
            )
            _write_deps_yaml(
                incompatible,
                """
dependencies:
  - name: upstream
    path: ../upstream
    version: "<0.2.0"
""",
            )

            code, output = _run_cli(["release", "minor", str(upstream), "--no-git-tag"])

            self.assertEqual(code, 0)
            self.assertIn("reverse deps: 1 compatible, 0 review, 1 incompatible", output)
            report = (upstream / "reports" / "release-v0.2.0.md").read_text(encoding="utf-8")
            self.assertIn("## Reverse Dependencies", report)
            self.assertIn("- compatible:", report)
            self.assertIn("- incompatible:", report)
            self.assertIn("version 0.2.0 does not satisfy <0.2.0", report)

    def test_build_binary_dry_run_outputs_pyinstaller_command(self) -> None:
        root = Path(__file__).resolve().parents[1]

        completed = subprocess.run(
            [sys.executable, "scripts/build_binary.py", "--dry-run"],
            cwd=root,
            check=True,
            text=True,
            capture_output=True,
        )

        self.assertIn("PyInstaller", completed.stdout)
        self.assertIn("--onefile", completed.stdout)
        self.assertIn("sit_launcher.py", completed.stdout)

    def test_git_wrappers_can_add_and_commit_initialized_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp) / "git-skill"
            init_code, _ = _run_cli(["init", "git-skill", "--path", str(package)])
            self.assertEqual(init_code, 0)

            subprocess.run(["git", "config", "user.email", "sit@example.test"], cwd=package, check=True)
            subprocess.run(["git", "config", "user.name", "SIT Test"], cwd=package, check=True)

            add_code, _ = _run_cli_in(package, ["add", "."])
            commit_code, _ = _run_cli_in(package, ["commit", "-m", "feat: initial skill"])
            log_code, _ = _run_cli_in(package, ["log", "--oneline"])
            log_output = subprocess.run(
                ["git", "log", "--oneline"],
                cwd=package,
                check=True,
                text=True,
                capture_output=True,
            ).stdout

            self.assertEqual(add_code, 0)
            self.assertEqual(commit_code, 0)
            self.assertEqual(log_code, 0)
            self.assertIn("feat: initial skill", log_output)

    def test_commit_allows_non_semantic_change_without_version_bump(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "docs-only-skill", version="0.1.0")
            _git(package, "init")
            _git(package, "config", "user.email", "sit@example.test")
            _git(package, "config", "user.name", "SIT Test")
            _git(package, "add", ".")
            _git(package, "commit", "-m", "feat: initial skill")
            (package / "docs").mkdir()
            (package / "docs" / "note.md").write_text("Operator note.\n", encoding="utf-8")
            _git(package, "add", ".")

            code, stdout, stderr = _run_cli_capture(["commit", "-m", "docs: add note"], cwd=package)

            self.assertEqual(code, 0)
            self.assertIn("required=none, actual=none", stdout)
            self.assertEqual(stderr, "")
            log = subprocess.run(["git", "log", "--oneline"], cwd=package, check=True, text=True, capture_output=True).stdout
            self.assertIn("docs: add note", log)

    def test_commit_blocks_breaking_change_without_major_version_bump(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "gate-skill", version="0.1.0")
            _git(package, "init")
            _git(package, "config", "user.email", "sit@example.test")
            _git(package, "config", "user.name", "SIT Test")
            _git(package, "add", ".")
            _git(package, "commit", "-m", "feat: initial skill")

            _upgrade_package_to_v2(package)
            _git(package, "add", ".")

            code, stdout, stderr = _run_cli_capture(["commit", "-m", "feat: require confidence"], cwd=package)

            self.assertEqual(code, 2)
            self.assertIn("commit version gate blocked", stdout)
            self.assertIn("Reason: semantic changes require at least a major version bump", stderr)
            self.assertIn("Fix: update skill.yaml to a major bump or run `sit release major`", stderr)
            self.assertIn("SCHEMA output property added confidence (required)", stderr)

    def test_commit_validates_staged_snapshot_not_unstaged_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "staged-commit-skill", version="0.1.0")
            _git(package, "init")
            _git(package, "config", "user.email", "sit@example.test")
            _git(package, "config", "user.name", "SIT Test")
            _git(package, "add", ".")
            _git(package, "commit", "-m", "feat: initial skill")
            (package / "docs").mkdir()
            (package / "docs" / "note.md").write_text("Operator note.\n", encoding="utf-8")
            _git(package, "add", "docs/note.md")
            (package / "schemas" / "output.schema.json").write_text("{not json", encoding="utf-8")

            code, stdout, stderr = _run_cli_capture(["commit", "-m", "docs: add note"], cwd=package)

            self.assertEqual(code, 0)
            self.assertIn("golden tests: skipped", stdout)
            self.assertEqual(stderr, "")
            log = subprocess.run(["git", "log", "--oneline"], cwd=package, check=True, text=True, capture_output=True).stdout
            self.assertIn("docs: add note", log)
            committed_schema = subprocess.run(
                ["git", "show", "HEAD:schemas/output.schema.json"],
                cwd=package,
                check=True,
                text=True,
                capture_output=True,
            ).stdout
            self.assertNotIn("{not json", committed_schema)

    def test_commit_gate_ignores_unstaged_version_bump(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "unstaged-bump-skill", version="0.1.0")
            _git(package, "init")
            _git(package, "config", "user.email", "sit@example.test")
            _git(package, "config", "user.name", "SIT Test")
            _git(package, "add", ".")
            _git(package, "commit", "-m", "feat: initial skill")
            (package / "prompts" / "extraction.md").write_text("Extract with care.", encoding="utf-8")
            _git(package, "add", "prompts/extraction.md")
            manifest = package / "skill.yaml"
            manifest.write_text(manifest.read_text(encoding="utf-8").replace("version: 0.1.0", "version: 0.2.0"), encoding="utf-8")

            code, stdout, stderr = _run_cli_capture(["commit", "-m", "feat: update prompt"], cwd=package)

            self.assertEqual(code, 2)
            self.assertIn("required=minor, actual=none", stdout)
            self.assertIn("semantic changes require at least a minor version bump", stderr)
            log = subprocess.run(["git", "log", "--oneline"], cwd=package, check=True, text=True, capture_output=True).stdout
            self.assertNotIn("feat: update prompt", log)
            staged_paths = subprocess.run(
                ["git", "diff", "--cached", "--name-only"],
                cwd=package,
                check=True,
                text=True,
                capture_output=True,
            ).stdout.splitlines()
            self.assertEqual(staged_paths, ["prompts/extraction.md"])

    def test_release_blocks_breaking_change_without_major_bump(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "release-gate-skill", version="0.1.0")
            _git(package, "init")
            _git(package, "config", "user.email", "sit@example.test")
            _git(package, "config", "user.name", "SIT Test")
            _git(package, "add", ".")
            _git(package, "commit", "-m", "feat: initial skill")
            _upgrade_package_to_v2(package)

            code, _stdout, stderr = _run_cli_capture(["release", "minor", str(package), "--no-git-tag"])

            self.assertEqual(code, 2)
            self.assertIn("release version gate blocked: risk=breaking-change, required=major, actual=minor", stderr)
            self.assertIn("Fix: update skill.yaml to a major bump or run `sit release major`", stderr)
            self.assertIn("version: 0.2.0", (package / "skill.yaml").read_text(encoding="utf-8"))

    def test_release_blocks_empty_release_after_commit_bump(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "already-bumped-skill", version="0.1.0")
            _git(package, "init")
            _git(package, "config", "user.email", "sit@example.test")
            _git(package, "config", "user.name", "SIT Test")
            _git(package, "add", ".")
            _git(package, "commit", "-m", "feat: initial skill")
            (package / "prompts" / "extraction.md").write_text("Extract with care.", encoding="utf-8")
            _git(package, "add", ".")
            commit_code, _commit_stdout, _commit_stderr = _run_cli_capture(
                ["commit", "-m", "feat: update prompt", "--bump", "minor"],
                cwd=package,
            )

            release_code, _release_stdout, release_stderr = _run_cli_capture(
                ["release", "minor", str(package), "--no-git-tag"]
            )

            self.assertEqual(commit_code, 0)
            self.assertEqual(release_code, 2)
            self.assertIn("release blocked: HEAD already contains semantic changes", release_stderr)
            self.assertIn("version: 0.2.0", (package / "skill.yaml").read_text(encoding="utf-8"))

    def test_release_allow_empty_overrides_commit_bump_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "allow-empty-release-skill", version="0.1.0")
            _git(package, "init")
            _git(package, "config", "user.email", "sit@example.test")
            _git(package, "config", "user.name", "SIT Test")
            _git(package, "add", ".")
            _git(package, "commit", "-m", "feat: initial skill")
            (package / "prompts" / "extraction.md").write_text("Extract with care.", encoding="utf-8")
            _git(package, "add", ".")
            commit_code, _commit_stdout, _commit_stderr = _run_cli_capture(
                ["commit", "-m", "feat: update prompt", "--bump", "minor"],
                cwd=package,
            )

            release_code, release_stdout, release_stderr = _run_cli_capture(
                ["release", "patch", str(package), "--no-git-tag", "--allow-empty"]
            )

            self.assertEqual(commit_code, 0)
            self.assertEqual(release_code, 0)
            self.assertEqual(release_stderr, "")
            self.assertIn("Released sample-skill@0.2.1", release_stdout)

    def test_release_tag_points_to_release_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "tagged-release-skill", version="0.1.0")
            _git(package, "init")
            _git(package, "config", "user.email", "sit@example.test")
            _git(package, "config", "user.name", "SIT Test")
            _git(package, "add", ".")
            _git(package, "commit", "-m", "feat: initial skill")

            code, output = _run_cli(["release", "patch", str(package)])

            self.assertEqual(code, 0)
            self.assertIn("git tag: v0.1.1", output)
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=package, check=True, text=True, capture_output=True).stdout.strip()
            tag_commit = subprocess.run(["git", "rev-parse", "v0.1.1^{}"], cwd=package, check=True, text=True, capture_output=True).stdout.strip()
            tagged_manifest = subprocess.run(
                ["git", "show", "v0.1.1:skill.yaml"],
                cwd=package,
                check=True,
                text=True,
                capture_output=True,
            ).stdout
            self.assertEqual(tag_commit, head)
            self.assertIn("version: 0.1.1", tagged_manifest)

    def test_release_allows_major_bump_for_breaking_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "release-major-skill", version="0.1.0")
            _git(package, "init")
            _git(package, "config", "user.email", "sit@example.test")
            _git(package, "config", "user.name", "SIT Test")
            _git(package, "add", ".")
            _git(package, "commit", "-m", "feat: initial skill")
            _upgrade_package_to_v2(package)

            code, output = _run_cli(["release", "major", str(package), "--no-git-tag"])

            self.assertEqual(code, 0)
            self.assertIn("Released sample-skill@1.0.0", output)
            self.assertIn("version: 1.0.0", (package / "skill.yaml").read_text(encoding="utf-8"))
            changelog = (package / "CHANGELOG.md").read_text(encoding="utf-8")
            report = (package / "reports" / "release-v1.0.0.md").read_text(encoding="utf-8")
            self.assertIn("### Breaking", changelog)
            self.assertIn("### Changes", changelog)
            self.assertIn("### Risk", changelog)
            self.assertIn("Release risk: breaking-change", changelog)
            self.assertIn("Suggested bump: major", changelog)
            self.assertIn("Version gate: required major, actual major", changelog)
            self.assertIn("Golden tests: pass (1/1)", changelog)
            self.assertIn("### Reproduce", changelog)
            self.assertIn("SCHEMA output property added confidence (required)", changelog)
            self.assertIn("## Release Summary", report)
            self.assertIn("release version gate passed", report)
            self.assertIn("### Breaking", report)
            self.assertIn("### Reproduce", report)
            self.assertIn("SCHEMA output property added confidence (required)", report)

    def test_yaml_numeric_version_is_normalized_for_release(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "numeric-version-skill", version="1.0")

            validate_code, validate_output = _run_cli(["validate", str(package)])
            release_code, release_output = _run_cli(["release", "patch", str(package), "--no-git-tag"])

            self.assertEqual(validate_code, 0)
            self.assertIn("OK  version: 1.0.0", validate_output)
            self.assertEqual(release_code, 0)
            self.assertIn("Released sample-skill@1.0.1", release_output)

    def test_full_git_passthrough_routes_any_git_subcommand(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "passthrough-skill", version="0.1.0")
            _git(package, "init")
            _git(package, "config", "user.email", "sit@example.test")
            _git(package, "config", "user.name", "SIT Test")
            _git(package, "add", ".")
            _git(package, "commit", "-m", "feat: initial")

            # Test commands beyond the original 6 passthrough set
            status_code, status_output = _run_cli_in(package, ["status"])
            # 'sit status' is a sit subcommand, not git passthrough
            self.assertEqual(status_code, 0)
            self.assertIn("Package: sample-skill", status_output)

            # 'sit stash' should route to git stash (passthrough)
            stash_code, stash_output = _run_cli_in(package, ["stash", "list"])
            self.assertEqual(stash_code, 0)

            # 'sit tag' should route to git tag
            tag_code, tag_output = _run_cli_in(package, ["tag", "--list"])
            self.assertEqual(tag_code, 0)

            # 'sit remote' should route to git remote
            remote_code, remote_output = _run_cli_in(package, ["remote", "-v"])
            self.assertEqual(remote_code, 0)

    def test_git_passthrough_failure_prints_sit_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, stdout, stderr = _run_cli_capture(["push"], cwd=Path(tmp))

            self.assertNotEqual(code, 0)
            self.assertIn("sit: hint:", stderr)
            self.assertIn("sit status", stderr)

    def test_quiet_suppresses_passthrough_failure_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, stdout, stderr = _run_cli_capture(["--quiet", "push"], cwd=Path(tmp))

            self.assertNotEqual(code, 0)
            self.assertNotIn("sit: hint:", stderr)

    def test_add_prints_lightweight_semantic_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "add-hint-skill", version="0.1.0")
            _git(package, "init")
            _git(package, "config", "user.email", "sit@example.test")
            _git(package, "config", "user.name", "SIT Test")
            _git(package, "add", ".")
            _git(package, "commit", "-m", "feat: initial skill")
            (package / "prompts" / "extraction.md").write_text("Extract with care.", encoding="utf-8")

            code, output = _run_cli_in(package, ["add", "prompts/extraction.md"])

            self.assertEqual(code, 0)
            self.assertIn("semantic path(s)", output)
            self.assertIn("run `sit diff --staged`", output)

    def test_help_shows_sit_help_not_git_help(self) -> None:
        code, output = _run_cli(["help"])

        self.assertEqual(code, 0)
        self.assertIn("Skill Iteration Toolkit CLI", output)
        self.assertIn("ci-summary", output)

    def test_noise_filtering_suppresses_non_monotonic_index(self) -> None:
        from sit.git import _filter_stderr

        noisy = "error: non-monotonic index pack-abc123.idx\nfatal: actual error\n"
        filtered = _filter_stderr(noisy)
        self.assertNotIn("non-monotonic", filtered)
        self.assertIn("fatal: actual error", filtered)

        clean = "Everything up-to-date"
        self.assertEqual(_filter_stderr(clean), clean)
        self.assertEqual(_filter_stderr(""), "")

    def test_parse_pack_issues_detects_corruption(self) -> None:
        from sit.git import parse_pack_issues

        output = "error: non-monotonic index pack-abc.idx\n"
        issues = parse_pack_issues(output)
        self.assertEqual(len(issues), 1)
        self.assertIn("non-monotonic", issues[0])

        output_fork = "warning: ._pack-abc in .git/objects\n"
        issues_fork = parse_pack_issues(output_fork)
        self.assertEqual(len(issues_fork), 1)
        self.assertIn("macOS resource fork", issues_fork[0])

        self.assertEqual(parse_pack_issues(""), [])

    def test_additive_changes_require_only_patch_bump(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = _write_package(root / "old", version="0.1.0")
            new = _write_package(root / "new", version="0.1.0")
            # Add a new golden test case (additive-only change)
            records = [
                {"case_id": "case-1", "input": {"text": "hello"}, "expected": {"answer": "ok"}},
                {"case_id": "case-2", "input": {"text": "world"}, "expected": {"answer": "yes"}},
            ]
            (new / "tests" / "golden.jsonl").write_text(
                "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
            )

            json_code, json_output = _run_cli(["diff", str(old), str(new), "--format", "json"])

            self.assertEqual(json_code, 0)
            payload = json.loads(json_output)
            self.assertEqual(payload["suggested_bump"], "patch")
            golden_events = [e for e in payload["events"] if "GOLDEN" in e["message"] and "added" in e["message"]]
            self.assertTrue(golden_events)
            self.assertTrue(golden_events[0]["additive"])

    def test_commit_bump_auto_bumps_and_commits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "bump-skill", version="0.1.0")
            _git(package, "init")
            _git(package, "config", "user.email", "sit@example.test")
            _git(package, "config", "user.name", "SIT Test")
            _git(package, "add", ".")
            _git(package, "commit", "-m", "feat: initial skill")
            (package / "prompts" / "extraction.md").write_text("Extract with care.", encoding="utf-8")
            _git(package, "add", ".")

            code, stdout, stderr = _run_cli_capture(["commit", "-m", "feat: update prompt", "--bump", "minor"], cwd=package)

            self.assertEqual(code, 0)
            self.assertIn("auto-bumped version: 0.1.0 -> 0.2.0", stdout)
            self.assertIn("committed", stdout)
            manifest = (package / "skill.yaml").read_text(encoding="utf-8")
            self.assertIn("version: 0.2.0", manifest)

    def test_gate_cache_tracks_prompt_content_changes(self) -> None:
        from sit.gate import check_version_gate_against_head, invalidate_gate_cache
        from sit.package import load_package

        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "cache-skill", version="0.1.0")
            _git(package, "init")
            _git(package, "config", "user.email", "sit@example.test")
            _git(package, "config", "user.name", "SIT Test")
            _git(package, "add", ".")
            _git(package, "commit", "-m", "feat: initial skill")
            invalidate_gate_cache()

            first = check_version_gate_against_head(load_package(package))
            (package / "prompts" / "extraction.md").write_text("Extract with new behavior.", encoding="utf-8")
            second = check_version_gate_against_head(load_package(package))

            self.assertEqual(first.required_bump, "none")
            self.assertEqual(second.required_bump, "minor")

    def test_commit_smart_skip_skips_tests_for_docs_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "skip-skill", version="0.1.0")
            _git(package, "init")
            _git(package, "config", "user.email", "sit@example.test")
            _git(package, "config", "user.name", "SIT Test")
            _git(package, "add", ".")
            _git(package, "commit", "-m", "feat: initial skill")
            (package / "docs").mkdir()
            (package / "docs" / "note.md").write_text("Note.\n", encoding="utf-8")
            _git(package, "add", ".")

            code, stdout, stderr = _run_cli_capture(["commit", "-m", "docs: add note"], cwd=package)

            self.assertEqual(code, 0)
            self.assertIn("golden tests: skipped", stdout)

    def test_diff_staged_compares_index_not_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "staged-skill", version="0.1.0")
            _git(package, "init")
            _git(package, "config", "user.email", "sit@example.test")
            _git(package, "config", "user.name", "SIT Test")
            _git(package, "add", ".")
            _git(package, "commit", "-m", "feat: initial skill")
            prompt = package / "prompts" / "extraction.md"
            prompt.write_text("Extract staged answer.\n", encoding="utf-8")
            _git(package, "add", "prompts/extraction.md")
            prompt.write_text("Extract unstaged answer.\n", encoding="utf-8")

            code, output = _run_cli_in(package, ["diff", "--staged", "--prompt"])

            self.assertEqual(code, 0)
            self.assertIn("Current: sample-skill@0.1.0 (STAGED)", output)
            self.assertIn("+Extract staged answer.", output)
            self.assertNotIn("unstaged answer", output)

    def test_undo_soft_resets_last_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "undo-skill", version="0.1.0")
            _git(package, "init")
            _git(package, "config", "user.email", "sit@example.test")
            _git(package, "config", "user.name", "SIT Test")
            _git(package, "add", ".")
            _git(package, "commit", "-m", "feat: initial skill")
            (package / "docs").mkdir()
            (package / "docs" / "note.md").write_text("Note.\n", encoding="utf-8")
            _git(package, "add", ".")
            _git(package, "commit", "-m", "docs: note")

            code, output = _run_cli_in(package, ["undo", "--package", str(package)])

            self.assertEqual(code, 0)
            self.assertIn("undid commit: docs: note", output)
            self.assertIn("changes preserved", output)
            log = subprocess.run(["git", "log", "--oneline"], cwd=package, check=True, text=True, capture_output=True).stdout
            self.assertNotIn("docs: note", log)
            self.assertIn("feat: initial skill", log)

    def test_undo_dry_run_does_not_reset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = _write_package(Path(tmp) / "undo-dry-run-skill", version="0.1.0")
            _git(package, "init")
            _git(package, "config", "user.email", "sit@example.test")
            _git(package, "config", "user.name", "SIT Test")
            _git(package, "add", ".")
            _git(package, "commit", "-m", "feat: initial skill")
            (package / "docs").mkdir()
            (package / "docs" / "note.md").write_text("Note.\n", encoding="utf-8")
            _git(package, "add", ".")
            _git(package, "commit", "-m", "docs: note")

            code, output = _run_cli_in(package, ["undo", "--dry-run", "--package", str(package)])

            self.assertEqual(code, 0)
            self.assertIn("would undo commit: docs: note", output)
            log = subprocess.run(["git", "log", "--oneline"], cwd=package, check=True, text=True, capture_output=True).stdout
            self.assertIn("docs: note", log)

    def test_undo_package_resets_specified_repo_from_other_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = _write_package(root / "undo-target", version="0.1.0")
            other = root / "other-repo"
            other.mkdir()
            _git(package, "init")
            _git(package, "config", "user.email", "sit@example.test")
            _git(package, "config", "user.name", "SIT Test")
            _git(package, "add", ".")
            _git(package, "commit", "-m", "feat: initial skill")
            (package / "docs").mkdir()
            (package / "docs" / "note.md").write_text("Note.\n", encoding="utf-8")
            _git(package, "add", ".")
            _git(package, "commit", "-m", "docs: note")

            _git(other, "init")
            _git(other, "config", "user.email", "sit@example.test")
            _git(other, "config", "user.name", "SIT Test")
            (other / "README.md").write_text("Other.\n", encoding="utf-8")
            _git(other, "add", ".")
            _git(other, "commit", "-m", "feat: other initial")

            code, output = _run_cli_capture(["undo", "--package", str(package)], cwd=other)[:2]

            self.assertEqual(code, 0)
            self.assertIn("undid commit: docs: note", output)
            target_log = subprocess.run(["git", "log", "--oneline"], cwd=package, check=True, text=True, capture_output=True).stdout
            other_log = subprocess.run(["git", "log", "--oneline"], cwd=other, check=True, text=True, capture_output=True).stdout
            self.assertNotIn("docs: note", target_log)
            self.assertIn("feat: other initial", other_log)


def _run_cli(argv: list[str]) -> tuple[int, str]:
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        code = main(argv)
    return code, stdout.getvalue()


def _run_cli_in(cwd: Path, argv: list[str]) -> tuple[int, str]:
    old_cwd = Path.cwd()
    stdout = io.StringIO()
    try:
        import os

        os.chdir(cwd)
        with contextlib.redirect_stdout(stdout):
            code = main(argv)
    finally:
        os.chdir(old_cwd)
    return code, stdout.getvalue()


def _run_cli_capture(argv: list[str], *, cwd: Path | None = None) -> tuple[int, str, str]:
    old_cwd = Path.cwd()
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        if cwd is not None:
            import os

            os.chdir(cwd)
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(argv)
    finally:
        if cwd is not None:
            import os

            os.chdir(old_cwd)
    return code, stdout.getvalue(), stderr.getvalue()


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, text=True, capture_output=True)


def _write_package(
    root: Path,
    *,
    version: str,
    prompt: str = "Extract the answer.",
    extra_required: dict[str, dict[str, str]] | None = None,
) -> Path:
    (root / "prompts").mkdir(parents=True)
    (root / "schemas").mkdir()
    (root / "tests").mkdir()
    (root / "reports").mkdir()

    (root / "skill.yaml").write_text(
        "\n".join(
            [
                "name: sample-skill",
                f"version: {version}",
                "description: Sample package for CLI tests.",
                "prompts:",
                "  extraction: prompts/extraction.md",
                "schemas:",
                "  output: schemas/output.schema.json",
                "tests:",
                "  golden: tests/golden.jsonl",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (root / "prompts" / "extraction.md").write_text(prompt, encoding="utf-8")

    properties: dict[str, dict[str, str]] = {"answer": {"type": "string"}}
    required = ["answer"]
    if extra_required:
        properties.update(extra_required)
        required.extend(extra_required)

    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }
    (root / "schemas" / "output.schema.json").write_text(json.dumps(schema), encoding="utf-8")

    expected = {"answer": "ok"}
    if extra_required:
        expected.update({name: "high" for name in extra_required})
    record = {"case_id": "case-1", "input": {"text": "hello"}, "expected": expected}
    (root / "tests" / "golden.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")

    return root


def _write_nested_schema(
    root: Path,
    *,
    enum_values: list[str],
    tag_values: list[str],
    require_confidence: bool,
    closed: bool,
) -> None:
    schema_path = root / "schemas" / "output.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    metadata_required = ["source"]
    metadata_properties = {
        "source": {"type": "string", "enum": enum_values},
        "tags": {"type": "array", "items": {"type": "string", "enum": tag_values}},
    }
    if require_confidence:
        metadata_required.append("confidence")
        metadata_properties["confidence"] = {"type": "string"}
    schema["properties"]["metadata"] = {
        "type": "object",
        "additionalProperties": not closed,
        "required": metadata_required,
        "properties": metadata_properties,
    }
    schema_path.write_text(json.dumps(schema), encoding="utf-8")


def _write_complex_schema(root: Path, *, include_link_payload: bool, add_confidence_constraint: bool) -> None:
    schema_path = root / "schemas" / "output.schema.json"
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["answer", "classification", "payload", "metadata"],
        "properties": {
            "answer": {"type": "string"},
            "classification": {"$ref": "#/$defs/classification"},
            "payload": {
                "oneOf": [
                    {"$ref": "#/$defs/textPayload"},
                    *([{"$ref": "#/$defs/linkPayload"}] if include_link_payload else []),
                ]
            },
            "metadata": {
                "allOf": [
                    {"$ref": "#/$defs/baseMetadata"},
                    *([{"required": ["confidence"], "properties": {"confidence": {"type": "string"}}}] if add_confidence_constraint else []),
                ]
            },
        },
        "$defs": {
            "classification": {
                "type": "string",
                "enum": ["survey", "method", *([] if add_confidence_constraint else ["dataset"])],
            },
            "textPayload": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text"],
                "properties": {"text": {"type": "string"}},
            },
            "linkPayload": {
                "type": "object",
                "additionalProperties": False,
                "required": ["url"],
                "properties": {"url": {"type": "string", "format": "uri"}},
            },
            "baseMetadata": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source"],
                "properties": {"source": {"type": "string"}},
            },
        },
    }
    schema_path.write_text(json.dumps(schema), encoding="utf-8")


def _write_ref_target_schema(root: Path, *, ref: str) -> None:
    schema_path = root / "schemas" / "output.schema.json"
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "required": ["answer", "reviewer"],
        "properties": {
            "answer": {"type": "string"},
            "reviewer": {"$ref": ref},
        },
        "definitions": {
            "reviewer": {"type": "string"},
        },
        "$defs": {
            "reviewer": {"type": "string", "enum": ["lead", "peer"]},
        },
    }
    schema_path.write_text(json.dumps(schema), encoding="utf-8")


def _write_anyof_schema(root: Path, *, include_number: bool) -> None:
    schema_path = root / "schemas" / "output.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["properties"]["choice"] = {
        "anyOf": [
            {"type": "string"},
            *([{"type": "number"}] if include_number else []),
        ]
    }
    schema["required"].append("choice")
    schema_path.write_text(json.dumps(schema), encoding="utf-8")


def _upgrade_package_to_v2(root: Path) -> None:
    manifest = (root / "skill.yaml").read_text(encoding="utf-8")
    (root / "skill.yaml").write_text(manifest.replace("version: 0.1.0", "version: 0.2.0"), encoding="utf-8")
    (root / "prompts" / "extraction.md").write_text("Extract the answer and cite evidence.", encoding="utf-8")

    schema_path = root / "schemas" / "output.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["properties"]["confidence"] = {"type": "string"}
    schema["required"].append("confidence")
    schema_path.write_text(json.dumps(schema), encoding="utf-8")

    record = {"case_id": "case-1", "input": {"text": "hello"}, "expected": {"answer": "ok", "confidence": "high"}}
    (root / "tests" / "golden.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")


def _set_manifest_status(root: Path, status: str) -> None:
    path = root / "skill.yaml"
    lines = path.read_text(encoding="utf-8").splitlines()
    updated: list[str] = []
    replaced = False
    inserted = False
    for line in lines:
        if line.startswith("status:"):
            updated.append(f"status: {status}")
            replaced = True
            inserted = True
            continue
        updated.append(line)
        if not replaced and not inserted and line.startswith("version:"):
            updated.append(f"status: {status}")
            inserted = True
    if not inserted:
        updated.append(f"status: {status}")
    path.write_text("\n".join(updated) + "\n", encoding="utf-8")


def _write_runner_script(root: Path, *, answer_expr: str) -> None:
    scripts = root / "scripts"
    scripts.mkdir(exist_ok=True)
    (scripts / "run_case.py").write_text(
        "\n".join(
            [
                "import argparse",
                "import json",
                "",
                "parser = argparse.ArgumentParser()",
                "parser.add_argument('--input', required=True)",
                "parser.add_argument('--output', required=True)",
                "args = parser.parse_args()",
                "payload = json.loads(open(args.input, encoding='utf-8').read())",
                f"actual = {{'answer': {answer_expr}}}",
                "open(args.output, 'w', encoding='utf-8').write(json.dumps(actual))",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_case_id_runner_script(root: Path) -> None:
    scripts = root / "scripts"
    scripts.mkdir(exist_ok=True)
    (scripts / "run_case.py").write_text(
        "\n".join(
            [
                "import argparse",
                "import json",
                "",
                "parser = argparse.ArgumentParser()",
                "parser.add_argument('--input', required=True)",
                "parser.add_argument('--output', required=True)",
                "parser.add_argument('--case-id', required=True)",
                "args = parser.parse_args()",
                "open(args.output, 'w', encoding='utf-8').write(json.dumps({'answer': 'ok'}))",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_resource_files(root: Path, *, script: str, asset: str, reference: str) -> None:
    (root / "scripts").mkdir(exist_ok=True)
    (root / "assets").mkdir(exist_ok=True)
    (root / "references").mkdir(exist_ok=True)
    (root / "scripts" / "scan.py").write_text(script, encoding="utf-8")
    (root / "assets" / "template.html").write_text(asset, encoding="utf-8")
    (root / "references" / "guide.md").write_text(reference, encoding="utf-8")


def _write_deps_yaml(root: Path, content: str) -> None:
    (root / "deps.yaml").write_text(content.strip() + "\n", encoding="utf-8")


def _append_runner_command(root: Path, *, command: str | None = None) -> None:
    manifest = (root / "skill.yaml").read_text(encoding="utf-8")
    command = command or f"{sys.executable} scripts/run_case.py --input {{input}} --output {{output}}"
    (root / "skill.yaml").write_text(manifest + f"commands:\n  run_case: \"{command}\"\n", encoding="utf-8")


def _assert_json_schema_valid(schema_name: str, payload: dict) -> None:
    from jsonschema import Draft202012Validator

    root = Path(__file__).resolve().parents[1]
    schema = json.loads((root / "docs" / "schemas" / schema_name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(payload)


if __name__ == "__main__":
    unittest.main()
