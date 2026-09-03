from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Tuple, Union

from .paths import logical_absolute


Command = Union[str, Tuple[str, ...]]


@dataclass(frozen=True)
class FileRef:
    path: str
    role: str | None = None


@dataclass(frozen=True)
class ResourceSpec:
    cpus: int | None = None
    memory: str | None = None
    disk: str | None = None


@dataclass(frozen=True)
class CondorSpec:
    request_cpus: int = 1
    request_memory: str = "2GB"
    request_disk: str = "2GB"
    getenv: bool = True
    wrapper: str | None = None


@dataclass(frozen=True)
class TaskSpec:
    name: str
    command: Command
    cwd: str | None = None
    parents: Tuple[str, ...] = ()
    retries: int = 0
    inputs: Tuple[FileRef, ...] = ()
    outputs: Tuple[FileRef, ...] = ()
    resources: ResourceSpec = ResourceSpec()


@dataclass(frozen=True)
class CampaignSpec:
    name: str
    tasks: Tuple[TaskSpec, ...]
    source: Path
    backend: str = "local"
    condor: CondorSpec = CondorSpec()


def _validate_graph(tasks: list[TaskSpec]) -> None:
    by_name = {task.name: task for task in tasks}
    if len(by_name) != len(tasks):
        seen: set[str] = set()
        for task in tasks:
            if task.name in seen:
                raise ValueError(f"duplicate task name: {task.name}")
            seen.add(task.name)
    for task in tasks:
        unknown = [parent for parent in task.parents if parent not in by_name]
        if unknown:
            raise ValueError(
                f"task {task.name!r} has unknown parents: {', '.join(unknown)}"
            )
        if task.name in task.parents:
            raise ValueError(f"task {task.name!r} cannot depend on itself")

    state: dict[str, int] = {}

    def visit(name: str) -> None:
        if state.get(name) == 1:
            raise ValueError(f"dependency cycle involving {name!r}")
        if state.get(name) == 2:
            return
        state[name] = 1
        for parent in by_name[name].parents:
            visit(parent)
        state[name] = 2

    for name in by_name:
        visit(name)


def load_spec(path: str | Path) -> CampaignSpec:
    source = logical_absolute(path)
    if source.suffix.lower() == ".toml":
        raise ValueError(
            "TOML campaign files are no longer supported; use Yawlfile syntax"
        )

    from .syntax import load_yawl_spec

    # Relative paths in a Yawlfile belong to the workflow, not to whichever
    # directory happened to invoke yawl-run. The syntax loader uses ordinary
    # glob operations while expanding @each and input patterns, so parse from
    # the Yawlfile directory and then restore the caller's working directory.
    previous_cwd = Path.cwd()
    try:
        os.chdir(source.parent)
        return load_yawl_spec(source)
    finally:
        os.chdir(previous_cwd)
