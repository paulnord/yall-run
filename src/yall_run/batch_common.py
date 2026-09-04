from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
from typing import Any

from .model import CampaignSpec
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


def archive_wrapper(
    spec: CampaignSpec,
    campaign_dir: Path,
    backend: str,
) -> dict[str, Any] | None:
    wrapper = spec.condor.wrapper
    if not wrapper:
        return None
    source = logical_absolute(wrapper, spec.source.parent)
    if not source.is_file():
        raise ValueError(f"{backend} wrapper does not exist: {source}")
    environment_dir = campaign_dir / "environment"
    environment_dir.mkdir(exist_ok=True)
    suffix = "".join(source.suffixes)
    archived = environment_dir / f"{backend}-wrapper{suffix}"
    shutil.copy2(source, archived)
    archived.chmod(archived.stat().st_mode | 0o100)
    digest = hashlib.sha256(archived.read_bytes()).hexdigest()
    return {
        "source": str(source),
        "path": str(archived),
        "sha256": digest,
        "size_bytes": archived.stat().st_size,
    }


def bundle_worker(campaign_dir: Path, backend: str) -> Path:
    backend_dir = campaign_dir / backend
    worker_source = Path(__file__).with_name("worker.py").read_text()
    worker = backend_dir / "yall_worker.py"
    worker.write_text(worker_source)
    worker.chmod(0o755)
    return worker


def worker_command(
    worker: Path,
    campaign_dir: Path,
    task_name: str,
    archived_wrapper: Path | None,
) -> str:
    command = (
        f"/usr/bin/env python3 {shlex.quote(str(worker))} "
        f"{shlex.quote(str(campaign_dir))} {shlex.quote(task_name)}"
    )
    if archived_wrapper is not None:
        command = f"{shlex.quote(str(archived_wrapper))} {command}"
    return command


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
