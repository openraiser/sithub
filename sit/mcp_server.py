"""MCP (Model Context Protocol) server for sit.

Exposes sit commands as MCP tools so LLM agents can call them natively
via the MCP protocol.  Uses the Python SDK under the hood — no subprocess.

Usage::

    # Run as stdio MCP server (default transport):
    python -m sit.mcp_server

    # Or import and customise:
    from sit.mcp_server import create_server
    server = create_server()
    server.run()
"""

from __future__ import annotations

from typing import Any

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool
except ImportError:
    raise ImportError(
        "MCP support requires the 'mcp' package. "
        "Install it with: pip install 'sit-toolkit[mcp]' or pip install mcp"
    )

from .mcp_handler import error_payload, handle_tool

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="sit_info",
        description=(
            "Get package info for a sit skill package. "
            "Returns the sit.info.v1 contract: name, version, git state, "
            "file listing, validation status, golden test results, reports."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "package_path": {
                    "type": "string",
                    "description": "Path to the skill package directory or skill.yaml file.",
                },
            },
            "required": ["package_path"],
        },
    ),
    Tool(
        name="sit_validate",
        description=(
            "Validate a sit skill package structure. "
            "Checks skill.yaml fields, prompt/schema/test paths, schema validity, "
            "JSONL format. Returns pass/fail with messages."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "package_path": {
                    "type": "string",
                    "description": "Path to the skill package directory or skill.yaml file.",
                },
            },
            "required": ["package_path"],
        },
    ),
    Tool(
        name="sit_test",
        description=(
            "Run validation + golden schema tests on a sit skill package. "
            "Returns the sit.test.v1 contract with validation and test results."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "package_path": {
                    "type": "string",
                    "description": "Path to the skill package directory or skill.yaml file.",
                },
                "run_actual": {
                    "type": "boolean",
                    "description": "If true, run the actual test runner (commands.run_case) instead of static validation only.",
                    "default": False,
                },
                "runner": {
                    "type": "string",
                    "description": "Override the test runner command (default: from skill.yaml commands.run_case).",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Runner timeout in seconds (default: 30).",
                    "default": 30,
                },
            },
            "required": ["package_path"],
        },
    ),
    Tool(
        name="sit_diff",
        description=(
            "Semantic diff between two sit skill packages. "
            "Returns the sit.diff.v1 contract with change events, risk assessment, "
            "and suggested version bump."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "old_path": {
                    "type": "string",
                    "description": "Path to the baseline/old skill package.",
                },
                "new_path": {
                    "type": "string",
                    "description": "Path to the current/new skill package.",
                },
                "include_text_diffs": {
                    "type": "boolean",
                    "description": "Include full unified diff lines in text_diffs (default: false).",
                    "default": False,
                },
            },
            "required": ["old_path", "new_path"],
        },
    ),
    Tool(
        name="sit_diff_range",
        description=(
            "Semantic diff for a Git range in a sit skill package, such as main..HEAD, "
            "HEAD..WORKTREE, or HEAD..STAGED."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "package_path": {
                    "type": "string",
                    "description": "Path to the skill package directory or skill.yaml file.",
                },
                "range": {
                    "type": "string",
                    "description": "Git range to compare (default: HEAD..WORKTREE).",
                    "default": "HEAD..WORKTREE",
                },
                "include_text_diffs": {
                    "type": "boolean",
                    "description": "Include full unified diff lines in text_diffs (default: false).",
                    "default": False,
                },
            },
            "required": ["package_path"],
        },
    ),
    Tool(
        name="sit_diff_staged",
        description="Semantic diff between HEAD and the currently staged Git index.",
        inputSchema={
            "type": "object",
            "properties": {
                "package_path": {
                    "type": "string",
                    "description": "Path to the skill package directory or skill.yaml file.",
                },
                "include_text_diffs": {
                    "type": "boolean",
                    "description": "Include full unified diff lines in text_diffs (default: false).",
                    "default": False,
                },
            },
            "required": ["package_path"],
        },
    ),
    Tool(
        name="sit_review",
        description=(
            "Generate a PR-ready Skill review for a package change. "
            "Returns the sit.review.v1 contract with validation, tests, risk, "
            "artifact summary, merge recommendation, and semantic diff."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "baseline_path": {
                    "type": "string",
                    "description": "Path to the baseline skill package.",
                },
                "current_path": {
                    "type": "string",
                    "description": "Path to the current skill package.",
                },
            },
            "required": ["baseline_path", "current_path"],
        },
    ),
    Tool(
        name="sit_review_range",
        description=(
            "Generate a PR-ready Skill review for a Git range, such as main..HEAD, "
            "HEAD..WORKTREE, or HEAD..STAGED."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "package_path": {
                    "type": "string",
                    "description": "Path to the skill package directory or skill.yaml file.",
                },
                "range": {
                    "type": "string",
                    "description": "Git range to compare (default: HEAD..WORKTREE).",
                    "default": "HEAD..WORKTREE",
                },
            },
            "required": ["package_path"],
        },
    ),
    Tool(
        name="sit_review_staged",
        description="Generate a PR-ready Skill review for the currently staged Git index.",
        inputSchema={
            "type": "object",
            "properties": {
                "package_path": {
                    "type": "string",
                    "description": "Path to the skill package directory or skill.yaml file.",
                },
            },
            "required": ["package_path"],
        },
    ),
    Tool(
        name="sit_pr_summary",
        description=(
            "Generate a PR summary for a skill package change. "
            "Returns the sit.pr_summary.v1 contract with baseline/current refs, "
            "validation, tests, risk, and semantic diff."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "baseline_path": {
                    "type": "string",
                    "description": "Path to the baseline skill package.",
                },
                "current_path": {
                    "type": "string",
                    "description": "Path to the current skill package.",
                },
            },
            "required": ["baseline_path", "current_path"],
        },
    ),
    Tool(
        name="sit_report",
        description=(
            "Build a full sit report for a skill package. "
            "Returns the sit.report.v1 contract with validation, golden tests, "
            "optional diff, and reproducibility commands."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "package_path": {
                    "type": "string",
                    "description": "Path to the skill package directory or skill.yaml file.",
                },
                "compare_path": {
                    "type": "string",
                    "description": "Optional path to a baseline package for diff comparison.",
                },
            },
            "required": ["package_path"],
        },
    ),
    Tool(
        name="sit_doctor",
        description=(
            "Run environment diagnostics for a sit skill package. "
            "Checks git, GitHub remote, manifest, validation, and golden tests."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "package_path": {
                    "type": "string",
                    "description": "Path to the skill package directory or skill.yaml file.",
                },
            },
            "required": ["package_path"],
        },
    ),
]


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------

def create_server() -> Server:
    """Create and configure an MCP Server instance with sit tools."""
    server = Server("sit-mcp-server")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        try:
            result = handle_tool(name, arguments)
        except Exception as exc:
            result = error_payload(name, exc)
        return [TextContent(type="text", text=result)]

    return server


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _run_stdio() -> None:
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    """Run the sit MCP server over stdio transport."""
    import asyncio

    asyncio.run(_run_stdio())


if __name__ == "__main__":
    main()
