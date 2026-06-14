from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import tempfile
from typing import Iterator

from .errors import SitError
from .git import git_root
from .package import SkillPackage, load_package
from .snapshot import archive_ref, archive_staged_index


@dataclass(frozen=True)
class GitRange:
    old: str
    new: str

    @property
    def display(self) -> str:
        return f"{self.old}..{self.new}"


def parse_git_range(value: str | None) -> GitRange | None:
    if not value or "..." in value or value.count("..") != 1:
        return None

    old, new = value.split("..", 1)
    if not old or not new:
        return None
    if old.startswith(".") or new.startswith("."):
        return None
    return GitRange(old=old, new=new)


@contextmanager
def load_package_pair(
    old_spec: str,
    new_spec: str | None = None,
    *,
    cwd: Path | None = None,
) -> Iterator[tuple[SkillPackage, SkillPackage]]:
    git_range = parse_git_range(old_spec) if new_spec is None else None
    if git_range is None:
        if new_spec is None:
            raise SitError("Expected a Git range like main..HEAD or two package paths")
        yield load_package(old_spec), load_package(new_spec)
        return

    with _load_git_range(git_range, cwd=cwd) as packages:
        yield packages


@contextmanager
def load_compare_package(
    current_spec: str,
    compare_spec: str | None,
    *,
    cwd: Path | None = None,
) -> Iterator[tuple[SkillPackage, SkillPackage | None]]:
    git_range = parse_git_range(compare_spec)
    if git_range is None:
        package = load_package(current_spec)
        compare = load_package(compare_spec) if compare_spec else None
        yield package, compare
        return

    effective_cwd = _effective_cwd(cwd)
    with _load_git_range(
        git_range,
        package_subpath=_package_subpath_for_spec(current_spec, cwd=effective_cwd),
        cwd=effective_cwd,
    ) as (old, new):
        yield new, old


@contextmanager
def load_staged_package(current_spec: str, *, cwd: Path | None = None) -> Iterator[SkillPackage]:
    effective_cwd = _effective_cwd(cwd)
    repo_root = git_root(effective_cwd)
    if repo_root is None:
        raise SitError("staged package requires running inside a Git work tree")

    package_subpath = _package_subpath_for_spec(current_spec, cwd=effective_cwd) or _current_package_subpath(repo_root, effective_cwd)
    with tempfile.TemporaryDirectory(prefix="sit-staged-") as tmp:
        staged_root = _snapshot_ref(repo_root, "STAGED", Path(tmp) / "staged")
        yield load_package(staged_root / package_subpath)


@contextmanager
def _load_git_range(
    git_range: GitRange,
    *,
    package_subpath: Path | None = None,
    cwd: Path | None = None,
) -> Iterator[tuple[SkillPackage, SkillPackage]]:
    effective_cwd = _effective_cwd(cwd)
    repo_root = git_root(effective_cwd)
    if repo_root is None:
        raise SitError(f"Git range requires running inside a Git work tree: {git_range.display}")

    package_subpath = package_subpath or _current_package_subpath(repo_root, effective_cwd)
    with tempfile.TemporaryDirectory(prefix="sit-ref-") as tmp:
        tmp_root = Path(tmp)
        old_root = _snapshot_ref(repo_root, git_range.old, tmp_root / "old")
        new_root = _snapshot_ref(repo_root, git_range.new, tmp_root / "new")
        yield load_package(old_root / package_subpath), load_package(new_root / package_subpath)


def _snapshot_ref(repo_root: Path, ref: str, destination: Path) -> Path:
    if _is_worktree_ref(ref):
        return repo_root
    if _is_staged_ref(ref):
        return archive_staged_index(repo_root, destination)
    return archive_ref(repo_root, ref, destination)


def _is_worktree_ref(ref: str) -> bool:
    return ref.upper() in {"WORKTREE", "WORKING"}


def _is_staged_ref(ref: str) -> bool:
    return ref.upper() in {"STAGED", "INDEX"}


def _package_subpath_for_spec(spec: str, *, cwd: Path | None = None) -> Path | None:
    spec_path = Path(spec).expanduser()
    if spec_path == Path("."):
        return None

    effective_cwd = _effective_cwd(cwd)
    repo_root = git_root(effective_cwd)
    if repo_root is None:
        return None

    path = (effective_cwd / spec_path).resolve() if not spec_path.is_absolute() else spec_path.resolve()
    if path.is_file() and path.name == "skill.yaml":
        path = path.parent
    if not path.exists() or (path != repo_root and repo_root not in path.parents):
        return None
    return path.relative_to(repo_root)


def _effective_cwd(cwd: Path | None) -> Path:
    return (cwd or Path.cwd()).resolve()


def _current_package_subpath(repo_root: Path, cwd: Path) -> Path:
    for candidate in (cwd, *cwd.parents):
        if repo_root != candidate and repo_root not in candidate.parents:
            break
        if (candidate / "skill.yaml").exists():
            return candidate.relative_to(repo_root)
        if candidate == repo_root:
            break
    return Path(".")
