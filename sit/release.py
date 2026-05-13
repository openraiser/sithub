from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from .errors import SitError
from .gate import VersionGateResult, check_release_gate_against_head, format_gate_failure
from .git import git_output, is_git_repo, run_git
from .package import SkillPackage, load_package
from .report import build_report
from .validate import run_golden_schema_tests, validate_package


def release_package(package: SkillPackage, bump: str, *, no_git_tag: bool = False, no_version_gate: bool = False) -> str:
    validation = validate_package(package)
    if not validation.ok:
        raise SitError("release blocked: validation failed")
    tests = run_golden_schema_tests(package)
    if not tests.ok:
        raise SitError("release blocked: golden tests failed")
    gate: VersionGateResult | None = None
    if not no_version_gate:
        gate = check_release_gate_against_head(package, bump)
        if not gate.ok:
            raise SitError(format_gate_failure(gate))

    old_version = package.version
    if old_version is None:
        raise SitError("release blocked: missing package version")

    new_version = bump_version(old_version, bump)
    manifest = dict(package.manifest)
    manifest["version"] = new_version
    package.manifest_path.write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")

    refreshed = load_package(package.root)
    _append_changelog(refreshed.root / "CHANGELOG.md", new_version, gate=gate, validation=validation, tests=tests)
    report_path = refreshed.report_dir() / f"release-v{new_version}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_build_release_report(refreshed, gate=gate, validation=validation, tests=tests), encoding="utf-8")

    tag_name = f"v{new_version}"
    tagged = False
    if not no_git_tag and is_git_repo(refreshed.root) and _has_head(refreshed.root):
        run_git(["add", str(refreshed.manifest_path), str(refreshed.root / "CHANGELOG.md"), str(report_path)], cwd=refreshed.root)
        run_git(["tag", "-a", tag_name, "-m", f"Release {tag_name}"], cwd=refreshed.root)
        tagged = True

    return f"Released {refreshed.name}@{new_version} (was {old_version}); report: {report_path}" + (
        f"; git tag: {tag_name}" if tagged else "; git tag: skipped"
    )


def bump_version(version: str, bump: str) -> str:
    parts = version.split(".")
    if len(parts) != 3:
        raise SitError(f"Expected semantic version MAJOR.MINOR.PATCH, got: {version}")
    try:
        major, minor, patch = (int(part) for part in parts)
    except ValueError as exc:
        raise SitError(f"Expected numeric semantic version, got: {version}") from exc

    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    if bump == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise SitError(f"Unknown release bump: {bump}")


def _append_changelog(path: Path, version: str, *, gate: VersionGateResult | None, validation, tests) -> None:
    existing = path.read_text(encoding="utf-8") if path.exists() else "# Changelog\n"
    lines = [
        f"## {version} - {date.today().isoformat()}",
        "",
        "- Released with `sit release`.",
        f"- Validation: {'pass' if validation.ok else 'fail'}.",
        f"- Golden tests: {'pass' if tests.ok else 'fail'}.",
    ]
    if gate is not None:
        lines.extend(
            [
                f"- Release risk: {gate.diff.risk if gate.diff is not None else 'not checked'}.",
                f"- Version gate: required {gate.required_bump}, actual {gate.actual_bump}.",
            ]
        )
        if gate.diff is not None:
            changes = _semantic_change_messages(gate)
            if changes:
                lines.append("- Semantic changes:")
                lines.extend(f"  - {message}" for message in changes[:5])
    else:
        lines.append("- Version gate: skipped.")
    entry = "\n" + "\n".join(lines) + "\n"
    path.write_text(existing.rstrip() + "\n" + entry, encoding="utf-8")


def _build_release_report(package: SkillPackage, *, gate: VersionGateResult | None, validation, tests) -> str:
    lines = [
        build_report(package).rstrip(),
        "",
        "## Release Summary",
        "",
        f"- Validation: {'pass' if validation.ok else 'fail'}",
        f"- Golden tests: {'pass' if tests.ok else 'fail'}",
    ]
    if gate is None:
        lines.append("- Version gate: skipped.")
    else:
        lines.extend(f"- {line}" for line in gate.summary_lines())
        if gate.diff is not None:
            lines.extend(["", "### Release Semantic Changes", ""])
            changes = _semantic_change_messages(gate)
            if changes:
                lines.extend(f"- `{message}`" for message in changes)
            else:
                lines.append("- `<none>`")
    return "\n".join(lines) + "\n"


def _semantic_change_messages(gate: VersionGateResult) -> list[str]:
    if gate.diff is None:
        return []
    return [
        event.message
        for event in gate.diff.events
        if event.category not in {"package", "risk"} and not event.message.startswith("MANIFEST changed version: ")
    ]


def _has_head(path: Path) -> bool:
    try:
        git_output(["rev-parse", "--verify", "HEAD"], cwd=path)
    except SitError:
        return False
    return True
