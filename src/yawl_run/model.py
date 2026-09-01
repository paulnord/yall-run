from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class TaskSpec:
    name: str
    command: str
    cwd: str | None = None


@dataclass(frozen=True)
class CampaignSpec:
    name: str
    tasks: tuple[TaskSpec, ...]
    source: Path


def load_spec(path: str | Path) -> CampaignSpec:
    source = Path(path).resolve()
    with source.open("rb") as fh:
        data = tomllib.load(fh)

    campaign = data.get("campaign", {})
    name = str(campaign.get("name", "")).strip()
    if not name:
        raise ValueError("[campaign].name is required")

    raw_tasks = data.get("task", [])
    if not raw_tasks:
        raise ValueError("at least one [[task]] is required")

    seen: set[str] = set()
    tasks: list[TaskSpec] = []
    for item in raw_tasks:
        task_name = str(item.get("name", "")).strip()
        command = str(item.get("command", "")).strip()
        if not task_name:
            raise ValueError("each [[task]] needs a name")
        if task_name in seen:
            raise ValueError(f"duplicate task name: {task_name}")
        if not command:
            raise ValueError(f"task {task_name!r} needs a command")
        seen.add(task_name)
        cwd = item.get("cwd")
        tasks.append(TaskSpec(task_name, command, str(cwd) if cwd else None))

    return CampaignSpec(name=name, tasks=tuple(tasks), source=source)
