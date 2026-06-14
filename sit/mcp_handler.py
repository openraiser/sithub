from __future__ import annotations

import json
from typing import Any

from .sdk import Sit


def handle_tool(name: str, arguments: dict[str, Any] | None) -> str:
    """Dispatch an MCP tool call to the SDK and return a JSON string."""
    arguments = arguments or {}
    try:
        if name == "sit_info":
            result = Sit(arguments["package_path"]).info()
        elif name == "sit_validate":
            result = Sit(arguments["package_path"]).validate()
        elif name == "sit_test":
            result = Sit(arguments["package_path"]).test(
                run_actual=arguments.get("run_actual", False),
                runner=arguments.get("runner"),
                timeout=arguments.get("timeout", 30),
            )
        elif name == "sit_diff":
            result = Sit(arguments["new_path"]).diff(
                arguments["old_path"],
                include_text_diffs=arguments.get("include_text_diffs", False),
            )
        elif name == "sit_diff_range":
            result = Sit(arguments["package_path"]).diff_range(
                arguments.get("range", "HEAD..WORKTREE"),
                include_text_diffs=arguments.get("include_text_diffs", False),
            )
        elif name == "sit_diff_staged":
            result = Sit(arguments["package_path"]).diff_staged(
                include_text_diffs=arguments.get("include_text_diffs", False),
            )
        elif name == "sit_review":
            result = Sit(arguments["current_path"]).review(arguments["baseline_path"])
        elif name == "sit_review_range":
            result = Sit(arguments["package_path"]).review_range(arguments.get("range", "HEAD..WORKTREE"))
        elif name == "sit_review_staged":
            result = Sit(arguments["package_path"]).review_staged()
        elif name == "sit_pr_summary":
            result = Sit(arguments["current_path"]).pr_summary(arguments["baseline_path"])
        elif name == "sit_report":
            result = Sit(arguments["package_path"]).report(
                compare=arguments.get("compare_path"),
            )
        elif name == "sit_doctor":
            result = Sit(arguments["package_path"]).doctor()
        else:
            return error_payload(name, ValueError(f"Unknown tool: {name}"))
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as exc:
        return error_payload(name, exc)


def error_payload(name: str, exc: Exception) -> str:
    return json.dumps(
        {
            "schema_version": "sit.mcp_error.v1",
            "ok": False,
            "tool": name,
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
            },
        },
        ensure_ascii=False,
        indent=2,
    )
