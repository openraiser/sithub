from __future__ import annotations

import io
from pathlib import Path
import subprocess
import tarfile

from .errors import SitError
from .git import git_output


def archive_ref(repo_root: Path, ref: str, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    command = ["git", "archive", "--format=tar", ref]
    try:
        completed = subprocess.run(command, cwd=repo_root, check=False, capture_output=True)
    except FileNotFoundError as exc:
        raise SitError("git executable not found") from exc

    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise SitError(f"git archive failed for {ref}" + (f": {message}" if message else ""))

    with tarfile.open(fileobj=io.BytesIO(completed.stdout), mode="r:") as archive:
        safe_extract(archive, destination)
    return destination


def archive_staged_index(repo_root: Path, destination: Path) -> Path:
    tree = git_output(["write-tree"], cwd=repo_root)
    return archive_ref(repo_root, tree, destination)


def safe_extract(archive: tarfile.TarFile, destination: Path) -> None:
    destination = destination.resolve()
    for member in archive.getmembers():
        target = (destination / member.name).resolve()
        if target != destination and destination not in target.parents:
            raise SitError(f"Unsafe path in git archive: {member.name}")
    try:
        archive.extractall(destination, filter="data")
    except TypeError:
        archive.extractall(destination)
