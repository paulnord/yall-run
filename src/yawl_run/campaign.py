from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
from typing import Any

from .model import CampaignSpec
from .paths import logical_absolute, logical_cwd
from .worker import run_task


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _campaign_id(spec: CampaignSpec) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    digest = hashlib.sha256((spec.name + stamp + str(os.getpid())).encode()).hexdigest()[:8]
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in spec.name).strip("-")
    return f"{safe}-{stamp}-{digest}"


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def _task_dir(campaign_dir: Path, task_name: str) -> Path:
    return campaign_dir / "tasks" / task_name


def create_campaign(spec: CampaignSpec, root: str | Path, backend: str | None = None) -> Path:
    root = logical_absolute(root)
    campaign_dir = root / _campaign_id(spec)
    campaign_dir.mkdir(parents=True, exist_ok=False)
    selected_backend = backend or spec.backend
    launch_cwd = logical_cwd()

    manifest = {
        "schema": 2,
        "id": campaign_dir.name,
        "name": spec.name,
        "backend": selected_backend,
        "created_at": _utc_now(),
        "spec_source": str(spec.source),
        "tasks": [task.name for task in spec.tasks],
    }
    _write_json(campaign_dir / "campaign.json", manifest)
    _write_json(campaign_dir / "provenance.json", {
        "created_at": _utc_now(),
        "hostname": platform.node(),
        "platform": platform.platform(),
        "python": sys.version,
        "cwd": str(launch_cwd),
        "argv": sys.argv,
    })

    for task in spec.tasks:
        task_dir = _task_dir(campaign_dir, task.name)
        (task_dir / "attempts").mkdir(parents=True)
        record = asdict(task)
        record["parents"] = list(task.parents)
        record["cwd"] = str(logical_absolute(task.cwd, launch_cwd)) if task.cwd else str(launch_cwd)
        record["state"] = "pending"
        record["attempts"] = 0
        _write_json(task_dir / "task.json", record)

    return campaign_dir


def start_local(spec: CampaignSpec, root: str | Path) -> Path:
    campaign_dir = create_campaign(spec, root, backend="local")
    by_name = {task.name: task for task in spec.tasks}
    remaining = set(by_name)

    while remaining:
        progressed = False
        for name in list(remaining):
            task = by_name[name]
            parent_states = {
                parent: _read_json(_task_dir(campaign_dir, parent) / "task.json")["state"]
                for parent in task.parents
            }
            if any(state in {"failed", "blocked"} for state in parent_states.values()):
                record = _read_json(_task_dir(campaign_dir, name) / "task.json")
                record["state"] = "blocked"
                _write_json(_task_dir(campaign_dir, name) / "task.json", record)
                remaining.remove(name)
                progressed = True
                continue
            if not all(state == "completed" for state in parent_states.values()):
                continue
            for _ in range(task.retries + 1):
                if run_task(campaign_dir, name) == 0:
                    break
            remaining.remove(name)
            progressed = True
        if not progressed:
            raise RuntimeError("campaign dependency graph made no progress")
    return campaign_dir


def campaign_status(campaign_dir: str | Path) -> dict[str, Any]:
    campaign_dir = logical_absolute(campaign_dir)
    manifest = _read_json(campaign_dir / "campaign.json")
    tasks = []
    counts: dict[str, int] = {}
    for name in manifest["tasks"]:
        task = _read_json(_task_dir(campaign_dir, name) / "task.json")
        tasks.append({"name": name, "state": task["state"], "attempts": task["attempts"], "parents": task.get("parents", [])})
        counts[task["state"]] = counts.get(task["state"], 0) + 1
    return {"id": manifest["id"], "name": manifest["name"], "backend": manifest.get("backend", "local"), "counts": counts, "tasks": tasks}
