# `sit.pr_summary.v1`

Produced by:

```bash
sit pr-summary <baseline> <current> --format json
sit pr-summary <git-range> --format json
```

Purpose: machine-readable Skill PR review payload. It combines validation, golden tests, semantic diff, risk, and prompt/reference summary.

## Top-Level Fields

| Field | Type | Required | Meaning |
|---|---:|---:|---|
| `schema_version` | string | yes | Literal `sit.pr_summary.v1`. |
| `baseline` | object | yes | Baseline package identity. |
| `current` | object | yes | Current package identity. |
| `validation` | object | yes | Validation result for current package. |
| `golden_tests` | object | yes | Golden test result for current package. |
| `risk` | string enum: `no-change`, `review-required`, `breaking-change` | yes | Overall diff risk. |
| `suggested_bump` | string enum: `patch`, `minor`, `major` | yes | Version bump implied by semantic diff. |
| `diff` | object | yes | Embedded `sit.diff.v1` payload. |
| `prompt_reference_summary` | string[] | yes | Prompt/reference text summaries from diff. |

## Objects

`baseline` and `current`:

| Field | Type | Required |
|---|---:|---:|
| `name` | string | yes |
| `version` | string | yes |
| `root` | string | yes |
| `manifest` | string | yes |
| `source` | string | no |

`validation`:

| Field | Type | Required |
|---|---:|---:|
| `status` | string enum: `pass`, `fail` | yes |
| `ok` | boolean | yes |
| `messages` | string[] | yes |

`golden_tests`:

| Field | Type | Required |
|---|---:|---:|
| `status` | string enum: `pass`, `fail`, `skipped` | yes |
| `ok` | boolean or null | yes |
| `messages` | string[] | yes |

`diff` is the same embedded `sit.diff.v1` shape documented in `sit.report.v1.md`: identity refs, `changed`, `breaking`, `risk`, `suggested_bump`, `messages`, `events`, and `text_diffs`.

## Example

```json
{
  "schema_version": "sit.pr_summary.v1",
  "baseline": {
    "name": "paper-taxonomy-mapper",
    "version": "0.1.0",
    "root": "/repo/examples/paper-taxonomy-mapper-v0.1.0",
    "manifest": "/repo/examples/paper-taxonomy-mapper-v0.1.0/skill.yaml",
    "source": "examples/paper-taxonomy-mapper-v0.1.0"
  },
  "current": {
    "name": "paper-taxonomy-mapper",
    "version": "0.2.0",
    "root": "/repo/examples/paper-taxonomy-mapper-v0.2.0",
    "manifest": "/repo/examples/paper-taxonomy-mapper-v0.2.0/skill.yaml",
    "source": "examples/paper-taxonomy-mapper-v0.2.0"
  },
  "validation": {"status": "pass", "ok": true, "messages": []},
  "golden_tests": {"status": "pass", "ok": true, "messages": ["SUMMARY 3/3 golden cases passed"]},
  "risk": "breaking-change",
  "suggested_bump": "major",
  "diff": {
    "schema_version": "sit.diff.v1",
    "changed": true,
    "breaking": true,
    "risk": "breaking-change",
    "suggested_bump": "major",
    "messages": ["SCHEMA output property added confidence (required)"],
    "events": [{"category": "schema", "severity": "breaking", "changed": true, "breaking": true, "message": "SCHEMA output property added confidence (required)"}],
    "text_diffs": [],
    "old": {"name": "paper-taxonomy-mapper", "version": "0.1.0", "root": "/repo/old", "manifest": "/repo/old/skill.yaml"},
    "new": {"name": "paper-taxonomy-mapper", "version": "0.2.0", "root": "/repo/new", "manifest": "/repo/new/skill.yaml"}
  },
  "prompt_reference_summary": ["PROMPT summary classify: +11 -2"]
}
```
