from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACKAGE = REPO_ROOT / "examples" / "paper-taxonomy-mapper-v0.2.0"
DEFAULT_RUN_ROOT = REPO_ROOT / "experiments" / "runs"
BAD_REGRESSION_MARKER = "BAD_REGRESSION_H2_OUTPUT_DEGRADES_30_PERCENT"
BASELINE_SUCCESS_RATE = 1.0
REGRESSED_SUCCESS_RATE = 0.7


class DriverError(RuntimeError):
    pass


class Trajectory:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict[str, Any]) -> None:
        payload = {"time": datetime.now(timezone.utc).isoformat(), **record}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    run_id = args.run_id or _default_run_id(args.seed)
    run_dir = args.output_root.resolve() / run_id

    if args.experiment == "h2" and args.condition == "all":
        _run_h2_batch(args, run_dir)
        print(f"H2 batch complete: {run_dir}")
        print(f"Summary: {run_dir / 'summary.json'}")
        return 0

    package_src = args.package.resolve()
    package_worktree = run_dir / "worktree"
    trajectory = Trajectory(run_dir / "trajectory.jsonl")

    if run_dir.exists() and any(run_dir.iterdir()):
        raise DriverError(f"run directory already exists: {run_dir}")

    _copy_package(package_src, package_worktree)
    rng = random.Random(args.seed)
    _init_git(package_worktree, trajectory)
    _record_state(trajectory, step=0, phase="init", package=package_worktree)

    if args.experiment == "fake":
        _run_fake_agent_loop(package_worktree, trajectory=trajectory, steps=args.steps, rng=rng)
        summary = {"experiment": "fake", "steps": args.steps, "seed": args.seed, "trajectory": str(trajectory.path)}
    else:
        summary = _run_h2_condition(
            package_worktree,
            run_dir=run_dir,
            trajectory=trajectory,
            condition=args.condition,
            steps=args.steps,
            bad_step=args.bad_step,
            snapshot_interval=args.snapshot_interval,
            no_vc_recovery_delay=args.no_vc_recovery_delay,
            rng=rng,
        )
    _write_summary(run_dir, summary)

    print(f"Experiment complete: {run_dir}")
    print(f"Trajectory: {trajectory.path}")
    print(f"Summary: {run_dir / 'summary.json'}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run SitHub multi-agent experiment drivers.")
    parser.add_argument("--experiment", choices=["fake", "h2"], default="fake", help="Experiment driver to run")
    parser.add_argument("--package", type=Path, default=DEFAULT_PACKAGE, help="Skill Package to copy into the experiment worktree")
    parser.add_argument("--steps", type=int, default=5, help="Number of fake-agent evolve/propose/verify/merge steps")
    parser.add_argument("--seed", type=int, default=0, help="Deterministic fake-agent seed")
    parser.add_argument("--seeds", default="0,1,2,3,4", help="Comma-separated seeds for --experiment h2 --condition all")
    parser.add_argument("--run-id", help="Run directory name under --output-root")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_RUN_ROOT, help="Directory for experiment run artifacts")
    parser.add_argument("--condition", choices=["ours-sit", "periodic-snap", "novc", "all"], default="ours-sit", help="H2 condition")
    parser.add_argument("--bad-step", type=int, default=100, help="H2 step where the bad Skill PR is force-merged")
    parser.add_argument("--snapshot-interval", type=int, default=50, help="H2 PeriodicSnap snapshot interval")
    parser.add_argument("--no-vc-recovery-delay", type=int, default=30, help="H2 NoVC relearning delay after detection")
    return parser


def _run_fake_agent_loop(package_worktree: Path, *, trajectory: Trajectory, steps: int, rng: random.Random) -> None:
    for step in range(1, steps + 1):
        branch = f"agent/fake-step-{step}"
        _git(package_worktree, trajectory, step, "fork", "checkout", "-b", branch)
        _evolve_prompt(package_worktree, step=step, rng=rng, trajectory=trajectory)
        _git(package_worktree, trajectory, step, "propose", "add", ".")
        _git(package_worktree, trajectory, step, "propose", "commit", "-m", f"fake-agent: evolve prompt step {step}")

        validation = _sit(package_worktree, trajectory, step, "verify", "validate", ".")
        tests = _sit(package_worktree, trajectory, step, "verify", "test", ".", "--format", "json")
        diff = _sit(package_worktree, trajectory, step, "propose", "diff", "main..HEAD", "--format", "json")
        summary = _sit(package_worktree, trajectory, step, "propose", "pr-summary", "main..HEAD", "--format", "json")

        accepted = validation.returncode == 0 and _payload_ok(tests.payload, "golden_tests")
        trajectory.write(
            {
                "step": step,
                "phase": "decision",
                "accepted": accepted,
                "diff_risk": _get(diff.payload, "risk"),
                "suggested_bump": _get(diff.payload, "suggested_bump"),
                "pr_risk": _get(summary.payload, "risk"),
            }
        )
        if not accepted:
            _git(package_worktree, trajectory, step, "merge", "checkout", "main")
            raise DriverError(f"step {step} rejected; see {trajectory.path}")

        _git(package_worktree, trajectory, step, "merge", "checkout", "main")
        _git(package_worktree, trajectory, step, "merge", "merge", "--ff-only", branch)
        _record_state(trajectory, step=step, phase="merged", package=package_worktree)


def _run_h2_batch(args: argparse.Namespace, run_dir: Path) -> None:
    if run_dir.exists() and any(run_dir.iterdir()):
        raise DriverError(f"run directory already exists: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for condition in ("novc", "periodic-snap", "ours-sit"):
        for seed in _parse_seeds(args.seeds):
            child_dir = run_dir / f"{condition}-seed{seed}"
            child_args = argparse.Namespace(**vars(args))
            child_args.condition = condition
            child_args.seed = seed
            child_args.run_id = child_dir.name
            child_args.output_root = run_dir
            child_args.experiment = "h2"
            package_worktree = child_dir / "worktree"
            trajectory = Trajectory(child_dir / "trajectory.jsonl")
            _copy_package(child_args.package.resolve(), package_worktree)
            rng = random.Random(seed)
            _init_git(package_worktree, trajectory)
            _record_state(trajectory, step=0, phase="init", package=package_worktree)
            summary = _run_h2_condition(
                package_worktree,
                run_dir=child_dir,
                trajectory=trajectory,
                condition=condition,
                steps=child_args.steps,
                bad_step=child_args.bad_step,
                snapshot_interval=child_args.snapshot_interval,
                no_vc_recovery_delay=child_args.no_vc_recovery_delay,
                rng=rng,
            )
            _write_summary(child_dir, summary)
            records.append(summary)
    _write_summary(run_dir, _aggregate_h2_summary(records))


def _run_h2_condition(
    package_worktree: Path,
    *,
    run_dir: Path,
    trajectory: Trajectory,
    condition: str,
    steps: int,
    bad_step: int,
    snapshot_interval: int,
    no_vc_recovery_delay: int,
    rng: random.Random,
) -> dict[str, Any]:
    if condition not in {"ours-sit", "periodic-snap", "novc"}:
        raise DriverError(f"H2 requires a concrete condition, got {condition}")
    if bad_step < 1 or bad_step > steps:
        raise DriverError("--bad-step must be between 1 and --steps for H2")

    snapshots_dir = run_dir / "snapshots"
    latest_snapshot: tuple[int, Path] | None = None
    detection_step: int | None = None
    recovery_action_step: int | None = None
    recovered_step: int | None = None
    bad_sha: str | None = None
    no_vc_recovery_step: int | None = None
    baseline_score = _evaluate_package(package_worktree)["success_rate"]
    score_history = []
    work_lost_steps = 0

    for step in range(1, steps + 1):
        if condition == "periodic-snap" and (step - 1) % snapshot_interval == 0:
            latest_snapshot = (step - 1, _write_snapshot(package_worktree, snapshots_dir, step - 1))
            trajectory.write({"step": step, "phase": "snapshot", "snapshot_step": latest_snapshot[0], "path": str(latest_snapshot[1])})

        if step == bad_step:
            bad_sha = _force_merge_bad_pr(package_worktree, trajectory=trajectory, step=step)
        elif condition == "novc" and no_vc_recovery_step is not None and step >= no_vc_recovery_step and _has_bad_regression(package_worktree):
            _remove_bad_regression(package_worktree, trajectory=trajectory, step=step)
            _git(package_worktree, trajectory, step, "novc-recover", "add", ".")
            _git(package_worktree, trajectory, step, "novc-recover", "commit", "-m", f"novc: relearn after bad regression at step {step}")
        else:
            _commit_h2_evolution(package_worktree, trajectory=trajectory, step=step, rng=rng)

        evaluation = _evaluate_package(package_worktree)
        score_history.append({"step": step, **evaluation})
        trajectory.write({"step": step, "phase": "evaluate", **evaluation})

        if detection_step is None and evaluation["success_rate"] < baseline_score:
            detection_step = step
            trajectory.write({"step": step, "phase": "detect", "baseline_success_rate": baseline_score, **evaluation})
            if condition == "ours-sit":
                if not bad_sha:
                    raise DriverError("ours-sit recovery needs the bad merge sha")
                _recover_ours_sit(package_worktree, trajectory=trajectory, step=step, bad_sha=bad_sha)
                recovery_action_step = step
            elif condition == "periodic-snap":
                if latest_snapshot is None:
                    latest_snapshot = (0, _write_snapshot(package_worktree, snapshots_dir, 0))
                _restore_snapshot(package_worktree, latest_snapshot[1], trajectory=trajectory, step=step)
                _git(package_worktree, trajectory, step, "periodic-recover", "add", ".")
                _git(package_worktree, trajectory, step, "periodic-recover", "commit", "-m", f"periodic: restore snapshot step {latest_snapshot[0]}")
                work_lost_steps = max(0, step - latest_snapshot[0])
                recovery_action_step = step
            else:
                no_vc_recovery_step = step + no_vc_recovery_delay
                recovery_action_step = no_vc_recovery_step

        if detection_step is not None and recovered_step is None and step > detection_step and evaluation["success_rate"] >= baseline_score:
            recovered_step = step

    if detection_step is not None and recovered_step is None and not _has_bad_regression(package_worktree):
        recovered_step = recovery_action_step

    summary = {
        "experiment": "h2_regression_recovery",
        "condition": condition,
        "steps": steps,
        "bad_step": bad_step,
        "detection_step": detection_step,
        "recovery_action_step": recovery_action_step,
        "recovered_step": recovered_step,
        "recovery_steps": None if recovered_step is None else recovered_step - bad_step,
        "work_lost_steps": work_lost_steps,
        "baseline_success_rate": baseline_score,
        "final_success_rate": score_history[-1]["success_rate"] if score_history else baseline_score,
        "affected_skill_steps": sum(1 for item in score_history if item["affected_skills"] > 0),
        "bad_sha": bad_sha,
        "trajectory": str(trajectory.path),
    }
    trajectory.write({"step": steps, "phase": "summary", **summary})
    return summary


def _copy_package(src: Path, dst: Path) -> None:
    if not (src / "skill.yaml").exists():
        raise DriverError(f"not a Skill Package: {src}")
    ignore = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "dist", "._*", ".DS_Store")
    shutil.copytree(src, dst, ignore=ignore)


def _init_git(cwd: Path, trajectory: Trajectory) -> None:
    _git(cwd, trajectory, 0, "init", "init")
    _git(cwd, trajectory, 0, "init", "branch", "-M", "main")
    _git(cwd, trajectory, 0, "init", "config", "user.email", "sit-experiment@example.test")
    _git(cwd, trajectory, 0, "init", "config", "user.name", "Sit Experiment Driver")
    _git(cwd, trajectory, 0, "init", "add", ".")
    _git(cwd, trajectory, 0, "init", "commit", "-m", "experiment: initial package")


def _evolve_prompt(package: Path, *, step: int, rng: random.Random, trajectory: Trajectory) -> None:
    prompt = package / "prompts" / "classify.md"
    if not prompt.exists():
        prompt = next((package / "prompts").glob("*.md"))
    choices = [
        "Prefer evidence that names the task setting explicitly.",
        "When categories are close, choose the one most supported by the abstract.",
        "Keep confidence conservative when evidence is indirect.",
        "Treat benchmark construction and benchmark evaluation as separate signals.",
        "Use other only when no listed research area is supported.",
    ]
    addition = f"\nFake-agent note {step}: {rng.choice(choices)}\n"
    prompt.write_text(prompt.read_text(encoding="utf-8").rstrip() + addition, encoding="utf-8")
    trajectory.write({"step": step, "phase": "evolve", "file": str(prompt), "addition": addition.strip()})


def _commit_h2_evolution(package: Path, *, trajectory: Trajectory, step: int, rng: random.Random) -> None:
    branch = f"agent/h2-step-{step}"
    _git(package, trajectory, step, "fork", "checkout", "-b", branch)
    _evolve_prompt(package, step=step, rng=rng, trajectory=trajectory)
    _git(package, trajectory, step, "propose", "add", ".")
    _git(package, trajectory, step, "propose", "commit", "-m", f"h2-agent: evolve prompt step {step}")
    validation = _sit(package, trajectory, step, "verify", "validate", ".")
    tests = _sit(package, trajectory, step, "verify", "test", ".", "--format", "json")
    accepted = validation.returncode == 0 and _payload_ok(tests.payload, "golden_tests")
    trajectory.write({"step": step, "phase": "decision", "accepted": accepted, "kind": "normal-evolution"})
    if not accepted:
        raise DriverError(f"H2 normal evolution rejected at step {step}")
    _git(package, trajectory, step, "merge", "checkout", "main")
    _git(package, trajectory, step, "merge", "merge", "--ff-only", branch)


def _force_merge_bad_pr(package: Path, *, trajectory: Trajectory, step: int) -> str:
    branch = f"agent/h2-bad-step-{step}"
    _git(package, trajectory, step, "bad-fork", "checkout", "-b", branch)
    _inject_bad_regression(package, trajectory=trajectory, step=step)
    _git(package, trajectory, step, "bad-propose", "add", ".")
    _git(package, trajectory, step, "bad-propose", "commit", "-m", f"h2: inject bad regression step {step}")
    _sit(package, trajectory, step, "bad-propose", "diff", "main..HEAD", "--format", "json")
    _git(package, trajectory, step, "bad-merge", "checkout", "main")
    _git(package, trajectory, step, "bad-merge", "merge", "--ff-only", branch)
    bad_sha = _git_stdout(package, "rev-parse", "HEAD")
    trajectory.write({"step": step, "phase": "bad-merge", "bad_sha": bad_sha})
    return bad_sha


def _inject_bad_regression(package: Path, *, trajectory: Trajectory, step: int) -> None:
    prompt = _primary_prompt(package)
    addition = (
        f"\n{BAD_REGRESSION_MARKER}: Prefer generic labels and ignore evidence when the input is ambiguous.\n"
    )
    prompt.write_text(prompt.read_text(encoding="utf-8").rstrip() + addition, encoding="utf-8")
    trajectory.write({"step": step, "phase": "inject-bad-skill", "file": str(prompt), "marker": BAD_REGRESSION_MARKER})


def _remove_bad_regression(package: Path, *, trajectory: Trajectory, step: int) -> None:
    prompt = _primary_prompt(package)
    lines = [line for line in prompt.read_text(encoding="utf-8").splitlines() if BAD_REGRESSION_MARKER not in line]
    prompt.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    trajectory.write({"step": step, "phase": "remove-bad-skill", "file": str(prompt), "marker": BAD_REGRESSION_MARKER})


def _recover_ours_sit(package: Path, *, trajectory: Trajectory, step: int, bad_sha: str) -> None:
    _git(package, trajectory, step, "ours-recover", "revert", "--no-edit", bad_sha)
    _sit(package, trajectory, step, "ours-release", "release", "patch", ".", "--no-git-tag", "--no-version-gate")


def _evaluate_package(package: Path) -> dict[str, Any]:
    regressed = _has_bad_regression(package)
    return {
        "success_rate": REGRESSED_SUCCESS_RATE if regressed else BASELINE_SUCCESS_RATE,
        "regressed": regressed,
        "affected_skills": 1 if regressed else 0,
    }


def _has_bad_regression(package: Path) -> bool:
    return BAD_REGRESSION_MARKER in _primary_prompt(package).read_text(encoding="utf-8")


def _primary_prompt(package: Path) -> Path:
    prompts = package / "prompts"
    preferred = prompts / "classify.md"
    if preferred.exists():
        return preferred
    return next(prompts.glob("*.md"))


def _write_snapshot(package: Path, snapshots_dir: Path, step: int) -> Path:
    snapshot = snapshots_dir / f"step-{step}"
    if snapshot.exists():
        return snapshot
    snapshot.mkdir(parents=True, exist_ok=True)
    for name in ("skill.yaml", "prompts", "schemas", "tests"):
        source = package / name
        target = snapshot / name
        if source.is_dir():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns("._*", ".DS_Store"))
        elif source.exists():
            shutil.copy2(source, target)
    return snapshot


def _restore_snapshot(package: Path, snapshot: Path, *, trajectory: Trajectory, step: int) -> None:
    for name in ("skill.yaml", "prompts", "schemas", "tests"):
        source = snapshot / name
        target = package / name
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            target.unlink()
        if source.is_dir():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns("._*", ".DS_Store"))
        elif source.exists():
            shutil.copy2(source, target)
    trajectory.write({"step": step, "phase": "restore-snapshot", "snapshot": str(snapshot)})


class CommandResult:
    def __init__(self, returncode: int, stdout: str, stderr: str, payload: dict[str, Any] | None = None) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.payload = payload


def _git(cwd: Path, trajectory: Trajectory, step: int, phase: str, *args: str) -> CommandResult:
    return _run(cwd, trajectory, step, phase, ["git", *args])


def _git_stdout(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=True).stdout.strip()


def _sit(cwd: Path, trajectory: Trajectory, step: int, phase: str, *args: str) -> CommandResult:
    env = os.environ.copy()
    pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(REPO_ROOT) if not pythonpath else str(REPO_ROOT) + os.pathsep + pythonpath
    return _run(cwd, trajectory, step, phase, [sys.executable, "-m", "sit.cli", *args], env=env)


def _run(
    cwd: Path,
    trajectory: Trajectory,
    step: int,
    phase: str,
    command: list[str],
    *,
    env: dict[str, str] | None = None,
) -> CommandResult:
    completed = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True)
    payload = _json_payload(completed.stdout)
    trajectory.write(
        {
            "step": step,
            "phase": phase,
            "command": command,
            "returncode": completed.returncode,
            "stdout_summary": _summarize_text(completed.stdout),
            "stderr_summary": _summarize_text(completed.stderr),
            "payload_summary": _summarize_payload(payload),
        }
    )
    if completed.returncode != 0:
        raise DriverError(f"command failed ({completed.returncode}): {' '.join(command)}")
    return CommandResult(completed.returncode, completed.stdout, completed.stderr, payload)


def _record_state(trajectory: Trajectory, *, step: int, phase: str, package: Path) -> None:
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=package, text=True, capture_output=True, check=True).stdout.strip()
    trajectory.write({"step": step, "phase": phase, "head": head})


def _json_payload(text: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _payload_ok(payload: dict[str, Any] | None, key: str) -> bool:
    if not payload:
        return False
    value = payload.get(key)
    return isinstance(value, dict) and value.get("ok") is True


def _get(payload: dict[str, Any] | None, key: str) -> Any:
    return payload.get(key) if payload else None


def _summarize_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    return {
        "schema_version": payload.get("schema_version"),
        "status": payload.get("status"),
        "risk": payload.get("risk"),
        "suggested_bump": payload.get("suggested_bump"),
    }


def _summarize_text(text: str, *, limit: int = 300) -> str:
    compact = " ".join(text.split())
    return compact[:limit] + ("..." if len(compact) > limit else "")


def _default_run_id(seed: int) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"fake-agent-{stamp}-seed{seed}"


def _parse_seeds(value: str) -> list[int]:
    seeds = []
    for raw in value.split(","):
        raw = raw.strip()
        if not raw:
            continue
        seeds.append(int(raw))
    if not seeds:
        raise DriverError("--seeds must contain at least one integer")
    return seeds


def _write_summary(run_dir: Path, summary: dict[str, Any]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _aggregate_h2_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_condition: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_condition.setdefault(str(record["condition"]), []).append(record)
    conditions = {}
    for condition, items in sorted(by_condition.items()):
        conditions[condition] = {
            "runs": len(items),
            "mean_recovery_steps": _mean(item.get("recovery_steps") for item in items),
            "mean_affected_skill_steps": _mean(item.get("affected_skill_steps") for item in items),
            "mean_work_lost_steps": _mean(item.get("work_lost_steps") for item in items),
            "unrecovered_runs": sum(1 for item in items if item.get("recovered_step") is None),
        }
    return {
        "experiment": "h2_regression_recovery_batch",
        "conditions": conditions,
        "runs": records,
    }


def _mean(values) -> float | None:
    numbers = [float(value) for value in values if isinstance(value, (int, float))]
    if not numbers:
        return None
    return sum(numbers) / len(numbers)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DriverError as exc:
        print(f"driver: error: {exc}", file=sys.stderr)
        raise SystemExit(2)
