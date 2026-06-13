from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .errors import SitError

GIT_NOISE_RE = re.compile(
    r"^error: non-monotonic index |"
    r"^warning: ignoring broken ref |"
    r"^Auto packing the repository"
)


def _filter_stderr(stderr: str) -> str:
    """Remove known git infrastructure noise from stderr."""
    if not stderr:
        return stderr
    lines = [line for line in stderr.splitlines() if not GIT_NOISE_RE.match(line)]
    return "\n".join(lines)


def run_git(args: list[str], *, cwd: Path | None = None, check: bool = True) -> int:
    command = ["git", *args]
    try:
        completed = subprocess.run(command, cwd=cwd, check=False, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise SitError("git executable not found") from exc

    filtered_stderr = _filter_stderr(completed.stderr)
    if filtered_stderr:
        import sys
        sys.stderr.write(filtered_stderr)
        if not filtered_stderr.endswith("\n"):
            sys.stderr.write("\n")

    if completed.stdout:
        import sys
        sys.stdout.write(completed.stdout)
        if not completed.stdout.endswith("\n"):
            sys.stdout.write("\n")

    if check and completed.returncode != 0:
        raise SitError(f"git command failed: {' '.join(command)}")
    return completed.returncode


def git_output(args: list[str], *, cwd: Path | None = None) -> str:
    command = ["git", *args]
    try:
        completed = subprocess.run(command, cwd=cwd, check=False, text=True, capture_output=True)
    except FileNotFoundError as exc:
        raise SitError("git executable not found") from exc

    if completed.returncode != 0:
        message = _filter_stderr(completed.stderr).strip() or completed.stdout.strip()
        raise SitError(f"git command failed: {' '.join(command)}" + (f": {message}" if message else ""))
    return completed.stdout.strip()


def is_git_repo(path: Path) -> bool:
    try:
        git_output(["rev-parse", "--is-inside-work-tree"], cwd=path)
    except SitError:
        return False
    return True


def git_root(path: Path) -> Path | None:
    try:
        output = git_output(["rev-parse", "--show-toplevel"], cwd=path)
    except SitError:
        return None
    return Path(output).resolve()


def has_pack_errors(path: Path) -> list[str]:
    """Check for git pack index corruption (e.g., non-monotonic index from macOS resource forks)."""
    command = ["git", "fsck", "--no-full", "--no-dangling"]
    try:
        completed = subprocess.run(command, cwd=path, check=False, text=True, capture_output=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    combined = completed.stderr + completed.stdout
    return parse_pack_issues(combined)


def parse_pack_issues(output: str) -> list[str]:
    """Parse git fsck output for known pack issues. Separated for testability."""
    issues: list[str] = []
    if "non-monotonic" in output:
        issues.append("Pack index corruption detected (non-monotonic index). Fix with: git repack -a -d")
    if "._pack-" in output:
        issues.append("macOS resource fork files (._*) in pack directory. Fix with: find .git -name '._*' -delete && git repack -a -d")
    return issues
