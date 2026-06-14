from __future__ import annotations

import re
import sys
from pathlib import Path

from .errors import SitError
from .gate import VersionGateResult, check_version_gate, invalidate_gate_cache
from .git import git_output, run_git
from .ref import load_compare_package, load_staged_package


SIT_SUBCOMMANDS = {
    "init", "status", "info", "doctor", "deps", "onboard", "standardize",
    "validate", "test", "diff", "report", "ci-summary", "pr-summary",
    "review", "release", "commit", "undo", "install-hooks", "_hook-pre-commit", "help",
}


def is_git_passthrough(argv: list[str]) -> bool:
    """Return True if argv[0] should be passed through to git."""
    if not argv:
        return False
    cmd = argv[0]
    if cmd.startswith("-"):
        return False
    if cmd in SIT_SUBCOMMANDS:
        return False
    return True


def check_staged_version_gate(package_spec: str) -> VersionGateResult:
    try:
        with load_compare_package(package_spec, "HEAD..STAGED") as (staged, baseline):
            if baseline is None:
                return staged_gate_skipped(staged)
            return check_version_gate(baseline, staged)
    except SitError as exc:
        if "git archive failed for HEAD" not in str(exc):
            raise
        with load_staged_package(package_spec) as staged:
            return staged_gate_skipped(staged)


def staged_gate_skipped(staged) -> VersionGateResult:
    return VersionGateResult(
        checked=False,
        ok=True,
        required_bump="none",
        actual_bump="none",
        baseline_version=None,
        current_version=staged.version,
        diff=None,
        message="version gate skipped: no Git HEAD baseline",
    )


def git_hook_path(repo_root: Path, hook_name: str) -> Path:
    try:
        hook = git_output(["rev-parse", "--git-path", f"hooks/{hook_name}"], cwd=repo_root)
    except SitError:
        return repo_root / ".git" / "hooks" / hook_name
    hook_path = Path(hook)
    if not hook_path.is_absolute():
        hook_path = repo_root / hook_path
    return hook_path


def hook_package_arg(repo_root: Path, package_root: Path) -> str:
    try:
        return package_root.relative_to(repo_root).as_posix() or "."
    except ValueError:
        return package_root.as_posix()


def print_git_failure_hint(command: str) -> None:
    hints = {
        "commit": "sit commit runs Skill validation, tests, and version gates before committing.",
        "push": "run `sit status` or `sit review main..HEAD` before pushing a Skill change.",
        "add": "run `git status --short` to inspect paths, then `sit diff --staged` after staging.",
        "pull": "after pulling, run `sit validate` and `sit test` if Skill files changed.",
    }
    hint = hints.get(command, "this was passed through to Git; rerun with `git " + command + "` for raw Git behavior.")
    print(f"sit: hint: {hint}", file=sys.stderr)


def print_add_gate_hints() -> None:
    """After staging files, give a cheap semantic hint without full package diff."""
    try:
        staged = git_output(["diff", "--cached", "--name-only"])
    except Exception:
        return
    if not staged:
        return

    paths = staged.splitlines()
    semantic = [path for path in paths if is_semantic_staged_path(path)]
    if semantic:
        print(f"staged {len(paths)} file(s); {len(semantic)} semantic path(s) may affect Skill behavior")
        for path in semantic[:4]:
            print(f"  {path}")
        if len(semantic) > 4:
            print(f"  ... and {len(semantic) - 4} more")
        print("run `sit diff --staged` for exact semantic impact")
    else:
        print(f"staged {len(paths)} file(s); no obvious Skill semantic paths")


def is_semantic_staged_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    if normalized in {"skill.yaml", "skill.yml", "deps.yaml", "deps.yml"}:
        return True
    if normalized.endswith("/skill.yaml") or normalized.endswith("/skill.yml"):
        return True
    return any(
        part in normalized.split("/")
        for part in {"prompts", "schemas", "tests", "scripts", "assets", "references"}
    )


def auto_bump_version(package, bump: str) -> None:
    """Bump version in skill.yaml and stage the change."""
    manifest = package.manifest_path
    text = manifest.read_text(encoding="utf-8")
    old_version = package.version or "0.0.0"
    new_version = compute_bumped_version(old_version, bump)
    new_text = re.sub(
        r"^(version:\s*).*$",
        f"\\g<1>{new_version}",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    manifest.write_text(new_text, encoding="utf-8")
    run_git(["add", str(manifest)], check=False)
    invalidate_gate_cache()
    print(f"auto-bumped version: {old_version} -> {new_version}")


def undo_bump_on_commit_failure(package) -> None:
    """Rollback the auto-bumped skill.yaml when git commit fails."""
    try:
        run_git(["checkout", "--", str(package.manifest_path)], check=False)
        print("auto-bump rolled back (commit failed)")
    except Exception:
        pass


def should_run_golden_tests(package) -> bool:
    """Check if staged files touch prompts/schemas/tests/scripts (smart-skip)."""
    try:
        staged = git_output(["diff", "--cached", "--name-only"], cwd=package.root)
    except Exception:
        return True
    if not staged:
        return True

    trigger_prefixes = ("prompts/", "schemas/", "tests/", "scripts/")
    trigger_files = ("skill.yaml",)
    for path in staged.splitlines():
        path_lower = path.lower()
        if any(path_lower.startswith(prefix) for prefix in trigger_prefixes):
            return True
        if path_lower in trigger_files:
            return True
        if path_lower.endswith("skill.md"):
            return True
    return False


def compute_bumped_version(version: str, bump: str) -> str:
    parts = version.split(".")
    if len(parts) != 3:
        parts = ["0", "0", "0"]
    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def print_gate_hint(gate) -> None:
    """Print a short actionable hint when the gate blocks."""
    if gate.diff is not None:
        events = [e for e in gate.diff.events if e.category not in {"package", "risk"}]
        if events:
            print(f"hint: {len(events)} semantic changes detected. Use --bump {gate.required_bump} to auto-fix.")


def git_short_hash(path) -> str:
    """Get the current HEAD short hash."""
    try:
        return git_output(["rev-parse", "--short", "HEAD"], cwd=path)
    except Exception:
        return "??????"
