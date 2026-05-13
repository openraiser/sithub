from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import SitError
from .package import SkillPackage, load_json, load_jsonl


@dataclass(frozen=True)
class DiffEvent:
    message: str
    category: str
    changed: bool = False
    breaking: bool = False

    @property
    def severity(self) -> str:
        if self.breaking:
            return "breaking"
        if self.changed:
            return "changed"
        return "info"

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "severity": self.severity,
            "changed": self.changed,
            "breaking": self.breaking,
            "message": self.message,
        }


@dataclass
class PackageDiff:
    messages: list[str] = field(default_factory=list)
    events: list[DiffEvent] = field(default_factory=list)
    breaking: bool = False
    changed: bool = False

    def add(self, message: str, *, changed: bool = False, breaking: bool = False, category: str | None = None) -> None:
        self.messages.append(message)
        self.events.append(
            DiffEvent(
                message=message,
                category=category or _infer_category(message),
                changed=changed or breaking,
                breaking=breaking,
            )
        )
        self.changed = self.changed or changed or breaking
        self.breaking = self.breaking or breaking

    @property
    def risk(self) -> str:
        if self.breaking:
            return "breaking-change"
        if self.changed:
            return "review-required"
        return "no-change"

    @property
    def suggested_bump(self) -> str:
        if self.breaking:
            return "major"
        if self.changed:
            return "minor"
        return "patch"

    def to_dict(
        self,
        old: SkillPackage,
        new: SkillPackage,
        *,
        old_source: str | None = None,
        new_source: str | None = None,
    ) -> dict[str, Any]:
        return {
            "schema_version": "sit.diff.v1",
            "old": _package_ref(old, source=old_source),
            "new": _package_ref(new, source=new_source),
            "changed": self.changed,
            "breaking": self.breaking,
            "risk": self.risk,
            "suggested_bump": self.suggested_bump,
            "messages": self.messages,
            "events": [event.to_dict() for event in self.events],
        }


def diff_packages(old: SkillPackage, new: SkillPackage) -> PackageDiff:
    result = PackageDiff()
    result.add(
        f"PACKAGE {old.name or '<unknown>'}@{old.version or '<unknown>'} -> {new.name or '<unknown>'}@{new.version or '<unknown>'}",
        category="package",
    )

    _diff_manifest(result, old.manifest, new.manifest)
    _diff_path_group(result, "prompt", old.prompt_paths(), new.prompt_paths(), compare_text=True)
    _diff_schema_group(result, old.schema_paths(), new.schema_paths())
    _diff_path_group(result, "test", old.test_paths(), new.test_paths(), compare_text=True)
    _diff_golden_cases(result, old, new)

    if result.breaking:
        result.add("RISK breaking-change", breaking=True, category="risk")
    elif result.changed:
        result.add("RISK review-required", changed=True, category="risk")
    else:
        result.add("RISK no-change", category="risk")

    return result


def _diff_manifest(result: PackageDiff, old: dict[str, Any], new: dict[str, Any]) -> None:
    ignored = {"prompts", "schemas", "tests"}
    old_keys = set(old) - ignored
    new_keys = set(new) - ignored

    for key in sorted(new_keys - old_keys):
        result.add(f"MANIFEST added {key}", changed=True)
    for key in sorted(old_keys - new_keys):
        result.add(f"MANIFEST removed {key}", changed=True)
    for key in sorted(old_keys & new_keys):
        if old[key] != new[key]:
            result.add(f"MANIFEST changed {key}: {_short(old[key])} -> {_short(new[key])}", changed=True)


def _diff_path_group(
    result: PackageDiff,
    label: str,
    old_paths: dict[str, Path],
    new_paths: dict[str, Path],
    *,
    compare_text: bool,
) -> None:
    old_names = set(old_paths)
    new_names = set(new_paths)

    for name in sorted(new_names - old_names):
        result.add(f"{label.upper()} added {name}: {new_paths[name]}", changed=True)
    for name in sorted(old_names - new_names):
        result.add(f"{label.upper()} removed {name}: {old_paths[name]}", changed=True)

    if not compare_text:
        return

    for name in sorted(old_names & new_names):
        old_path = old_paths[name]
        new_path = new_paths[name]
        if not old_path.exists() or not new_path.exists():
            continue
        if old_path.read_text(encoding="utf-8") != new_path.read_text(encoding="utf-8"):
            result.add(f"{label.upper()} changed {name}: {old_path.name} -> {new_path.name}", changed=True)


def _diff_schema_group(result: PackageDiff, old_paths: dict[str, Path], new_paths: dict[str, Path]) -> None:
    _diff_path_group(result, "schema", old_paths, new_paths, compare_text=False)

    for name in sorted(set(old_paths) & set(new_paths)):
        old_path = old_paths[name]
        new_path = new_paths[name]
        if not old_path.exists() or not new_path.exists():
            continue

        old_schema = load_json(old_path)
        new_schema = load_json(new_path)
        if old_schema == new_schema:
            continue

        result.add(f"SCHEMA changed {name}: {old_path.name} -> {new_path.name}", changed=True)
        _diff_schema_node(result, name, old_schema, new_schema, path="")


def _diff_schema_node(result: PackageDiff, name: str, old_schema: Any, new_schema: Any, *, path: str) -> None:
    if not isinstance(old_schema, dict) or not isinstance(new_schema, dict):
        return

    _diff_schema_type(result, name, old_schema, new_schema, path=path)
    _diff_schema_enum(result, name, old_schema, new_schema, path=path)
    _diff_schema_additional_properties(result, name, old_schema, new_schema, path=path)
    _diff_schema_bounds(result, name, old_schema, new_schema, path=path)
    _diff_schema_items(result, name, old_schema, new_schema, path=path)
    _diff_schema_properties(result, name, old_schema, new_schema, path=path)


def _diff_schema_properties(result: PackageDiff, name: str, old_schema: dict[str, Any], new_schema: dict[str, Any], *, path: str) -> None:
    old_properties = _schema_properties(old_schema)
    new_properties = _schema_properties(new_schema)
    old_required = _schema_required(old_schema)
    new_required = _schema_required(new_schema)

    old_property_names = set(old_properties)
    new_property_names = set(new_properties)

    for field in sorted(new_property_names - old_property_names):
        marker = "required" if field in new_required else "optional"
        field_path = _join_schema_path(path, field)
        result.add(f"SCHEMA {name} property added {field_path} ({marker})", changed=True, breaking=field in new_required)
    for field in sorted(old_property_names - new_property_names):
        field_path = _join_schema_path(path, field)
        result.add(f"SCHEMA {name} property removed {field_path}", breaking=True)

    for field in sorted(old_property_names & new_property_names):
        field_path = _join_schema_path(path, field)
        _diff_schema_node(result, name, old_properties[field], new_properties[field], path=field_path)

    for field in sorted(new_required - old_required):
        if field in old_properties:
            field_path = _join_schema_path(path, field)
            result.add(f"SCHEMA {name} property became required {field_path}", breaking=True)
    for field in sorted(old_required - new_required):
        if field in new_properties:
            field_path = _join_schema_path(path, field)
            result.add(f"SCHEMA {name} property became optional {field_path}", changed=True)


def _diff_schema_type(result: PackageDiff, name: str, old_schema: dict[str, Any], new_schema: dict[str, Any], *, path: str) -> None:
    old_type = _schema_type(old_schema)
    new_type = _schema_type(new_schema)
    if old_type == new_type:
        return

    label = _schema_label(path)
    if path:
        result.add(f"SCHEMA {name} property type changed {label}: {old_type} -> {new_type}", breaking=True)
    else:
        result.add(f"SCHEMA {name} root type changed: {old_type} -> {new_type}", breaking=True)


def _diff_schema_enum(result: PackageDiff, name: str, old_schema: dict[str, Any], new_schema: dict[str, Any], *, path: str) -> None:
    old_enum = _schema_enum(old_schema)
    new_enum = _schema_enum(new_schema)
    if old_enum is None and new_enum is None:
        return

    label = _schema_label(path)
    if old_enum is None:
        result.add(f"SCHEMA {name} enum added {label}: {_short_sorted(new_enum or set())}", breaking=True)
        return
    if new_enum is None:
        result.add(f"SCHEMA {name} enum removed {label}", changed=True)
        return

    removed = old_enum - new_enum
    added = new_enum - old_enum
    if removed:
        result.add(f"SCHEMA {name} enum removed values {label}: {_short_sorted(removed)}", breaking=True)
    if added:
        result.add(f"SCHEMA {name} enum added values {label}: {_short_sorted(added)}", changed=True)


def _diff_schema_additional_properties(
    result: PackageDiff,
    name: str,
    old_schema: dict[str, Any],
    new_schema: dict[str, Any],
    *,
    path: str,
) -> None:
    old_value = old_schema.get("additionalProperties", True)
    new_value = new_schema.get("additionalProperties", True)
    if old_value == new_value:
        return

    label = _schema_label(path)
    if old_value is not False and new_value is False:
        result.add(f"SCHEMA {name} additionalProperties restricted {label}: {_short(old_value)} -> False", breaking=True)
    elif old_value is False and new_value is not False:
        result.add(f"SCHEMA {name} additionalProperties relaxed {label}: False -> {_short(new_value)}", changed=True)
    else:
        result.add(
            f"SCHEMA {name} additionalProperties changed {label}: {_short(old_value)} -> {_short(new_value)}",
            changed=True,
            breaking=isinstance(old_value, dict) or new_value is False,
        )


def _diff_schema_bounds(result: PackageDiff, name: str, old_schema: dict[str, Any], new_schema: dict[str, Any], *, path: str) -> None:
    for key in ("minLength", "minItems", "minimum", "exclusiveMinimum", "minProperties"):
        _diff_lower_bound(result, name, old_schema, new_schema, key, path=path)
    for key in ("maxLength", "maxItems", "maximum", "exclusiveMaximum", "maxProperties"):
        _diff_upper_bound(result, name, old_schema, new_schema, key, path=path)


def _diff_lower_bound(
    result: PackageDiff,
    name: str,
    old_schema: dict[str, Any],
    new_schema: dict[str, Any],
    key: str,
    *,
    path: str,
) -> None:
    old_value = old_schema.get(key)
    new_value = new_schema.get(key)
    if old_value == new_value:
        return

    label = _schema_label(path)
    breaking = _is_number(old_value) and _is_number(new_value) and new_value > old_value
    if old_value is None and new_value is not None:
        breaking = True
    result.add(f"SCHEMA {name} constraint changed {label}.{key}: {_short(old_value)} -> {_short(new_value)}", changed=True, breaking=breaking)


def _diff_upper_bound(
    result: PackageDiff,
    name: str,
    old_schema: dict[str, Any],
    new_schema: dict[str, Any],
    key: str,
    *,
    path: str,
) -> None:
    old_value = old_schema.get(key)
    new_value = new_schema.get(key)
    if old_value == new_value:
        return

    label = _schema_label(path)
    breaking = _is_number(old_value) and _is_number(new_value) and new_value < old_value
    if old_value is None and new_value is not None:
        breaking = True
    result.add(f"SCHEMA {name} constraint changed {label}.{key}: {_short(old_value)} -> {_short(new_value)}", changed=True, breaking=breaking)


def _diff_schema_items(result: PackageDiff, name: str, old_schema: dict[str, Any], new_schema: dict[str, Any], *, path: str) -> None:
    if "items" not in old_schema and "items" not in new_schema:
        return
    if "items" not in old_schema:
        result.add(f"SCHEMA {name} items added {_schema_label(path)}", breaking=True)
        return
    if "items" not in new_schema:
        result.add(f"SCHEMA {name} items removed {_schema_label(path)}", changed=True)
        return

    old_items = old_schema["items"]
    new_items = new_schema["items"]
    if old_items == new_items:
        return
    if isinstance(old_items, dict) and isinstance(new_items, dict):
        _diff_schema_node(result, name, old_items, new_items, path=_join_schema_path(path, "[]"))
    else:
        result.add(f"SCHEMA {name} items changed {_schema_label(path)}: {_short(old_items)} -> {_short(new_items)}", breaking=True)


def _diff_golden_cases(result: PackageDiff, old: SkillPackage, new: SkillPackage) -> None:
    old_tests = old.test_paths()
    new_tests = new.test_paths()
    if "golden" not in old_tests or "golden" not in new_tests:
        return
    old_path = old_tests["golden"]
    new_path = new_tests["golden"]
    if not old_path.exists() or not new_path.exists():
        return

    old_cases = _case_map(load_jsonl(old_path))
    new_cases = _case_map(load_jsonl(new_path))

    old_case_ids = set(old_cases)
    new_case_ids = set(new_cases)

    for case_id in sorted(new_case_ids - old_case_ids):
        result.add(f"GOLDEN case added {case_id}", changed=True)
    for case_id in sorted(old_case_ids - new_case_ids):
        result.add(f"GOLDEN case removed {case_id}", changed=True)
    for case_id in sorted(old_case_ids & new_case_ids):
        if old_cases[case_id].get("expected") != new_cases[case_id].get("expected"):
            result.add(f"GOLDEN expected changed {case_id}", changed=True)


def _case_map(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    cases: dict[str, dict[str, Any]] = {}
    for index, record in enumerate(records, start=1):
        case_id = record.get("case_id", f"line-{index}")
        if not isinstance(case_id, str):
            raise SitError(f"golden case_id at line {index} must be a string")
        cases[case_id] = record
    return cases


def _schema_properties(schema: dict[str, Any]) -> dict[str, Any]:
    properties = schema.get("properties", {})
    return properties if isinstance(properties, dict) else {}


def _schema_required(schema: dict[str, Any]) -> set[str]:
    required = schema.get("required", [])
    return {item for item in required if isinstance(item, str)} if isinstance(required, list) else set()


def _schema_enum(schema: dict[str, Any]) -> set[str] | None:
    values = schema.get("enum")
    if not isinstance(values, list):
        return None
    return {repr(value) for value in values}


def _schema_type(schema: Any) -> str:
    if not isinstance(schema, dict):
        return "<unknown>"
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        return "|".join(str(item) for item in schema_type)
    if schema_type is None:
        return "<unspecified>"
    return str(schema_type)


def _join_schema_path(base: str, part: str) -> str:
    if not base:
        return part
    if part == "[]":
        return f"{base}[]"
    return f"{base}.{part}"


def _schema_label(path: str) -> str:
    return path or "<root>"


def _short_sorted(values: set[str]) -> str:
    text = ", ".join(sorted(values))
    return text if len(text) <= 80 else text[:77] + "..."


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _short(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= 80 else text[:77] + "..."


def _infer_category(message: str) -> str:
    head = message.split(maxsplit=1)[0].lower() if message else "unknown"
    return "".join(char for char in head if char.isalnum() or char in {"_", "-"}) or "unknown"


def _package_ref(package: SkillPackage, *, source: str | None = None) -> dict[str, str]:
    data = {
        "name": package.name or "<unknown>",
        "version": package.version or "<unknown>",
        "root": str(package.root),
        "manifest": str(package.manifest_path),
    }
    if source is not None:
        data["source"] = source
    return data
