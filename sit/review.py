from __future__ import annotations

from collections import Counter
from typing import Any

from .package import SkillPackage
from .script_summary import render_script_details
from .summary import build_pr_summary_payload


def build_skill_review_payload(
    old: SkillPackage,
    new: SkillPackage,
    *,
    baseline_source: str | None = None,
    current_source: str | None = None,
) -> dict[str, Any]:
    payload = build_pr_summary_payload(
        old,
        new,
        baseline_source=baseline_source,
        current_source=current_source,
    )
    payload["schema_version"] = "sit.review.v1"
    payload["review"] = _review_decision(payload)
    payload["artifact_summary"] = _artifact_summary(payload)
    return payload


def render_skill_review_markdown(
    data: dict[str, Any],
    *,
    current_spec: str | None = None,
    diff_command: str | None = None,
) -> str:
    baseline = data["baseline"]
    current = data["current"]
    validation = data["validation"]
    tests = data["golden_tests"]
    diff = data["diff"]
    review = data["review"]
    artifact_summary = data["artifact_summary"]

    current_ref = current_spec or current.get("source") or current["root"]
    diff_cmd = diff_command or _default_diff_command(baseline, current)

    lines = [
        "## SitHub Skill Review",
        "",
        f"Status: **{review['status']}**",
        f"Recommendation: **{review['recommendation']}**",
        "",
        "| Signal | Result |",
        "|---|---|",
        f"| Package | `{current['name']}@{current['version']}` |",
        f"| Baseline | `{baseline['name']}@{baseline['version']}` |",
        f"| Validation | **{validation['status']}** |",
        f"| Golden tests | **{tests['status']}** |",
        f"| Diff risk | **{diff['risk']}** |",
        f"| Suggested bump | `{diff['suggested_bump']}` |",
        "",
        "### Merge Guidance",
        "",
    ]
    lines.extend(f"- {reason}" for reason in review["reasons"])

    lines.extend(["", "### Changed Artifacts", ""])
    if artifact_summary["total_events"] == 0:
        lines.append("- No semantic changes detected.")
    else:
        for category in artifact_summary["categories"]:
            lines.append(f"- `{category['category']}`: {category['count']} event(s)")

    lines.extend(["", "### Semantic Diff", ""])
    events = diff.get("events", [])
    if not events:
        lines.append("- <none>")
    for event in events:
        lines.append(f"- `{event['message']}`")
        for detail in _event_detail_lines(event):
            lines.append(f"  - `{detail}`")

    if data.get("prompt_reference_summary"):
        lines.extend(["", "### Prompt/Reference Summary", ""])
        lines.extend(f"- `{summary}`" for summary in data["prompt_reference_summary"])

    lines.extend(
        [
            "",
            "### Reproduce",
            "",
            "```bash",
            f"sit validate {current_ref}",
            f"sit test {current_ref}",
            diff_cmd,
            "```",
        ]
    )

    return "\n".join(lines) + "\n"


def _review_decision(data: dict[str, Any]) -> dict[str, Any]:
    validation = data["validation"]
    tests = data["golden_tests"]
    risk = data["risk"]

    reasons: list[str] = []
    status = "approve"
    recommendation = "Safe to merge after normal review."

    if not validation["ok"]:
        status = "block"
        recommendation = "Block merge until validation passes."
        reasons.append("Validation failed; the Skill Package structure or schemas must be fixed first.")
    if tests["ok"] is False:
        status = "block"
        recommendation = "Block merge until golden tests pass."
        reasons.append("Golden tests failed; this change has an observed behavior regression or invalid expected output.")
    elif tests["ok"] is None:
        status = "block"
        recommendation = "Block merge until tests can run."
        reasons.append("Golden tests were skipped because validation failed.")

    if status != "block":
        if risk == "breaking-change":
            status = "needs-maintainer-review"
            recommendation = "Require maintainer approval and a major version bump before merge."
            reasons.append("Semantic diff contains a breaking change; downstream consumers may need migration.")
        elif risk == "review-required":
            status = "needs-review"
            recommendation = "Review semantic changes before merge."
            reasons.append("Semantic diff changed prompts, schemas, scripts, references, assets, or lifecycle metadata.")
        else:
            reasons.append("Validation and golden tests pass, and no semantic change was detected.")

    if not reasons:
        reasons.append("No review guidance generated.")

    return {
        "status": status,
        "recommendation": recommendation,
        "reasons": reasons,
    }


def _artifact_summary(data: dict[str, Any]) -> dict[str, Any]:
    events = data.get("diff", {}).get("events", [])
    counts = Counter(event.get("category", "unknown") for event in events)
    return {
        "total_events": len(events),
        "categories": [
            {"category": category, "count": count}
            for category, count in sorted(counts.items(), key=lambda item: (item[0], item[1]))
        ],
    }


def _default_diff_command(baseline: dict[str, Any], current: dict[str, Any]) -> str:
    baseline_source = baseline.get("source") or baseline["root"]
    current_source = current.get("source") or current["root"]
    if baseline_source and current_source:
        return f"sit diff {baseline_source} {current_source}"
    return "sit diff <baseline> <current>"


def _event_detail_lines(event: dict[str, Any]) -> list[str]:
    details = event.get("details")
    if not isinstance(details, dict):
        return []
    return render_script_details(details, indent="")

