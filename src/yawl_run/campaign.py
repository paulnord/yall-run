from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any

from .model import CampaignSpec


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
    root = Path(root).resolve()
    campaign_dir = root / _campaign_id(spec)
    campaign_dir.mkdir(parents=True, exist_ok=False)
    selected_backend = backend or spec.backend

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

    provenance = {
        "created_at": _utc_now(),
        "hostname": platform.node(),
        "platform": platform.platform(),
        "python": sys.version,
        "cwd": os.getcwd(),
        "argv": sys.argv,
    }
    _write_json(campaign_dir / "provenance.json", provenance)

    for task in spec.tasks:
        tdir = _task_dir(campaign_dir, task.name)
        (tdir / "attempts").mkdir(parents=True)
        _write_json(tdir / "task.json", {
            **asdict(task),
            "parents": list(task.parents),
            "state": "pending",
            "attempts": 0,
        })

    return campaign_dir


def _next_attempt_dir(task_dir: Path) -> tuple[int, Path]:
    attempts = task_dir / "attempts"
    existing = [int(p.name) for p in attempts.iterdir() if p.is_dir() and p.name.isdigit()]
    number = max(existing, default=0) + 1
    adir = attempts / f"{number:03d}"
    adir.mkdir(parents=True, exist_ok=False)
    return number, adir


def run_task(campaign_dir: str | Path, task_name: str) -> int:
    campaign_dir = Path(campaign_dir).resolve()
    tdir = _task_dir(campaign_dir, task_name)
    task_path = tdir / "task.json"
    if not task_path.exists():
        raise ValueError(f"unknown task: {task_name}")

    task = _read_json(task_path)
    number, adir = _next_attempt_dir(tdir)
    stdout_path = adir / "stdout.log"
    stderr_path = adir / "stderr.log"

    started = _utc_now()
    _write_json(adir / "attempt.json", {
        "attempt": number,
        "state": "running",
        "started_at": started,
        "command": task["command"],
    })

    task["state"] = "running"
    task["attempts"] = number
    _write_json(task_path, task)

    cwd = Path(task["cwd"]).expanduser() if task.get("cwd") else None
    with stdout_path.open("w") as out, stderr_path.open("w") as err:
        proc = subprocess.run(
            task["command"],
            shell=True,
            cwd=str(cwd) if cwd else None,
            stdout=out,
            stderr=err,
            text=True,
        )

    state = "completed" if proc.returncode == 0 else "failed"
    finished = _utc_now()
    _write_json(adir / "attempt.json", {
        "attempt": number,
        "state": state,
        "started_at": started,
        "finished_at": finished,
        "returncode": proc.returncode,
        "command": task["command"],
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
    })

    task["state"] = state
    task["attempts"] = number
    task["last_returncode"] = proc.returncode
    _write_json(task_path, task)
    return proc.returncode


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
                rc = run_task(campaign_dir, name)
                if rc == 0:
                    break
            remaining.remove(name)
            progressed = True

        if not progressed:
            raise RuntimeError("campaign dependency graph made no progress")

    return campaign_dir


def campaign_status(campaign_dir: str | Path) -> dict[str, Any]:
    campaign_dir = Path(campaign_dir).resolve()
    manifest = _read_json(campaign_dir / "campaign.json")
    tasks = []
    counts: dict[str, int] = {}
    for name in manifest["tasks"]:
        task = _read_json(_task_dir(campaign_dir, name) / "task.json")
        tasks.append({
            "name": name,
            "state": task["state"],
            "attempts": task["attempts"],
            "parents": task.get("parents", []),
        })
        counts[task["state"]] = counts.get(task["state"], 0) + 1
    return {
        "id": manifest["id"],
        "name": manifest["name"],
        "backend": manifest.get("backend", "local"),
        "counts": counts,
        "tasks": tasks,
    }
