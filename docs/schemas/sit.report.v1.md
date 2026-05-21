# `sit.report.v1`

Produced by:

```bash
sit report <package> --format json
sit report <package> --compare <baseline> --format json
```

Purpose: full machine-readable validation, test, semantic diff, and reproducibility report.

## Top-Level Fields

| Field | Type | Required | Meaning |
|---|---:|---:|---|
| `schema_version` | string | yes | Literal `sit.report.v1`. |
| `date` | string | yes | Local report date in `YYYY-MM-DD`. |
| `package` | object | yes | Current package identity. |
| `validation` | object | yes | `sit validate` result. |
| `golden_tests` | object | yes | Static golden test result, skipped when validation fails. |
| `diff` | object or null | yes | Embedded `sit.diff.v1` payload when `--compare` is supplied. |
| `reproducibility` | object | yes | Commands to reproduce validate/test/diff. |

## Objects

`package`:

| Field | Type | Required |
|---|---:|---:|
| `name` | string | yes |
| `version` | string | yes |
| `description` | string or null | yes |
| `root` | string | yes |
| `manifest` | string | yes |
| `source` | string | no |

`validation` and `golden_tests` use the same shapes as `sit.test.v1`.

`diff`, when present, is the `sit.diff.v1` object:

| Field | Type | Required |
|---|---:|---:|
| `schema_version` | string | yes |
| `old` | object | yes |
| `new` | object | yes |
| `changed` | boolean | yes |
| `breaking` | boolean | yes |
| `risk` | string enum: `no-change`, `review-required`, `breaking-change` | yes |
| `suggested_bump` | string enum: `patch`, `minor`, `major` | yes |
| `messages` | string[] | yes |
| `events` | object[] | yes |
| `text_diffs` | object[] | yes |

Each `events[]` entry contains `category`, `severity`, `changed`, `breaking`, `message`, and optional `details`. `STATUS changed active -> deprecated` events use category `status`; `retired` transitions are breaking.

`reproducibility`:

| Field | Type | Required |
|---|---:|---:|
| `validate` | string | yes |
| `test` | string | yes |
| `diff` | string or null | yes |

## Example

```json
{
  "schema_version": "sit.report.v1",
  "date": "2026-05-22",
  "package": {
    "name": "paper-taxonomy-mapper",
    "version": "0.2.0",
    "description": "Classify AI research paper snippets with taxonomy, domain, evidence, and confidence.",
    "root": "/repo/examples/paper-taxonomy-mapper-v0.2.0",
    "manifest": "/repo/examples/paper-taxonomy-mapper-v0.2.0/skill.yaml"
  },
  "validation": {"status": "pass", "ok": true, "messages": []},
  "golden_tests": {"status": "pass", "ok": true, "passed": 3, "total": 3, "summary": "SUMMARY 3/3 golden cases passed", "messages": []},
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
  "reproducibility": {
    "validate": "python3 -m sit.cli validate /repo/examples/paper-taxonomy-mapper-v0.2.0",
    "test": "python3 -m sit.cli test /repo/examples/paper-taxonomy-mapper-v0.2.0",
    "diff": "python3 -m sit.cli diff /repo/old /repo/new"
  }
}
```
