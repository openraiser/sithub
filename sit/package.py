from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import yaml

from .errors import SitError


@dataclass(frozen=True)
class SkillPackage:
    root: Path
    manifest_path: Path
    manifest: dict[str, Any]

    @property
    def name(self) -> str | None:
        value = self.manifest.get("name")
        return value if isinstance(value, str) else None

    @property
    def version(self) -> str | None:
        value = self.manifest.get("version")
        return str(value) if value is not None else None

    @property
    def description(self) -> str | None:
        value = self.manifest.get("description")
        return value if isinstance(value, str) else None

    def resolve_manifest_path(self, value: str) -> Path:
        return (self.root / value).resolve()

    def prompt_paths(self) -> dict[str, Path]:
        return _resolve_path_map(self, "prompts")

    def schema_paths(self) -> dict[str, Path]:
        return _resolve_path_map(self, "schemas")

    def test_paths(self) -> dict[str, Path]:
        return _resolve_path_map(self, "tests")

    def report_dir(self) -> Path:
        return (self.root / "reports").resolve()


def load_package(package_path: str | Path) -> SkillPackage:
    root = Path(package_path).expanduser().resolve()
    if root.is_file():
        if root.name != "skill.yaml":
            raise SitError(f"Expected a package directory or skill.yaml, got file: {root}")
        manifest_path = root
        root = root.parent
    else:
        manifest_path = root / "skill.yaml"

    if not manifest_path.exists():
        raise SitError(f"Missing skill.yaml: {manifest_path}")

    try:
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SitError(f"Invalid YAML in {manifest_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise SitError(f"skill.yaml must contain a mapping: {manifest_path}")

    return SkillPackage(root=root, manifest_path=manifest_path, manifest=data)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SitError(f"Invalid JSON in {path}: line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SitError(f"Invalid JSONL in {path}: line {index}: {exc.msg}") from exc
        if not isinstance(record, dict):
            raise SitError(f"JSONL line {index} in {path} must be an object")
        records.append(record)
    return records


def _resolve_path_map(package: SkillPackage, key: str) -> dict[str, Path]:
    value = package.manifest.get(key, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SitError(f"skill.yaml field '{key}' must be a mapping")

    paths: dict[str, Path] = {}
    for name, relative_path in value.items():
        if isinstance(relative_path, str):
            paths[str(name)] = package.resolve_manifest_path(relative_path)
        else:
            raise SitError(f"skill.yaml field '{key}.{name}' must be a path string")
    return paths
