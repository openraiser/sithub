# `sit.info.v1`

Produced by:

```bash
sit info <package> --format json
```

Purpose: a machine-readable Skill Package state snapshot for agents, VS Code, and dashboards.

## Top-Level Fields

| Field | Type | Required | Meaning |
|---|---:|---:|---|
| `schema_version` | string | yes | Literal `sit.info.v1`. |
| `package` | object | yes | Package identity and manifest paths. |
| `git` | object | yes | Local Git state for the package root. |
| `files` | object | yes | Resolved prompt/schema/test manifest paths. |
| `validation` | object | yes | `sit validate` result. |
| `golden_tests` | object | yes | Static golden test result, skipped when validation fails. |
| `reports` | object | yes | Report directory state and latest report file. |

## Objects

`package`:

| Field | Type | Required |
|---|---:|---:|
| `name` | string | yes |
| `version` | string | yes |
| `description` | string or null | yes |
| `root` | string | yes |
| `manifest` | string | yes |

`git`:

| Field | Type | Required |
|---|---:|---:|
| `available` | boolean | yes |
| `root` | string or null | yes |
| `branch` | string or null | yes |
| `commit` | string or null | yes |
| `dirty` | boolean or null | yes |
| `changed_files_count` | integer or null | yes |

`files.prompts`, `files.schemas`, and `files.tests` are objects keyed by manifest alias. Each value has `path: string` and `exists: boolean`.

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

`reports`:

| Field | Type | Required |
|---|---:|---:|
| `path` | string | yes |
| `exists` | boolean | yes |
| `latest` | object or null | yes |

`reports.latest`, when present, contains `name`, `path`, `modified`, and `size_bytes`.

## Example

```json
{
  "schema_version": "sit.info.v1",
  "package": {
    "name": "paper-taxonomy-mapper",
    "version": "0.2.0",
    "description": "Classify AI research paper snippets with taxonomy, domain, evidence, and confidence.",
    "root": "/repo/examples/paper-taxonomy-mapper-v0.2.0",
    "manifest": "/repo/examples/paper-taxonomy-mapper-v0.2.0/skill.yaml"
  },
  "git": {
    "available": true,
    "root": "/repo",
    "branch": "main",
    "commit": "ba6c9ea",
    "dirty": true,
    "changed_files_count": 9
  },
  "files": {
    "prompts": {"classify": {"path": "/repo/examples/paper-taxonomy-mapper-v0.2.0/prompts/classify.md", "exists": true}},
    "schemas": {"output": {"path": "/repo/examples/paper-taxonomy-mapper-v0.2.0/schemas/output.schema.json", "exists": true}},
    "tests": {"golden": {"path": "/repo/examples/paper-taxonomy-mapper-v0.2.0/tests/golden.jsonl", "exists": true}}
  },
  "validation": {"status": "pass", "ok": true, "messages": ["OK  name: paper-taxonomy-mapper"]},
  "golden_tests": {"status": "pass", "ok": true, "passed": 3, "total": 3, "summary": "SUMMARY 3/3 golden cases passed", "messages": []},
  "reports": {"path": "/repo/examples/paper-taxonomy-mapper-v0.2.0/reports", "exists": true, "latest": null}
}
```
