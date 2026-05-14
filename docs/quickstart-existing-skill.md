# Quickstart: Onboard an Existing Skill

This guide shows how to bring an existing Skill directory under SitHub control.

Use this when a project already has files such as:

- `SKILL.md`
- prompt/reference documents
- scripts
- examples
- generated outputs

SitHub does not replace those files. It adds a semantic control layer around them.

## 1. Start From a Git Repository

If the project is not a Git repository yet:

```bash
cd /path/to/existing-skill
git init -b main
git add .
git commit -m "feat: add existing skill"
git remote add origin https://github.com/OWNER/REPO.git
git push -u origin main
```

## 2. Add SitHub Package Metadata

Create `skill.yaml`:

```yaml
name: paper-webpage-builder
version: 0.1.0
description: Build polished single-page academic project webpages from paper sources.
prompts:
  skill: SKILL.md
schemas:
  input: schemas/input.schema.json
  output: schemas/output.schema.json
tests:
  golden: tests/golden.jsonl
runtime:
  model: configurable
  temperature: 0
tags:
  - codex-skill
  - sithub
```

The important part is that `prompts`, `schemas`, and `tests` point to files that exist.

## 3. Add Schemas

Create:

```text
schemas/input.schema.json
schemas/output.schema.json
```

The output schema should describe the result contract of the Skill. For example, a webpage builder can return:

- generated `index_html_path`
- page `modules`
- copied `assets`
- `validation` status
- remaining `risks`

## 4. Add Golden Cases

Create `tests/golden.jsonl`.

Each line is one deterministic case:

```json
{"case_id":"case-001","input":{"text":"hello"},"expected":{"answer":"hello"},"match_mode":"schema_only"}
```

Use `schema_only` first. It lets SitHub check that expected outputs conform to the output schema before you have a full execution harness.

## 5. Check Readiness

Run:

```bash
sit doctor
sit validate
sit test
sit info
```

Expected result:

- `sit doctor` reports package/Git/GitHub/CI readiness
- `sit validate` passes
- `sit test` passes
- `sit info` shows Git state and reports

## 6. Generate an Onboarding Report

```bash
mkdir -p reports
sit report --format html --output reports/sithub-onboarding.html
sit report --output reports/sithub-onboarding.md
sit ci-summary --artifact-dir reports/ci-onboarding
```

These reports prove the Skill can be observed and tested by SitHub.

## 7. Add GitHub Actions

Create `.github/workflows/sit-ci.yaml`:

```yaml
name: Skill CI
on: [pull_request]

env:
  SIT_PACKAGE_DIR: "."
  SIT_BASELINE_REF: "origin/${{ github.base_ref }}"
  SIT_HEAD_REF: "HEAD"
  SIT_ARTIFACT_DIR: "reports/ci"

jobs:
  validate-and-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - name: Install sit
        run: python -m pip install git+https://github.com/OpenRaiser/SitHub.git
      - run: sit validate "$SIT_PACKAGE_DIR"
      - run: sit test "$SIT_PACKAGE_DIR"
      - name: Write SitHub summary
        if: always()
        run: |
          sit ci-summary \
            --package-dir "$SIT_PACKAGE_DIR" \
            --baseline-ref "$SIT_BASELINE_REF" \
            --head-ref "$SIT_HEAD_REF" \
            --artifact-dir "$SIT_ARTIFACT_DIR" \
            >> "$GITHUB_STEP_SUMMARY"
      - name: Upload SitHub artifacts
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: sithub-report
          path: ${{ env.SIT_ARTIFACT_DIR }}
```

This requires `OpenRaiser/SitHub` to be publicly installable, or a private install token to be configured.

## 8. Run a PR Loop

Create a branch:

```bash
sit checkout -b experiment/sithub-pr-loop
```

Make a small Skill change, then run:

```bash
sit validate
sit test
sit diff main..HEAD
sit report . --compare main..HEAD --format html --output reports/pr-loop.html
sit pr-summary main..HEAD
sit commit -m "feat: update skill contract"
sit push -u origin experiment/sithub-pr-loop
```

Open a GitHub PR and confirm:

- Actions install `sit`
- `sit validate` passes
- `sit test` passes
- SitHub summary appears in the run summary
- `sithub-report` artifact is uploaded

## Current Limits

- SitHub does not run the Skill itself yet; golden cases validate expected/actual records against schema and match mode.
- Installing from GitHub source requires `OpenRaiser/SitHub` to be reachable from CI.
- Existing `SKILL.md` conversion is still manual. A future `sit onboard` command should automate the repetitive parts.
