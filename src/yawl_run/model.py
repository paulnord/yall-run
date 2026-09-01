from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Tuple, Union

from .paths import logical_absolute

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.9 and 3.10
    import tomli as tomllib


Command = Union[str, Tuple[str, ...]]


@dataclass(frozen=True)
class FileRef:
    path: str
    role: str | None = None


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


@dataclass(frozen=True)
class CampaignSpec:
    name: str
    tasks: Tuple[TaskSpec, ...]
    source: Path
    backend: str = "local"
    condor: CondorSpec = CondorSpec()


def _validate_graph(tasks: list[TaskSpec]) -> None:
    by_name = {task.name: task for task in tasks}
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


def _parse_command(value: object, task_name: str) -> Command:
    if isinstance(value, str):
        command = value.strip()
        if not command:
            raise ValueError(f"task {task_name!r} needs a command")
        return command
    if isinstance(value, list):
        command = tuple(str(item) for item in value)
        if not command or not command[0]:
            raise ValueError(f"task {task_name!r} needs a command")
        return command
    raise ValueError(f"task {task_name!r} command must be a string or array")


def _parse_refs(value: object, task_name: str, field: str) -> Tuple[FileRef, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"task {task_name!r} {field} must be an array")
    refs: list[FileRef] = []
    for item in value:
        if isinstance(item, str):
            if not item:
                raise ValueError(f"task {task_name!r} has an empty {field} path")
            refs.append(FileRef(path=item))
            continue
        if not isinstance(item, dict):
            raise ValueError(
                f"task {task_name!r} {field} entries must be paths or tables"
            )
        path = str(item.get("path", "")).strip()
        if not path:
            raise ValueError(f"task {task_name!r} has a {field} entry without path")
        role_value = item.get("role")
        role = str(role_value).strip() if role_value is not None else None
        refs.append(FileRef(path=path, role=role or None))
    return tuple(refs)


def load_spec(path: str | Path) -> CampaignSpec:
    source = logical_absolute(path)
    with source.open("rb") as fh:
        data = tomllib.load(fh)

    campaign = data.get("campaign", {})
    name = str(campaign.get("name", "")).strip()
    if not name:
        raise ValueError("[campaign].name is required")

    backend = str(campaign.get("backend", "local")).strip().lower()
    if backend not in {"local", "condor"}:
        raise ValueError("[campaign].backend must be 'local' or 'condor'")

    raw_condor = data.get("condor", {})
    request_cpus = int(raw_condor.get("request_cpus", 1))
    if request_cpus < 1:
        raise ValueError("[condor].request_cpus must be positive")
    wrapper_value = raw_condor.get("wrapper")
    condor = CondorSpec(
        request_cpus=request_cpus,
        request_memory=str(raw_condor.get("request_memory", "2GB")),
        request_disk=str(raw_condor.get("request_disk", "2GB")),
        getenv=bool(raw_condor.get("getenv", True)),
        wrapper=str(wrapper_value) if wrapper_value else None,
    )

    raw_tasks = data.get("task", [])
    if not raw_tasks:
        raise ValueError("at least one [[task]] is required")

    seen: set[str] = set()
    tasks: list[TaskSpec] = []
    for item in raw_tasks:
        task_name = str(item.get("name", "")).strip()
        if not task_name:
            raise ValueError("each [[task]] needs a name")
        if task_name in seen:
            raise ValueError(f"duplicate task name: {task_name}")
        command = _parse_command(item.get("command"), task_name)
        retries = int(item.get("retries", 0))
        if retries < 0:
            raise ValueError(f"task {task_name!r} retries may not be negative")
        parents = tuple(str(value) for value in item.get("parents", []))
        inputs = _parse_refs(item.get("inputs"), task_name, "inputs")
        outputs = _parse_refs(item.get("outputs"), task_name, "outputs")
        seen.add(task_name)
        cwd = item.get("cwd")
        tasks.append(
            TaskSpec(
                name=task_name,
                command=command,
                cwd=str(cwd) if cwd else None,
                parents=parents,
                retries=retries,
                inputs=inputs,
                outputs=outputs,
            )
        )

    _validate_graph(tasks)
    return CampaignSpec(
        name=name,
        tasks=tuple(tasks),
        source=source,
        backend=backend,
        condor=condor,
    )
