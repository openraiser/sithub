# SitHub Experiments

This directory holds agent-native experiments built on top of `sit`. The `sit/`
CLI remains the infrastructure layer; experiment drivers live here so agent
algorithms can evolve Skills without adding policy or benchmark code to the CLI.

## Driver Contract

`driver.py` supports two deterministic loops.

### Fake-Agent Smoke

The default mode runs a simple fake-agent loop:

1. copy a Skill Package into `experiments/runs/<run-id>/worktree`
2. initialize a local Git repository on `main`
3. for each step, fork an agent branch
4. evolve one prompt line
5. propose the change with `sit diff` and `sit pr-summary` JSON
6. verify with `sit validate` and `sit test --format json`
7. merge accepted changes back to `main`

Each command and decision is appended to:

```text
experiments/runs/<run-id>/trajectory.jsonl
```

The trajectory records command summaries, return codes, parsed `sit` JSON schema
versions, diff risk, suggested bump, and merge decisions.

Run:

```bash
python3 experiments/driver.py \
  --package examples/paper-taxonomy-mapper-v0.2.0 \
  --steps 5 \
  --seed 0
```

Use `--run-id <name>` to make a reproducible output path.

### H2 Regression Recovery

H2 injects a bad Skill PR at `--bad-step` by adding a deterministic regression
marker to the prompt. The synthetic evaluator treats that marker as a 30%
success-rate drop. This keeps the recovery infrastructure reproducible before
LLM-backed task success measurement is added.

Supported conditions:

- `novc`: direct overwrite baseline; recovery happens after `--no-vc-recovery-delay` relearning steps.
- `periodic-snap`: restores the most recent full package snapshot from `--snapshot-interval`.
- `ours-sit`: uses `git revert <bad-sha>` and `sit release patch --no-git-tag --no-version-gate`.

Single-condition smoke:

```bash
python3 experiments/driver.py \
  --experiment h2 \
  --condition ours-sit \
  --steps 8 \
  --bad-step 4 \
  --seed 0 \
  --run-id h2-ours-smoke
```

Batch smoke across all conditions:

```bash
python3 experiments/driver.py \
  --experiment h2 \
  --condition all \
  --steps 8 \
  --bad-step 4 \
  --snapshot-interval 3 \
  --no-vc-recovery-delay 3 \
  --seeds 0,1 \
  --run-id h2-batch-smoke
```

Each run writes `summary.json` with `detection_step`, `recovery_action_step`,
`recovered_step`, `recovery_steps`, `affected_skill_steps`, `work_lost_steps`,
and final success rate. Batch mode writes per-run summaries plus an aggregate
`summary.json`.

## JSON Schemas

Drivers should parse only documented `sit` JSON contracts:

- `docs/schemas/sit.info.v1.md`
- `docs/schemas/sit.pr-summary.v1.md`
- `docs/schemas/sit.report.v1.md`
- `docs/schemas/sit.test.v1.md`

The fake-agent driver intentionally uses `subprocess` and public `sit` JSON
outputs. Future H1/H2/H3/H4 experiments should keep the same boundary unless a
new CLI contract is documented first.
