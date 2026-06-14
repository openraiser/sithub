from __future__ import annotations

import argparse
import json
from pathlib import Path

from .errors import SitError
from .report import render_report_html, render_report_markdown
from .script_summary import render_script_details
from .summary import build_pr_summary, build_pr_summary_payload, build_pr_summary_text


def print_path_group(title: str, paths: dict[str, Path]) -> None:
    print(f"{title}:")
    if not paths:
        print("  <none>")
        return
    for name, path in paths.items():
        marker = "exists" if path.exists() else "missing"
        print(f"  {name}: {marker} {path}")


def ci_compare_spec(args: argparse.Namespace) -> str | None:
    if args.compare and (args.baseline_ref or args.head_ref):
        raise SitError("Use either --compare or --baseline-ref/--head-ref, not both")
    if args.compare:
        return args.compare
    if args.baseline_ref or args.head_ref:
        return f"{args.baseline_ref or 'origin/main'}..{args.head_ref or 'HEAD'}"
    return None


def write_ci_artifacts(directory: Path, payload: dict, summary: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "sit-summary.md").write_text(summary, encoding="utf-8")
    (directory / "sit-report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (directory / "sit-report.md").write_text(render_report_markdown(payload), encoding="utf-8")
    (directory / "sit-report.html").write_text(render_report_html(payload), encoding="utf-8")


def render_diff(
    result,
    old,
    new,
    output_format: str,
    *,
    old_source: str | None = None,
    new_source: str | None = None,
    show_prompt_diff: bool = False,
) -> str:
    if output_format == "json":
        return json.dumps(
            result.to_dict(old, new, old_source=old_source, new_source=new_source, include_text_diffs=show_prompt_diff),
            ensure_ascii=False,
            indent=2,
        ) + "\n"
    if output_format == "markdown":
        lines = [
            "## Skill Diff",
            "",
            f"- Baseline: `{format_package_ref(old, old_source)}`",
            f"- Current: `{format_package_ref(new, new_source)}`",
            f"- Risk: `{result.risk}`",
            f"- Suggested version bump: `{result.suggested_bump}`",
            "",
            "### Events",
            "",
        ]
        for event in result.events:
            lines.append(f"- `{event.message}`")
            lines.extend(f"  - `{detail}`" for detail in event_detail_lines(event))
        if result.text_diffs:
            lines.extend(["", "### Prompt/Reference Text Summary", ""])
            lines.extend(f"- `{text_diff.summary}`" for text_diff in result.text_diffs)
        if show_prompt_diff and result.text_diffs:
            lines.extend(["", "### Prompt/Reference Unified Diff", ""])
            for text_diff in result.text_diffs:
                lines.extend([f"#### {text_diff.kind}: {text_diff.name}", "", "```diff"])
                lines.extend(text_diff.lines)
                lines.extend(["```", ""])
        return "\n".join(lines) + "\n"
    if output_format == "plain":
        lines = []
        for event in result.events:
            lines.append(event.message)
            lines.extend(event_detail_lines(event))
        if show_prompt_diff and result.text_diffs:
            lines.extend(["", "Prompt/Reference Unified Diff:"])
            for text_diff in result.text_diffs:
                lines.append(f"--- {text_diff.kind}: {text_diff.name}")
                lines.extend(text_diff.lines)
        return "\n".join(lines) + "\n"

    lines = [
        "Skill Diff",
        f"Baseline: {format_package_ref(old, old_source)}",
        f"Current: {format_package_ref(new, new_source)}",
        f"Risk: {result.risk}",
        f"Suggested version bump: {result.suggested_bump}",
        "",
    ]
    grouped: dict[str, list[str]] = {}
    for event in result.events:
        grouped.setdefault(event.category, []).append(event.message)
    for category in sorted(grouped):
        lines.append(f"[{category}]")
        for event in sorted((event for event in result.events if event.category == category), key=lambda item: item.message):
            lines.append(f"  - {event.message}")
            lines.extend(f"    {detail}" for detail in event_detail_lines(event))
        lines.append("")
    if result.text_diffs:
        lines.extend(["Prompt/Reference Text Summary:"])
        lines.extend(text_diff.summary for text_diff in result.text_diffs)
    if show_prompt_diff and result.text_diffs:
        lines.extend(["", "Prompt/Reference Unified Diff:"])
        for text_diff in result.text_diffs:
            lines.append(f"--- {text_diff.kind}: {text_diff.name}")
            lines.extend(text_diff.lines)
    return "\n".join(lines) + "\n"


def format_package_ref(package, source: str | None = None) -> str:
    ref = f"{package.name or '<unknown>'}@{package.version or '<unknown>'}"
    return f"{ref} ({source})" if source else ref


def event_detail_lines(event) -> list[str]:
    details = getattr(event, "details", None)
    if not isinstance(details, dict):
        return []
    return render_script_details(details, indent="")


def render_pr_summary(
    old,
    new,
    output_format: str,
    *,
    current_spec: str | None = None,
    diff_command: str | None = None,
    baseline_source: str | None = None,
    current_source: str | None = None,
) -> str:
    if output_format == "json":
        return json.dumps(
            build_pr_summary_payload(old, new, baseline_source=baseline_source, current_source=current_source),
            ensure_ascii=False,
            indent=2,
        ) + "\n"
    if output_format == "text":
        return build_pr_summary_text(old, new)
    return build_pr_summary(old, new, current_spec=current_spec, diff_command=diff_command)
