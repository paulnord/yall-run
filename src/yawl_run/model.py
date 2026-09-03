from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path
import shlex
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
    overwrite: bool = False


@dataclass(frozen=True)
class CampaignSpec:
    name: str
    tasks: Tuple[TaskSpec, ...]
    source: Path
    backend: str = "local"
    condor: CondorSpec = CondorSpec()
    set_values: Tuple[Tuple[str, str], ...] = ()


def _validate_graph(tasks: list[TaskSpec], base_dir: Path | None = None) -> None:
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

    if base_dir is not None:
        owners: dict[str, str] = {}
        for task in tasks:
            task_cwd = logical_absolute(task.cwd, base_dir) if task.cwd else base_dir
            for output in task.outputs:
                path = str(logical_absolute(output.path, task_cwd))
                previous = owners.get(path)
                if previous is not None and previous != task.name:
                    raise ValueError(
                        f"declared output {path!r} is owned by both "
                        f"{previous!r} and {task.name!r}"
                    )
                owners[path] = task.name


def _set_values(text: str) -> Tuple[Tuple[str, str], ...]:
    values: dict[str, str] = {}
    pending: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        pending = (pending + " " + line.lstrip()) if pending is not None else line
        if pending.rstrip().endswith("\\"):
            pending = pending.rstrip()[:-1].rstrip()
            continue
        stripped = pending.strip()
        if pending[:1] and not pending[:1].isspace():
            if stripped.startswith("@set "):
                parts = shlex.split(stripped)
                if len(parts) == 3:
                    values[parts[1]] = parts[2]
            elif stripped.startswith("@env "):
                parts = shlex.split(stripped)
                if len(parts) == 2 and parts[1] in os.environ:
                    values[parts[1]] = os.environ[parts[1]]
        pending = None
    return tuple(values.items())


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
        spec = load_yawl_spec(source)
        return replace(spec, set_values=_set_values(source.read_text()))
    finally:
        os.chdir(previous_cwd)
