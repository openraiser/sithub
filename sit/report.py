from __future__ import annotations

from datetime import date
from html import escape
from typing import Any

from .diff import diff_packages
from .package import SkillPackage
from .validate import CheckResult, run_golden_schema_tests, validate_package


def build_report(
    package: SkillPackage,
    *,
    compare: SkillPackage | None = None,
    package_spec: str | None = None,
    diff_command: str | None = None,
) -> str:
    data = build_report_payload(package, compare=compare, package_spec=package_spec, diff_command=diff_command)
    return render_report_markdown(data)


def build_report_payload(
    package: SkillPackage,
    *,
    compare: SkillPackage | None = None,
    package_spec: str | None = None,
    diff_command: str | None = None,
) -> dict[str, Any]:
    validation = validate_package(package)
    test_result = run_golden_schema_tests(package) if validation.ok else None
    diff = diff_packages(compare, package) if compare is not None else None
    validate_command = f"python3 -m sit.cli validate {package_spec or package.root}"
    test_command = f"python3 -m sit.cli test {package_spec or package.root}"
    payload = {
        "schema_version": "sit.report.v1",
        "date": date.today().isoformat(),
        "package": _package_ref(package),
        "validation": validation.to_dict(),
        "golden_tests": _test_result_dict(test_result),
        "diff": diff.to_dict(compare, package) if diff is not None and compare is not None else None,
        "reproducibility": {
            "validate": validate_command,
            "test": test_command,
            "diff": diff_command or (f"python3 -m sit.cli diff {compare.root} {package.root}" if compare is not None else None),
        },
    }
    return payload


def render_report_markdown(data: dict[str, Any]) -> str:
    package = data["package"]
    validation = data["validation"]
    test_result = data["golden_tests"]

    lines: list[str] = [
        f"# {package['name'] or 'Skill Package'} {package['version'] or '<unknown>'} SIT Report",
        "",
        f"Date: {data['date']}",
        "",
        "## Package",
        "",
        f"- Name: `{package['name']}`",
        f"- Version: `{package['version']}`",
        f"- Root: `{package['root']}`",
        f"- Manifest: `{package['manifest']}`",
    ]

    if package.get("description"):
        lines.append(f"- Description: {package['description']}")

    lines.extend(
        [
            "",
            "## Validation",
            "",
            f"- Result: {validation['status']}",
        ]
    )
    lines.extend(f"- `{message}`" for message in validation["messages"])

    lines.extend(["", "## Golden Tests", ""])
    if test_result["status"] == "skipped":
        lines.append("- Skipped because validation failed.")
    else:
        lines.append(f"- Result: {test_result['status']}")
        lines.extend(f"- `{message}`" for message in test_result["messages"])

    if data["diff"] is not None:
        diff = data["diff"]
        lines.extend(
            [
                "",
                "## Diff",
                "",
                f"- Baseline: `{diff['old']['name']}@{diff['old']['version']}`",
                f"- Current: `{diff['new']['name']}@{diff['new']['version']}`",
            ]
        )
        lines.extend(f"- `{message}`" for message in diff["messages"])

    lines.extend(
        [
            "",
            "## Reproducibility",
            "",
            "- Re-run validation with:",
            f"  `{data['reproducibility']['validate']}`",
            "- Re-run golden schema tests with:",
            f"  `{data['reproducibility']['test']}`",
        ]
    )

    if data["reproducibility"]["diff"] is not None:
        lines.extend(
            [
                "- Re-run package diff with:",
                f"  `{data['reproducibility']['diff']}`",
            ]
        )

    return "\n".join(lines) + "\n"


def render_report_html(data: dict[str, Any]) -> str:
    package = data["package"]
    validation = data["validation"]
    tests = data["golden_tests"]
    diff = data.get("diff")
    risk = diff["risk"] if diff is not None else "no-compare"
    suggested_bump = diff["suggested_bump"] if diff is not None else "none"
    test_percent = _percent(tests.get("passed"), tests.get("total"))
    title = f"{package['name']} {package['version']} SitHub Report"

    html = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{escape(title)}</title>",
        "<style>",
        _html_css(),
        "</style>",
        "</head>",
        "<body>",
        '<main class="page">',
        '<section class="hero">',
        '<div class="hero-copy">',
        '<p class="eyebrow">SitHub semantic control report</p>',
        f"<h1>{escape(str(package['name']))}</h1>",
        f"<p class=\"subtitle\">Version {escape(str(package['version']))} - {escape(str(data['date']))}</p>",
        "</div>",
        '<div class="risk-panel">',
        '<span class="panel-label">Diff risk</span>',
        f'<strong class="risk {escape(_token_class(risk))}">{escape(risk)}</strong>',
        f"<span>Suggested bump: <b>{escape(suggested_bump)}</b></span>",
        "</div>",
        "</section>",
        '<section class="metrics" aria-label="Status metrics">',
        _metric("Validation", validation["status"], validation["ok"]),
        _metric("Golden Tests", tests["status"], tests.get("ok")),
        _metric("Cases Passed", _count_label(tests.get("passed"), tests.get("total")), tests.get("ok")),
        _metric("Version Bump", suggested_bump, None),
        "</section>",
        '<section class="band">',
        '<div class="section-head">',
        "<h2>Golden Test Progress</h2>",
        f"<span>{escape(_count_label(tests.get('passed'), tests.get('total')))}</span>",
        "</div>",
        '<div class="progress" aria-label="Golden test pass rate">',
        f'<div class="progress-fill" style="width: {test_percent}%"></div>',
        "</div>",
        "</section>",
        '<section class="columns">',
        _message_section("Validation", validation["messages"], "validation"),
        _message_section("Golden Tests", tests["messages"], "tests"),
        "</section>",
    ]

    if diff is not None:
        html.extend(
            [
                '<section class="band">',
                '<div class="section-head">',
                "<h2>Semantic Diff</h2>",
                f'<span class="risk {escape(_token_class(risk))}">{escape(risk)}</span>',
                "</div>",
                '<div class="timeline">',
                *_diff_items(diff["events"]),
                "</div>",
                "</section>",
            ]
        )

    html.extend(
        [
            '<section class="band">',
            '<div class="section-head">',
            "<h2>Reproduce</h2>",
            "<span>local commands</span>",
            "</div>",
            "<pre><code>",
            escape("\n".join(_repro_commands(data))),
            "</code></pre>",
            "</section>",
            "</main>",
            "</body>",
            "</html>",
        ]
    )
    return "\n".join(html) + "\n"


def _test_result_dict(result: CheckResult | None) -> dict[str, Any]:
    if result is None:
        return {
            "status": "skipped",
            "ok": None,
            "passed": None,
            "total": None,
            "summary": None,
            "messages": [],
        }
    passed, total, summary = _parse_summary(result.messages)
    return {
        "status": "pass" if result.ok else "fail",
        "ok": result.ok,
        "passed": passed,
        "total": total,
        "summary": summary,
        "messages": result.messages,
    }


def _parse_summary(messages: list[str]) -> tuple[int | None, int | None, str | None]:
    prefix = "SUMMARY "
    suffix = " golden cases passed"
    for message in reversed(messages):
        if not message.startswith(prefix) or not message.endswith(suffix):
            continue
        counts = message[len(prefix) : -len(suffix)]
        left, separator, right = counts.partition("/")
        if not separator:
            continue
        try:
            return int(left), int(right), message
        except ValueError:
            continue
    return None, None, None


def _package_ref(package: SkillPackage) -> dict[str, str | None]:
    return {
        "name": package.name or "<unknown>",
        "version": package.version or "<unknown>",
        "description": package.description,
        "root": str(package.root),
        "manifest": str(package.manifest_path),
    }


def _metric(title: str, value: str, ok: bool | None) -> str:
    state = "neutral" if ok is None else ("pass" if ok else "fail")
    return "\n".join(
        [
            '<div class="metric">',
            f"<span>{escape(title)}</span>",
            f'<strong class="{state}">{escape(str(value))}</strong>',
            "</div>",
        ]
    )


def _message_section(title: str, messages: list[str], section_class: str) -> str:
    lines = [f'<section class="band compact {section_class}">', f"<h2>{escape(title)}</h2>", '<ul class="message-list">']
    if messages:
        lines.extend(f"<li>{escape(message)}</li>" for message in messages)
    else:
        lines.append("<li>&lt;none&gt;</li>")
    lines.extend(["</ul>", "</section>"])
    return "\n".join(lines)


def _diff_items(events: list[dict[str, Any]]) -> list[str]:
    items: list[str] = []
    for event in events:
        severity = _token_class(str(event.get("severity", "info")))
        category = str(event.get("category", "event"))
        message = str(event.get("message", ""))
        items.append(
            "\n".join(
                [
                    f'<article class="diff-item {escape(severity)}">',
                    f"<span>{escape(category)}</span>",
                    f"<p>{escape(message)}</p>",
                    "</article>",
                ]
            )
        )
    return items


def _repro_commands(data: dict[str, Any]) -> list[str]:
    repro = data.get("reproducibility", {})
    return [command for command in (repro.get("validate"), repro.get("test"), repro.get("diff")) if command]


def _percent(passed: int | None, total: int | None) -> int:
    if not passed or not total:
        return 0
    return max(0, min(100, round((passed / total) * 100)))


def _count_label(passed: int | None, total: int | None) -> str:
    if passed is None or total is None:
        return "n/a"
    return f"{passed}/{total}"


def _token_class(value: str) -> str:
    return "".join(char if char.isalnum() else "-" for char in value.lower()).strip("-") or "neutral"


def _html_css() -> str:
    return """
:root {
  color-scheme: light;
  --ink: #17202a;
  --muted: #5b6472;
  --line: #d9dee7;
  --paper: #f7f8fb;
  --panel: #ffffff;
  --blue: #2457d6;
  --green: #147d64;
  --red: #b42318;
  --amber: #a05a00;
  --violet: #7047a8;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  color: var(--ink);
  background: var(--paper);
  letter-spacing: 0;
}
.page {
  width: min(1180px, calc(100% - 32px));
  margin: 0 auto;
  padding: 28px 0 48px;
}
.hero {
  min-height: 240px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(280px, 380px);
  gap: 24px;
  align-items: stretch;
  padding: 32px;
  color: #fff;
  background:
    linear-gradient(120deg, rgba(23, 32, 42, .88), rgba(36, 87, 214, .74)),
    url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='960' height='360' viewBox='0 0 960 360'%3E%3Crect width='960' height='360' fill='%23222f3e'/%3E%3Cg fill='none' stroke='%23ffffff' stroke-opacity='.18'%3E%3Cpath d='M80 270 C180 170 260 210 350 110 S560 80 660 160 790 280 900 110' stroke-width='5'/%3E%3Cpath d='M60 100 H900 M60 180 H900 M60 260 H900'/%3E%3Cpath d='M160 40 V320 M360 40 V320 M560 40 V320 M760 40 V320'/%3E%3C/g%3E%3Cg fill='%23ffffff' fill-opacity='.92'%3E%3Ccircle cx='350' cy='110' r='9'/%3E%3Ccircle cx='660' cy='160' r='9'/%3E%3Ccircle cx='900' cy='110' r='9'/%3E%3C/g%3E%3C/svg%3E");
  background-size: cover;
  background-position: center;
  border-radius: 8px;
}
.hero h1 {
  margin: 0;
  font-size: 42px;
  line-height: 1.05;
  overflow-wrap: anywhere;
}
.eyebrow, .subtitle {
  margin: 0 0 12px;
  color: rgba(255,255,255,.78);
}
.subtitle { margin-top: 14px; }
.risk-panel {
  align-self: end;
  display: grid;
  gap: 10px;
  min-height: 150px;
  padding: 20px;
  background: rgba(255,255,255,.12);
  border: 1px solid rgba(255,255,255,.24);
  border-radius: 8px;
}
.panel-label { color: rgba(255,255,255,.76); }
.risk-panel strong { font-size: 28px; line-height: 1.1; }
.metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
  margin: 18px 0;
}
.metric, .band {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
}
.metric {
  min-height: 112px;
  padding: 18px;
  display: grid;
  align-content: space-between;
}
.metric span, .section-head span, .diff-item span {
  color: var(--muted);
  font-size: 13px;
}
.metric strong {
  font-size: 25px;
  overflow-wrap: anywhere;
}
.pass { color: var(--green); }
.fail, .breaking, .breaking-change { color: var(--red); }
.changed, .review-required { color: var(--amber); }
.neutral, .info, .no-change, .no-compare { color: var(--blue); }
.band {
  padding: 22px;
  margin-top: 18px;
}
.compact { margin-top: 0; }
.section-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: center;
  margin-bottom: 16px;
}
h2 {
  margin: 0;
  font-size: 20px;
}
.progress {
  height: 14px;
  overflow: hidden;
  background: #e9edf3;
  border-radius: 999px;
}
.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--green), var(--blue));
}
.columns {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 18px;
  margin-top: 18px;
}
.message-list {
  margin: 14px 0 0;
  padding-left: 18px;
}
.message-list li {
  margin: 8px 0;
  line-height: 1.45;
  overflow-wrap: anywhere;
}
.timeline {
  display: grid;
  gap: 10px;
}
.diff-item {
  border-left: 4px solid var(--blue);
  padding: 12px 14px;
  background: #fbfcff;
  border-radius: 6px;
}
.diff-item.breaking { border-color: var(--red); }
.diff-item.changed { border-color: var(--amber); }
.diff-item p {
  margin: 5px 0 0;
  overflow-wrap: anywhere;
}
pre {
  margin: 0;
  padding: 16px;
  overflow: auto;
  background: #15202b;
  color: #edf3f8;
  border-radius: 8px;
}
code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 13px;
}
@media (max-width: 820px) {
  .hero, .metrics, .columns {
    grid-template-columns: 1fr;
  }
  .hero {
    padding: 24px;
  }
  .hero h1 {
    font-size: 32px;
  }
}
""".strip()
