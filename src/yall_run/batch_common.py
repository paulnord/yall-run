from __future__ import annotations

import json
from pathlib import Path
import re
import shlex
from typing import Any

from .paths import logical_absolute


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, indent=2, sort_keys=True) + "\n"
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(text)
    temp.replace(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def slug(name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-.")
    return value or "task"


def bundle_worker(campaign_dir: Path, backend: str) -> Path:
    backend_dir = campaign_dir / backend
    worker_source = Path(__file__).with_name("worker.py").read_text()
    worker = backend_dir / "yall_worker.py"
    worker.write_text(worker_source)
    worker.chmod(0o755)
    return worker


def worker_command(worker: Path, campaign_dir: Path, task_name: str) -> str:
    # Only the host-native Python worker belongs in a scheduler launch script.
    # The frozen campaign tells that worker how to wrap the scientific payload.
    return shlex.join(["/usr/bin/env", "python3", str(worker), str(campaign_dir), task_name])


def retry_shell(command: str, retries: int) -> str:
    attempts = retries + 1
    return (
        f"max_attempts={attempts}\n"
        "attempt=0\n"
        "rc=1\n"
        "while [ \"$attempt\" -lt \"$max_attempts\" ]; do\n"
        "    attempt=$((attempt + 1))\n"
        f"    if {command}; then\n"
        "        exit 0\n"
        "    else\n"
        "        rc=$?\n"
        "    fi\n"
        "done\n"
        "exit \"$rc\"\n"
    )


def normalize_memory(value: str, backend: str) -> str:
    text = value.strip()
    match = re.fullmatch(r"(\d+)\s*([KMGTPE]?)\s*(?:I?B)?", text, re.IGNORECASE)
    if not match:
        return text
    amount, unit = match.groups()
    unit = unit.upper()
    if backend == "slurm":
        return amount + unit
    if backend == "pbs":
        return amount + (unit.lower() + "b" if unit else "b")
    return text


def load_backend_campaign(
    campaign_dir: str | Path,
    backend: str,
) -> tuple[Path, dict[str, Any]]:
    campaign_dir = logical_absolute(campaign_dir)
    manifest_path = campaign_dir / "campaign.json"
    if not manifest_path.is_file():
        raise ValueError(f"not a yall campaign: {campaign_dir}")
    manifest = read_json(manifest_path)
    if manifest.get("backend") != backend:
        raise ValueError(f"campaign backend is not {backend}: {campaign_dir}")
    return campaign_dir, manifest


def campaign_task_names(manifest: dict[str, Any]) -> list[str]:
    order = manifest.get("task_order")
    if isinstance(order, list):
        return [str(name) for name in order]
    tasks = manifest.get("tasks", [])
    if isinstance(tasks, dict):
        return [str(name) for name in tasks]
    return [str(name) for name in tasks]


def campaign_task_definition(
    campaign_dir: Path,
    manifest: dict[str, Any],
    task_name: str,
) -> dict[str, Any]:
    tasks = manifest.get("tasks", {})
    if isinstance(tasks, dict):
        task = tasks.get(task_name)
        if not isinstance(task, dict):
            raise ValueError(f"unknown task: {task_name}")
        return task
    path = campaign_dir / "tasks" / f"{task_name}.json"
    if not path.is_file():
        raise ValueError(f"unknown task: {task_name}")
    return read_json(path)
