from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from test_cli import _assert_json_schema_valid, _git, _run_cli, _write_package


class AgentContractTest(unittest.TestCase):
    def test_json_contract_schemas_validate_real_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = _write_package(root / "old", version="0.1.0")
            new = _write_package(root / "new", version="0.2.0", extra_required={"confidence": {"type": "string"}})

            diff_code, diff_output = _run_cli(["diff", str(old), str(new), "--format", "json"])
            review_code, review_output = _run_cli(["review", str(old), str(new), "--format", "json"])
            deps_code, deps_output = _run_cli(["deps", "check", str(new), "--format", "json"])
            doctor_code, doctor_output = _run_cli(["doctor", str(new), "--format", "json"])

            self.assertEqual(diff_code, 0)
            self.assertEqual(review_code, 0)
            self.assertEqual(deps_code, 0)
            self.assertIn(doctor_code, {0, 1})

            _assert_json_schema_valid("sit-diff-v1.schema.json", json.loads(diff_output))
            _assert_json_schema_valid("sit-review-v1.schema.json", json.loads(review_output))
            _assert_json_schema_valid("sit-deps-v1.schema.json", json.loads(deps_output))
            _assert_json_schema_valid("sit-doctor-v1.schema.json", json.loads(doctor_output))

    def test_review_is_available_through_sdk_and_tool_schema(self) -> None:
        from sit.sdk import Sit
        from sit.tool_use import get_tool

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = _write_package(root / "old", version="0.1.0")
            new = _write_package(root / "new", version="0.2.0", extra_required={"confidence": {"type": "string"}})

            payload = Sit(new).review(old)
            tool = get_tool("sit_review")

            self.assertEqual(payload["schema_version"], "sit.review.v1")
            self.assertEqual(payload["review"]["status"], "needs-maintainer-review")
            self.assertEqual(tool["name"], "sit_review")
            self.assertEqual(tool["parameters"]["required"], ["baseline_path", "current_path"])

    def test_sdk_and_tool_schema_support_git_range_and_staged_review(self) -> None:
        from sit.sdk import Sit
        from sit.tool_use import get_tool

        with tempfile.TemporaryDirectory() as tmp:
            original_cwd = Path.cwd()
            package = _write_package(Path(tmp) / "sdk-range-skill", version="0.1.0")
            _git(package, "init")
            _git(package, "config", "user.email", "sit@example.test")
            _git(package, "config", "user.name", "SIT Test")
            _git(package, "add", ".")
            _git(package, "commit", "-m", "feat: initial skill")
            (package / "prompts" / "extraction.md").write_text("Extract with care.", encoding="utf-8")

            diff_payload = Sit(package).diff_range("HEAD..WORKTREE")
            review_payload = Sit(package).review_range("HEAD..WORKTREE")
            report_payload = Sit(package).report_range("HEAD..WORKTREE")

            self.assertEqual(diff_payload["old"]["source"], "HEAD")
            self.assertEqual(diff_payload["new"]["source"], "WORKTREE")
            self.assertEqual(diff_payload["risk"], "review-required")
            self.assertEqual(review_payload["schema_version"], "sit.review.v1")
            self.assertEqual(review_payload["review"]["status"], "needs-review")
            self.assertEqual(report_payload["package"]["source"], "WORKTREE")
            self.assertEqual(Path.cwd(), original_cwd)

            _git(package, "add", "prompts/extraction.md")
            staged_diff = Sit(package).diff_staged()
            staged_review = Sit(package).review_staged()

            self.assertEqual(staged_diff["new"]["source"], "STAGED")
            self.assertEqual(staged_review["current"]["source"], "STAGED")
            self.assertEqual(get_tool("sit_diff_range")["parameters"]["required"], ["package_path"])
            self.assertEqual(get_tool("sit_diff_staged")["parameters"]["required"], ["package_path"])
            self.assertEqual(get_tool("sit_review_range")["parameters"]["required"], ["package_path"])
            self.assertEqual(get_tool("sit_review_staged")["parameters"]["required"], ["package_path"])

    def test_mcp_tool_errors_are_structured(self) -> None:
        from sit.mcp_handler import handle_tool

        unknown = json.loads(handle_tool("sit_missing", {}))
        missing_args = json.loads(handle_tool("sit_info", {}))

        self.assertEqual(unknown["schema_version"], "sit.mcp_error.v1")
        self.assertFalse(unknown["ok"])
        self.assertEqual(unknown["tool"], "sit_missing")
        self.assertEqual(unknown["error"]["type"], "ValueError")
        self.assertEqual(missing_args["schema_version"], "sit.mcp_error.v1")
        self.assertEqual(missing_args["tool"], "sit_info")
        self.assertEqual(missing_args["error"]["type"], "KeyError")


if __name__ == "__main__":
    unittest.main()
