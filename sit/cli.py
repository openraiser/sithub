from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .agent_config import setup_agent_config, render_agent_setup_text
from .ci import render_ci_summary
from .deps import check_dependencies, dependency_warnings_for_commit, render_deps_text
from .doctor import build_doctor_payload, render_doctor_text
from .diff import diff_packages
from .errors import SitError
from .gate import check_version_gate, check_version_gate_against_head, format_gate_failure, invalidate_gate_cache
from .git import git_output, git_root, run_git
from .init import init_package
from .info import build_info_payload, render_info_text
from .onboard import onboard_existing_skill, render_onboard_text, render_standardize_text, standardize_skill_package
from .package import load_package
from .release import release_package
from .ref import load_compare_package, load_package_pair, parse_git_range
from .report import build_report, build_report_payload, render_report_html, render_report_markdown
from .review import build_skill_review_payload, render_skill_review_markdown
from .script_summary import render_script_details
from .summary import build_pr_summary, build_pr_summary_payload, build_pr_summary_text
from .validate import build_test_payload, run_golden_schema_tests, validate_package

GIT_PASSTHROUGH_COMMANDS = {"add", "push", "pull", "branch", "checkout", "log"}

SIT_SUBCOMMANDS = {
    "init", "status", "info", "doctor", "deps", "onboard", "standardize",
    "validate", "test", "diff", "report", "ci-summary", "pr-summary",
    "review", "release", "commit", "undo", "install-hooks", "_hook-pre-commit", "help",
}


def _is_git_passthrough(argv: list[str]) -> bool:
    """Return True if argv[0] should be passed through to git.

    Strategy: anything that is NOT a known sit subcommand and doesn't start
    with '-' is treated as a git command. This makes sit a full drop-in git
    wrapper with noise filtering.
    """
    if not argv:
        return False
    cmd = argv[0]
    if cmd.startswith("-"):
        return False
    if cmd in SIT_SUBCOMMANDS:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "help":
        parser = _build_parser()
        parser.print_help()
        return 0
    if argv and argv[0] == "_hook-pre-commit":
        parser = argparse.ArgumentParser(prog="sit _hook-pre-commit")
        parser.add_argument("--package", default=".")
        args = parser.parse_args(argv[1:])
        try:
            return cmd_hook_pre_commit(args)
        except SitError as exc:
            print(f"sit: error: {exc}", file=sys.stderr)
            return 2
    if _is_git_passthrough(argv):
        return cmd_git(argparse.Namespace(git_command=argv[0], git_args=argv[1:]))

    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        return args.func(args)
    except SitError as exc:
        print(f"sit: error: {exc}", file=sys.stderr)
        return 2


def cmd_status(args: argparse.Namespace) -> int:
    package = load_package(args.package)

    print(f"Package: {package.name or '<unknown>'}")
    print(f"Version: {package.version or '<unknown>'}")
    if package.description:
        print(f"Description: {package.description}")
    print(f"Root: {package.root}")
    print(f"Manifest: {package.manifest_path}")
    print()

    _print_path_group("Prompts", package.prompt_paths())
    _print_path_group("Schemas", package.schema_paths())
    _print_path_group("Tests", package.test_paths())
    report_dir = package.report_dir()
    marker = "exists" if report_dir.exists() else "missing"
    print(f"Reports: {marker} {report_dir}")
    print()
    _print_status_gate_preview(package, verbose=getattr(args, "verbose", False))
    return 0


def _print_status_gate_preview(package, *, verbose: bool = False) -> None:
    print("Gate preview:")
    try:
        gate = check_version_gate_against_head(package)
    except SitError as exc:
        print(f"  unavailable: {exc}")
        return
    if not gate.checked:
        print(f"  {gate.message}")
        return
    state = "pass" if gate.ok else "block"
    print(f"  {state}: required={gate.required_bump}, actual={gate.actual_bump}, risk={gate.diff.risk if gate.diff else 'unknown'}")
    if verbose:
        print(f"  message: {gate.message.splitlines()[0]}")
    if gate.diff is not None:
        events = [
            event for event in gate.diff.events
            if event.category not in {"package", "risk"} and not event.message.startswith("MANIFEST changed version:")
        ]
        if events:
            shown = events if verbose else events[:3]
            for event in shown:
                print(f"  - {event.message}")
            if not verbose and len(events) > 3:
                print(f"  - ... and {len(events) - 3} more")


def cmd_info(args: argparse.Namespace) -> int:
    package = load_package(args.package)
    payload = build_info_payload(package)
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_info_text(payload), end="")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    payload = build_doctor_payload(args.package)
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_doctor_text(payload), end="")
    return 0 if payload["ok"] else 1


def cmd_deps_check(args: argparse.Namespace) -> int:
    package = load_package(args.package)
    payload = check_dependencies(package)
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_deps_text(payload), end="")
    return 0 if payload["ok"] else 1


def cmd_onboard(args: argparse.Namespace) -> int:
    root = Path(args.path).expanduser().resolve()
    has_skill_yaml = (root / "skill.yaml").exists()
    has_skill_md = (root / "SKILL.md").exists()

    # --agent mode: add agent config to any package
    if args.agent:
        if has_skill_yaml:
            # Already a sit package — just add agent config
            agent_result = setup_agent_config(root, force=args.force)
            if args.format == "json":
                print(json.dumps(agent_result.to_dict(), ensure_ascii=False, indent=2))
            else:
                print(render_agent_setup_text(agent_result), end="")
            return 0
        if has_skill_md:
            # SKILL.md project — full onboard + agent config
            result = onboard_existing_skill(
                args.path, name=args.name, version=args.version,
                remote=args.remote, no_git=args.no_git, force=args.force,
            )
            agent_result = setup_agent_config(result.root, force=args.force)
            result.created.extend(agent_result.created)
            result.updated.extend(agent_result.updated)
            result.skipped.extend(agent_result.skipped)
            if args.format == "json":
                print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
            else:
                print(render_onboard_text(result), end="")
            return 0 if result.ok else 1
        # No skill.yaml or SKILL.md — agent config only
        root.mkdir(parents=True, exist_ok=True)
        agent_result = setup_agent_config(root, force=args.force)
        if args.format == "json":
            print(json.dumps(agent_result.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(render_agent_setup_text(agent_result), end="")
        return 0

    # Standard onboard (requires SKILL.md)
    result = onboard_existing_skill(
        args.path, name=args.name, version=args.version,
        remote=args.remote, no_git=args.no_git, force=args.force,
    )
    if args.format == "json":
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(render_onboard_text(result), end="")
    return 0 if result.ok else 1


def cmd_standardize(args: argparse.Namespace) -> int:
    result = standardize_skill_package(
        args.path,
        name=args.name,
        version=args.version,
        remote=args.remote,
        no_git=args.no_git,
        force=args.force,
    )
    if args.format == "json":
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(render_standardize_text(result), end="")
    return 0 if result.ok else 1


def cmd_validate(args: argparse.Namespace) -> int:
    package = load_package(args.package)
    result = validate_package(package)
    for message in result.messages:
        print(message)
    return 0 if result.ok else 1


def cmd_test(args: argparse.Namespace) -> int:
    package = load_package(args.package)
    if args.format == "json":
        payload = build_test_payload(package, run_actual=args.run, runner=args.runner, timeout=args.timeout)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["validation"]["ok"] and payload["golden_tests"]["ok"] else 1

    validation = validate_package(package)
    if not validation.ok:
        for message in validation.messages:
            print(message)
        return 1

    result = run_golden_schema_tests(package, run_actual=args.run, runner=args.runner, timeout=args.timeout)
    print("Skill Tests")
    print(f"Result: {'pass' if result.ok else 'fail'}")
    print()
    for message in result.messages:
        print(message)
    return 0 if result.ok else 1


def cmd_diff(args: argparse.Namespace) -> int:
    if args.staged:
        return _cmd_diff_staged(args)
    git_range = parse_git_range(args.old) if args.new is None else None
    with load_package_pair(args.old, args.new) as (old, new):
        result = diff_packages(old, new)
        print(
            _render_diff(
                result,
                old,
                new,
                args.format,
                old_source=git_range.old if git_range else args.old,
                new_source=git_range.new if git_range else args.new,
                show_prompt_diff=args.prompt,
            ),
            end="",
        )
    return 0


def _cmd_diff_staged(args: argparse.Namespace) -> int:
    """Compare HEAD with the Git index snapshot, excluding unstaged edits."""
    staged_range = "HEAD..STAGED"
    with load_package_pair(staged_range) as (old, new):
        result = diff_packages(old, new)
        print(
            _render_diff(
                result,
                old,
                new,
                args.format,
                old_source="HEAD",
                new_source="STAGED",
                show_prompt_diff=args.prompt,
            ),
            end="",
        )
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    compare_range = parse_git_range(args.compare)
    with load_compare_package(args.package, args.compare) as (package, compare):
        package_spec = "." if compare_range else None
        diff_command = f"python3 -m sit.cli diff {args.compare}" if compare_range else None
        if args.format == "json":
            content = json.dumps(
                build_report_payload(
                    package,
                    compare=compare,
                    package_spec=package_spec,
                    diff_command=diff_command,
                    package_source=compare_range.new if compare_range else None,
                    compare_source=compare_range.old if compare_range else None,
                ),
                ensure_ascii=False,
                indent=2,
            ) + "\n"
        elif args.format == "html":
            content = render_report_html(
                build_report_payload(
                    package,
                    compare=compare,
                    package_spec=package_spec,
                    diff_command=diff_command,
                    package_source=compare_range.new if compare_range else None,
                    compare_source=compare_range.old if compare_range else None,
                )
            )
        else:
            content = build_report(
                package,
                compare=compare,
                package_spec=package_spec,
                diff_command=diff_command,
                package_source=compare_range.new if compare_range else None,
                compare_source=compare_range.old if compare_range else None,
            )

    if args.output:
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding="utf-8")
        print(f"Wrote report: {output}")
    else:
        print(content, end="")
    return 0


def cmd_ci_summary(args: argparse.Namespace) -> int:
    package_spec_arg = args.package_dir or args.package
    compare_spec = _ci_compare_spec(args)
    compare_range = parse_git_range(compare_spec)
    with load_compare_package(package_spec_arg, compare_spec) as (package, compare):
        package_spec = package_spec_arg if args.package_dir else ("." if compare_range else None)
        diff_command = f"python3 -m sit.cli report {package_spec_arg} --compare {compare_spec}" if compare_range else None
        payload = build_report_payload(
            package,
            compare=compare,
            package_spec=package_spec,
            diff_command=diff_command,
            package_source=compare_range.new if compare_range else None,
            compare_source=compare_range.old if compare_range else None,
        )
        content = render_ci_summary(payload)

    if args.artifact_dir:
        _write_ci_artifacts(Path(args.artifact_dir).expanduser().resolve(), payload, content)

    if args.output:
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        if args.append:
            with output.open("a", encoding="utf-8") as handle:
                handle.write(content)
        else:
            output.write_text(content, encoding="utf-8")
        print(f"Wrote CI summary: {output}")
    else:
        print(content, end="")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    root = init_package(args.name, path=args.path, no_git=args.no_git)
    print(f"Initialized Skill Package: {root}")
    return 0


def cmd_install_hooks(args: argparse.Namespace) -> int:
    package = load_package(args.package)
    repo_root = git_root(package.root)
    if repo_root is None:
        raise SitError("install-hooks requires a Git repository")

    hook_path = _git_hook_path(repo_root, "pre-commit")
    hook_path.parent.mkdir(parents=True, exist_ok=True)
    if hook_path.exists() and "Generated by sit install-hooks" not in hook_path.read_text(encoding="utf-8", errors="replace") and not args.force:
        raise SitError(f"pre-commit hook already exists: {hook_path}; use --force to replace it")

    package_arg = _hook_package_arg(repo_root, package.root)
    hook = "\n".join(
        [
            "#!/bin/sh",
            "# Generated by sit install-hooks",
            f"sit _hook-pre-commit --package {json.dumps(package_arg)}",
            "",
        ]
    )
    hook_path.write_text(hook, encoding="utf-8")
    hook_path.chmod(hook_path.stat().st_mode | 0o111)
    print(f"Installed sit pre-commit hook: {hook_path}")
    print("Direct git commit will run sit staged validation, tests, and version gate.")
    return 0


def cmd_hook_pre_commit(args: argparse.Namespace) -> int:
    try:
        with load_compare_package(args.package, "HEAD..STAGED") as (staged, baseline):
            validation = validate_package(staged)
            if not validation.ok:
                for message in validation.messages:
                    print(message)
                raise SitError("pre-commit blocked: validation failed")

            tests = run_golden_schema_tests(staged)
            if not tests.ok:
                for message in tests.messages:
                    print(message)
                raise SitError("pre-commit blocked: golden tests failed")

            if baseline is not None:
                gate = check_version_gate(baseline, staged)
                if not gate.ok:
                    print(gate.message)
                    _print_gate_hint(gate)
                    raise SitError(f"pre-commit blocked: {format_gate_failure(gate)}")
                print(f"pre-commit gate: {gate.message.splitlines()[0]}")
            else:
                print("pre-commit gate: skipped (no baseline)")
    except SitError as exc:
        if "git archive failed for HEAD" not in str(exc):
            raise
        package = load_package(args.package)
        validation = validate_package(package)
        if not validation.ok:
            for message in validation.messages:
                print(message)
            raise SitError("pre-commit blocked: validation failed")
        tests = run_golden_schema_tests(package)
        if not tests.ok:
            for message in tests.messages:
                print(message)
            raise SitError("pre-commit blocked: golden tests failed")
        print("pre-commit gate: skipped (no HEAD baseline)")
    return 0


def _git_hook_path(repo_root: Path, hook_name: str) -> Path:
    try:
        hook = git_output(["rev-parse", "--git-path", f"hooks/{hook_name}"], cwd=repo_root)
    except SitError:
        return repo_root / ".git" / "hooks" / hook_name
    hook_path = Path(hook)
    if not hook_path.is_absolute():
        hook_path = repo_root / hook_path
    return hook_path


def _hook_package_arg(repo_root: Path, package_root: Path) -> str:
    try:
        return package_root.relative_to(repo_root).as_posix() or "."
    except ValueError:
        return package_root.as_posix()


def cmd_git(args: argparse.Namespace) -> int:
    result = run_git([args.git_command, *args.git_args], check=False)
    if args.git_command == "add" and result == 0:
        if not getattr(args, "quiet", False):
            _print_add_gate_hints()
    elif result != 0 and not getattr(args, "quiet", False):
        _print_git_failure_hint(args.git_command)
    return result


def _print_git_failure_hint(command: str) -> None:
    hints = {
        "commit": "sit commit runs Skill validation, tests, and version gates before committing.",
        "push": "run `sit status` or `sit review main..HEAD` before pushing a Skill change.",
        "add": "run `git status --short` to inspect paths, then `sit diff --staged` after staging.",
        "pull": "after pulling, run `sit validate` and `sit test` if Skill files changed.",
    }
    hint = hints.get(command, "this was passed through to Git; rerun with `git " + command + "` for raw Git behavior.")
    print(f"sit: hint: {hint}", file=sys.stderr)


def _print_add_gate_hints() -> None:
    """After staging files, give a cheap semantic hint without full package diff."""
    try:
        staged = git_output(["diff", "--cached", "--name-only"])
    except Exception:
        return
    if not staged:
        return

    paths = staged.splitlines()
    semantic = [path for path in paths if _is_semantic_staged_path(path)]
    if semantic:
        print(f"staged {len(paths)} file(s); {len(semantic)} semantic path(s) may affect Skill behavior")
        for path in semantic[:4]:
            print(f"  {path}")
        if len(semantic) > 4:
            print(f"  ... and {len(semantic) - 4} more")
        print("run `sit diff --staged` for exact semantic impact")
    else:
        print(f"staged {len(paths)} file(s); no obvious Skill semantic paths")


def _is_semantic_staged_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    if normalized in {"skill.yaml", "skill.yml", "deps.yaml", "deps.yml"}:
        return True
    if normalized.endswith("/skill.yaml") or normalized.endswith("/skill.yml"):
        return True
    return any(
        part in normalized.split("/")
        for part in {"prompts", "schemas", "tests", "scripts", "assets", "references"}
    )


def cmd_commit(args: argparse.Namespace) -> int:
    if not args.no_verify:
        package = load_package(args.package)
        validation = validate_package(package)
        if not validation.ok:
            for message in validation.messages:
                print(message)
            raise SitError("commit blocked: validation failed")

        if not args.no_test:
            if not _should_run_golden_tests(package):
                if not getattr(args, "quiet", False):
                    print("golden tests: skipped (no prompt/schema/test/script changes)")
            else:
                tests = run_golden_schema_tests(package)
                if not tests.ok:
                    for message in tests.messages:
                        print(message)
                    raise SitError("commit blocked: golden tests failed")

        if not args.no_version_gate:
            bump_snapshot: str | None = None
            if args.bump:
                gate_pre = check_version_gate_against_head(package)
                if not gate_pre.ok:
                    bump_snapshot = package.manifest_path.read_text(encoding="utf-8")
                    _auto_bump_version(package, args.bump)
                    package = load_package(args.package)

            gate = check_version_gate_against_head(package)
            if not gate.ok:
                if bump_snapshot is not None:
                    package.manifest_path.write_text(bump_snapshot, encoding="utf-8")
                    run_git(["checkout", "--", str(package.manifest_path)], check=False)
                print(gate.message)
                if args.bump:
                    print(f"sit: version bump '{args.bump}' was insufficient for the semantic changes detected.")
                else:
                    _print_gate_hint(gate)
                raise SitError(format_gate_failure(gate))
            if not getattr(args, "quiet", False):
                print(f"gate: {gate.message.splitlines()[0]}")
        else:
            if not getattr(args, "quiet", False):
                test_count = len(validation.messages)
                print(f"validated ({test_count} checks passed)")

        for warning in dependency_warnings_for_commit(package):
            print(f"WARN {warning}")

    git_args = ["commit"]
    if args.message:
        git_args.extend(["-m", args.message])
    git_args.extend(args.git_args)
    result = run_git(git_args, check=False)

    if result != 0 and args.bump and not args.no_verify and not args.no_version_gate:
        package = load_package(args.package)
        _undo_bump_on_commit_failure(package)

    if result == 0 and not args.no_verify and not getattr(args, "quiet", False):
        package = load_package(args.package)
        short_hash = _git_short_hash(package.root)
        version_info = f"{package.version}" if package.version else ""
        print(f"committed {short_hash} ({version_info})")

    return result


def _auto_bump_version(package, bump: str) -> None:
    """Bump version in skill.yaml and stage the change."""
    import re
    manifest = package.manifest_path
    text = manifest.read_text(encoding="utf-8")
    old_version = package.version or "0.0.0"
    new_version = _compute_bumped_version(old_version, bump)
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


def _undo_bump_on_commit_failure(package) -> None:
    """Rollback the auto-bumped skill.yaml when git commit fails."""
    try:
        run_git(["checkout", "--", str(package.manifest_path)], check=False)
        print("auto-bump rolled back (commit failed)")
    except Exception:
        pass


def _should_run_golden_tests(package) -> bool:
    """Check if staged files touch prompts/schemas/tests/scripts (smart-skip)."""
    from .git import git_output as _git_out
    try:
        staged = _git_out(["diff", "--cached", "--name-only"], cwd=package.root)
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


def cmd_undo(args: argparse.Namespace) -> int:
    """Undo the last commit (soft reset by default, preserving changes in working tree)."""
    from .git import git_output as _git_out
    package_path = Path(args.package).expanduser().resolve()
    reset_cwd = package_path.parent if package_path.is_file() else package_path

    try:
        last_msg = _git_out(["log", "-1", "--format=%s"], cwd=reset_cwd)
    except SitError:
        raise SitError("cannot undo: no commits found")

    if args.dry_run:
        mode = "hard" if args.hard else "soft"
        print(f"would undo commit: {last_msg}")
        print(f"mode: {mode}")
        if not args.hard:
            print("changes would be preserved in working tree")
        return 0

    mode = "--hard" if args.hard else "--soft"
    result = run_git(["reset", mode, "HEAD~1"], cwd=reset_cwd, check=False)
    if result != 0:
        raise SitError("git reset failed")

    print(f"undid commit: {last_msg}")
    if not args.hard:
        print("changes preserved in working tree (use --hard to discard)")
    return 0


def _compute_bumped_version(version: str, bump: str) -> str:
    parts = version.split(".")
    if len(parts) != 3:
        parts = ["0", "0", "0"]
    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
    if bump == "major":
        return f"{major + 1}.0.0"
    elif bump == "minor":
        return f"{major}.{minor + 1}.0"
    else:
        return f"{major}.{minor}.{patch + 1}"


def _print_gate_hint(gate) -> None:
    """Print a short actionable hint when the gate blocks."""
    if gate.diff is not None:
        events = [e for e in gate.diff.events if e.category not in {"package", "risk"}]
        if events:
            print(f"hint: {len(events)} semantic changes detected. Use --bump {gate.required_bump} to auto-fix.")


def _git_short_hash(path) -> str:
    """Get the current HEAD short hash."""
    from .git import git_output as _git_out
    try:
        return _git_out(["rev-parse", "--short", "HEAD"], cwd=path)
    except Exception:
        return "??????"


def cmd_pr_summary(args: argparse.Namespace) -> int:
    git_range = parse_git_range(args.old) if args.new is None else None
    with load_package_pair(args.old, args.new) as (old, new):
        content = _render_pr_summary(
            old,
            new,
            args.format,
            current_spec="." if git_range else None,
            diff_command=f"sit diff {args.old}" if git_range else None,
            baseline_source=git_range.old if git_range else args.old,
            current_source=git_range.new if git_range else args.new,
        )

    if args.output:
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding="utf-8")
        print(f"Wrote PR summary: {output}")
    else:
        print(content, end="")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    git_range = parse_git_range(args.old) if args.new is None else None
    with load_package_pair(args.old, args.new) as (old, new):
        payload = build_skill_review_payload(
            old,
            new,
            baseline_source=git_range.old if git_range else args.old,
            current_source=git_range.new if git_range else args.new,
        )
        if args.format == "json":
            content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        else:
            content = render_skill_review_markdown(
                payload,
                current_spec="." if git_range else None,
                diff_command=f"sit diff {args.old}" if git_range else None,
            )

    if args.output:
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding="utf-8")
        print(f"Wrote Skill review: {output}")
    else:
        print(content, end="")
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    package = load_package(args.package)
    message = release_package(
        package,
        args.bump,
        no_git_tag=args.no_git_tag,
        no_version_gate=args.no_version_gate,
        bundle=args.bundle,
        allow_empty=args.allow_empty,
    )
    if not getattr(args, "quiet", False):
        print(message)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sit", description="Skill Iteration Toolkit CLI")
    parser.add_argument("--version", action="version", version=f"sit {__version__}")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed output")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress non-essential output")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="Create a Skill Package scaffold and optionally initialize Git")
    init.add_argument("name", help="Skill Package name")
    init.add_argument("--path", help="Target directory; defaults to ./<name>")
    init.add_argument("--no-git", action="store_true", help="Do not run git init in the new package")
    init.set_defaults(func=cmd_init)

    status = subparsers.add_parser("status", help="Show Skill Package manifest and file status")
    status.add_argument("package", nargs="?", default=".", help="Skill Package directory or skill.yaml")
    status.set_defaults(func=cmd_status)

    info = subparsers.add_parser("info", help="Show a full Skill Package state snapshot")
    info.add_argument("package", nargs="?", default=".", help="Skill Package directory or skill.yaml")
    info.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    info.set_defaults(func=cmd_info)

    doctor = subparsers.add_parser("doctor", help="Check SitHub onboarding readiness for an existing Skill Package")
    doctor.add_argument("package", nargs="?", default=".", help="Skill Package directory or skill.yaml")
    doctor.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    doctor.set_defaults(func=cmd_doctor)

    deps = subparsers.add_parser("deps", help="Check local Skill Package dependencies")
    deps_subparsers = deps.add_subparsers(dest="deps_command", required=True)
    deps_check = deps_subparsers.add_parser("check", help="Validate deps.yaml local path dependencies")
    deps_check.add_argument("package", nargs="?", default=".", help="Skill Package directory or skill.yaml")
    deps_check.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    deps_check.set_defaults(func=cmd_deps_check)

    onboard = subparsers.add_parser("onboard", help="Onboard an existing SKILL.md project into a SitHub Skill Package")
    onboard.add_argument("path", nargs="?", default=".", help="Existing Skill directory containing SKILL.md")
    onboard.add_argument("--name", help="Skill Package name; defaults to the directory name")
    onboard.add_argument("--version", default="0.1.0", help="Initial Skill Package version")
    onboard.add_argument("--remote", help="Optional GitHub remote URL to add as origin when missing")
    onboard.add_argument("--no-git", action="store_true", help="Do not initialize a Git repository")
    onboard.add_argument("--force", action="store_true", help="Overwrite generated SitHub onboarding files")
    onboard.add_argument("--agent", action="store_true", help="Generate agent auto-discovery config (.mcp.json + AGENTS.md)")
    onboard.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    onboard.set_defaults(func=cmd_onboard)

    standardize = subparsers.add_parser("standardize", help="Standardize an existing prompt or SKILL.md project into a Skill Package")
    standardize.add_argument("path", nargs="?", default=".", help="Existing Skill or prompt directory")
    standardize.add_argument("--name", help="Skill Package name; defaults to the directory name")
    standardize.add_argument("--version", default="0.1.0", help="Initial Skill Package version")
    standardize.add_argument("--remote", help="Optional GitHub remote URL to add as origin when missing")
    standardize.add_argument("--no-git", action="store_true", help="Do not initialize a Git repository")
    standardize.add_argument("--force", action="store_true", help="Overwrite generated SitHub standardization files")
    standardize.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    standardize.set_defaults(func=cmd_standardize)

    install_hooks = subparsers.add_parser("install-hooks", help="Install Git hooks that run sit checks before direct git commit")
    install_hooks.add_argument("package", nargs="?", default=".", help="Skill Package directory or skill.yaml")
    install_hooks.add_argument("--force", action="store_true", help="Replace an existing non-sit pre-commit hook")
    install_hooks.set_defaults(func=cmd_install_hooks)

    validate = subparsers.add_parser("validate", help="Validate manifest paths, schemas, and golden JSONL")
    validate.add_argument("package", nargs="?", default=".", help="Skill Package directory or skill.yaml")
    validate.set_defaults(func=cmd_validate)

    test = subparsers.add_parser("test", help="Run deterministic golden expected-vs-schema tests")
    test.add_argument("package", nargs="?", default=".", help="Skill Package directory or skill.yaml")
    test.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    test.add_argument("--run", action="store_true", help="Run each golden case through a Skill runner to generate actual output")
    test.add_argument("--runner", help="Runner command template; overrides skill.yaml commands.run_case")
    test.add_argument("--timeout", type=int, default=30, help="Runner timeout per golden case in seconds")
    test.set_defaults(func=cmd_test)

    diff = subparsers.add_parser("diff", help="Compare two Skill Package directories or a Git range")
    diff.add_argument("old", nargs="?", default="HEAD..WORKTREE", help="Baseline Skill Package directory, skill.yaml, or Git range such as main..HEAD")
    diff.add_argument("new", nargs="?", help="Current Skill Package directory or skill.yaml")
    diff.add_argument("--staged", action="store_true", help="Preview the version gate result for currently staged/working changes")
    diff.add_argument("--format", choices=["text", "plain", "markdown", "json"], default="text", help="Output format")
    diff.add_argument("--prompt", action="store_true", help="Include prompt/reference unified text diffs")
    diff.set_defaults(func=cmd_diff)

    report = subparsers.add_parser("report", help="Generate a validation, test, and reproducibility report")
    report.add_argument("package", nargs="?", default=".", help="Skill Package directory or skill.yaml")
    report.add_argument("--compare", help="Optional baseline Skill Package or Git range such as main..HEAD")
    report.add_argument("--format", choices=["markdown", "json", "html"], default="markdown", help="Output format")
    report.add_argument("-o", "--output", help="Write report to this path instead of stdout")
    report.set_defaults(func=cmd_report)

    ci_summary = subparsers.add_parser("ci-summary", help="Generate a Markdown summary for GitHub Actions")
    ci_summary.add_argument("package", nargs="?", default=".", help="Skill Package directory or skill.yaml")
    ci_summary.add_argument("--compare", help="Optional baseline Skill Package or Git range such as origin/main..HEAD")
    ci_summary.add_argument("--baseline-ref", help="Baseline Git ref used to build a compare range")
    ci_summary.add_argument("--head-ref", help="Head Git ref used to build a compare range")
    ci_summary.add_argument("--package-dir", help="Skill Package subdirectory when running from a repository root")
    ci_summary.add_argument("--artifact-dir", help="Write sit-report.json, sit-report.md, sit-report.html, and sit-summary.md")
    ci_summary.add_argument("-o", "--output", help="Write Markdown summary to this path instead of stdout")
    ci_summary.add_argument("--append", action="store_true", help="Append to --output instead of replacing it")
    ci_summary.set_defaults(func=cmd_ci_summary)

    pr_summary = subparsers.add_parser("pr-summary", help="Generate a Markdown Skill PR summary")
    pr_summary.add_argument("old", help="Baseline Skill Package directory, skill.yaml, or Git range such as main..HEAD")
    pr_summary.add_argument("new", nargs="?", help="Current Skill Package directory or skill.yaml")
    pr_summary.add_argument("--format", choices=["markdown", "text", "json"], default="markdown", help="Output format")
    pr_summary.add_argument("-o", "--output", help="Write summary to this path instead of stdout")
    pr_summary.set_defaults(func=cmd_pr_summary)

    review = subparsers.add_parser("review", help="Generate a PR-ready Skill review comment")
    review.add_argument("old", help="Baseline Skill Package directory, skill.yaml, or Git range such as main..HEAD")
    review.add_argument("new", nargs="?", help="Current Skill Package directory or skill.yaml")
    review.add_argument("--format", choices=["markdown", "json"], default="markdown", help="Output format")
    review.add_argument("-o", "--output", help="Write review to this path instead of stdout")
    review.set_defaults(func=cmd_review)

    release = subparsers.add_parser("release", help="Bump package version, write release report, and tag with Git")
    release.add_argument("bump", choices=["patch", "minor", "major"], help="Semantic version bump")
    release.add_argument("package", nargs="?", default=".", help="Skill Package directory or skill.yaml")
    release.add_argument("--no-git-tag", action="store_true", help="Skip creating an annotated Git tag")
    release.add_argument("--no-version-gate", action="store_true", help="Skip semantic diff versus version bump consistency check")
    release.add_argument("--bundle", action="store_true", help="Write a reproducible release tarball under dist/")
    release.add_argument("--allow-empty", action="store_true", help="Allow an empty release when no unreleased semantic change is detected")
    release.set_defaults(func=cmd_release)

    for git_command in ("add", "push", "pull", "branch", "checkout", "log"):
        git_parser = subparsers.add_parser(git_command, help=f"Pass through to git {git_command}")
        git_parser.add_argument("git_args", nargs=argparse.REMAINDER)
        git_parser.set_defaults(func=cmd_git, git_command=git_command)

    commit = subparsers.add_parser("commit", help="Validate/test the Skill Package, then pass through to git commit")
    commit.add_argument("-m", "--message", help="Commit message")
    commit.add_argument("--bump", choices=["patch", "minor", "major"],
                        help="Auto-bump skill.yaml version before committing if the version gate requires it")
    commit.add_argument("--package", default=".", help="Skill Package to validate before committing")
    commit.add_argument("--no-test", action="store_true", help="Skip golden tests before committing")
    commit.add_argument("--no-version-gate", action="store_true", help="Skip semantic diff versus version bump consistency check")
    commit.add_argument("--no-verify", action="store_true", help="Skip sit validation and tests")
    commit.add_argument("git_args", nargs=argparse.REMAINDER)
    commit.set_defaults(func=cmd_commit)

    undo = subparsers.add_parser("undo", help="Undo the last sit commit (soft reset HEAD~1)")
    undo.add_argument("--hard", action="store_true", help="Hard reset (discard working tree changes too)")
    undo.add_argument("--dry-run", action="store_true", help="Show the commit that would be undone without resetting")
    undo.add_argument("--package", default=".", help="Skill Package directory")
    undo.set_defaults(func=cmd_undo)

    return parser


def _print_path_group(title: str, paths: dict[str, Path]) -> None:
    print(f"{title}:")
    if not paths:
        print("  <none>")
        return
    for name, path in paths.items():
        marker = "exists" if path.exists() else "missing"
        print(f"  {name}: {marker} {path}")


def _ci_compare_spec(args: argparse.Namespace) -> str | None:
    if args.compare and (args.baseline_ref or args.head_ref):
        raise SitError("Use either --compare or --baseline-ref/--head-ref, not both")
    if args.compare:
        return args.compare
    if args.baseline_ref or args.head_ref:
        return f"{args.baseline_ref or 'origin/main'}..{args.head_ref or 'HEAD'}"
    return None


def _write_ci_artifacts(directory: Path, payload: dict, summary: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "sit-summary.md").write_text(summary, encoding="utf-8")
    (directory / "sit-report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (directory / "sit-report.md").write_text(render_report_markdown(payload), encoding="utf-8")
    (directory / "sit-report.html").write_text(render_report_html(payload), encoding="utf-8")


def _render_diff(
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
            f"- Baseline: `{_format_package_ref(old, old_source)}`",
            f"- Current: `{_format_package_ref(new, new_source)}`",
            f"- Risk: `{result.risk}`",
            f"- Suggested version bump: `{result.suggested_bump}`",
            "",
            "### Events",
            "",
        ]
        for event in result.events:
            lines.append(f"- `{event.message}`")
            lines.extend(f"  - `{detail}`" for detail in _event_detail_lines(event))
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
            lines.extend(_event_detail_lines(event))
        if show_prompt_diff and result.text_diffs:
            lines.extend(["", "Prompt/Reference Unified Diff:"])
            for text_diff in result.text_diffs:
                lines.append(f"--- {text_diff.kind}: {text_diff.name}")
                lines.extend(text_diff.lines)
        return "\n".join(lines) + "\n"

    lines = [
        "Skill Diff",
        f"Baseline: {_format_package_ref(old, old_source)}",
        f"Current: {_format_package_ref(new, new_source)}",
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
            lines.extend(f"    {detail}" for detail in _event_detail_lines(event))
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


def _format_package_ref(package, source: str | None = None) -> str:
    ref = f"{package.name or '<unknown>'}@{package.version or '<unknown>'}"
    return f"{ref} ({source})" if source else ref


def _event_detail_lines(event) -> list[str]:
    details = getattr(event, "details", None)
    if not isinstance(details, dict):
        return []
    return render_script_details(details, indent="")


def _render_pr_summary(
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


if __name__ == "__main__":
    raise SystemExit(main())
