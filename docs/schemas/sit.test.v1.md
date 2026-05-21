# `sit.test.v1`

Produced by:

```bash
sit test <package> --format json
sit test <package> --run --format json
```

Purpose: machine-readable validation plus golden behavior result for agents and CI.

## Top-Level Fields

| Field | Type | Required | Meaning |
|---|---:|---:|---|
| `schema_version` | string | yes | Literal `sit.test.v1`. |
| `package` | object | yes | Package identity and manifest paths. |
| `execution` | object | yes | Static or runner-backed execution mode. |
| `validation` | object | yes | Manifest/path/schema validation result. |
| `golden_tests` | object | yes | Golden case pass/fail summary. |

## Objects

`package`:

| Field | Type | Required |
|---|---:|---:|
| `name` | string | yes |
| `version` | string | yes |
| `root` | string | yes |
| `manifest` | string | yes |

`execution`:

| Field | Type | Required |
|---|---:|---:|
| `mode` | string enum: `static`, `runner` | yes |
| `runner` | string or null | yes |
| `timeout_seconds` | integer or null | yes |

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
| `passed` | integer or null | yes |
| `total` | integer or null | yes |
| `summary` | string or null | yes |
| `messages` | string[] | yes |

## Example

```json
{
  "schema_version": "sit.test.v1",
  "package": {
    "name": "paper-taxonomy-mapper",
    "version": "0.2.0",
    "root": "/repo/examples/paper-taxonomy-mapper-v0.2.0",
    "manifest": "/repo/examples/paper-taxonomy-mapper-v0.2.0/skill.yaml"
  },
  "execution": {
    "mode": "static",
    "runner": null,
    "timeout_seconds": null
  },
  "validation": {"status": "pass", "ok": true, "messages": ["OK  status: active"]},
  "golden_tests": {
    "status": "pass",
    "ok": true,
    "passed": 3,
    "total": 3,
    "summary": "SUMMARY 3/3 golden cases passed",
    "messages": ["SUMMARY 3/3 golden cases passed"]
  }
}
```
