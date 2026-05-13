from __future__ import annotations

from typing import Any

from .diff import diff_packages
from .package import SkillPackage
from .validate import run_golden_schema_tests, validate_package


def build_pr_summary(
    old: SkillPackage,
    new: SkillPackage,
    *,
    current_spec: str | None = None,
    diff_command: str | None = None,
) -> str:
    data = build_pr_summary_data(old, new)

    diff = data["diff"]

    lines = [
        "## Skill Change Summary",
        "",
        f"- Baseline: `{data['baseline']['name']}@{data['baseline']['version']}`",
        f"- Current: `{data['current']['name']}@{data['current']['version']}`",
        f"- Validation: {data['validation']['status']}",
        f"- Golden tests: {data['golden_tests']['status']}",
        f"- Risk: `{data['risk']}`",
        f"- Suggested version bump: `{data['suggested_bump']}`",
        "",
        "### Semantic Diff",
        "",
    ]
    lines.extend(f"- `{message}`" for message in diff["messages"])
    lines.extend(
        [
            "",
            "### Reproduce",
            "",
            "```bash",
            f"sit validate {current_spec or new.root}",
            f"sit test {current_spec or new.root}",
            diff_command or f"sit diff {old.root} {new.root}",
            "```",
        ]
    )
    return "\n".join(lines) + "\n"


def build_pr_summary_text(old: SkillPackage, new: SkillPackage) -> str:
    data = build_pr_summary_data(old, new)
    lines = [
        f"Baseline: {data['baseline']['name']}@{data['baseline']['version']}",
        f"Current: {data['current']['name']}@{data['current']['version']}",
        f"Validation: {data['validation']['status']}",
        f"Golden tests: {data['golden_tests']['status']}",
        f"Risk: {data['risk']}",
        f"Suggested version bump: {data['suggested_bump']}",
        "",
        "Semantic Diff:",
    ]
    lines.extend(data["diff"]["messages"])
    return "\n".join(lines) + "\n"


def build_pr_summary_data(old: SkillPackage, new: SkillPackage) -> dict[str, Any]:
    return build_pr_summary_payload(old, new)


def build_pr_summary_payload(
    old: SkillPackage,
    new: SkillPackage,
    *,
    baseline_source: str | None = None,
    current_source: str | None = None,
) -> dict[str, Any]:
    validation = validate_package(new)
    tests = run_golden_schema_tests(new) if validation.ok else None
    diff = diff_packages(old, new)

    return {
        "schema_version": "sit.pr_summary.v1",
        "baseline": {
            "name": old.name or "<unknown>",
            "version": old.version or "<unknown>",
            "root": str(old.root),
            "manifest": str(old.manifest_path),
            **({"source": baseline_source} if baseline_source is not None else {}),
        },
        "current": {
            "name": new.name or "<unknown>",
            "version": new.version or "<unknown>",
            "root": str(new.root),
            "manifest": str(new.manifest_path),
            **({"source": current_source} if current_source is not None else {}),
        },
        "validation": {
            "status": "pass" if validation.ok else "fail",
            "ok": validation.ok,
            "messages": validation.messages,
        },
        "golden_tests": {
            "status": _test_status(tests),
            "ok": tests.ok if tests is not None else None,
            "messages": tests.messages if tests is not None else [],
        },
        "risk": diff.risk,
        "suggested_bump": diff.suggested_bump,
        "diff": diff.to_dict(old, new, old_source=baseline_source, new_source=current_source),
    }


def _test_status(tests) -> str:
    if tests is None:
        return "skipped"
    return "pass" if tests.ok else "fail"
