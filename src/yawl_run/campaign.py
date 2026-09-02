from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import time
from typing import Any

from . import __version__
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
    text = json.dumps(obj, indent=2, sort_keys=True) + "\n"
    temp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temp.write_text(text)
    temp.replace(path)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def _task_path(campaign_dir: Path, task_name: str) -> Path:
    return campaign_dir / "tasks" / f"{task_name}.json"


def campaign_manifest(campaign_dir: str | Path) -> tuple[Path, dict[str, Any]]:
    campaign_dir = logical_absolute(campaign_dir)
    manifest_path = campaign_dir / "campaign.json"
    if not manifest_path.is_file():
        raise ValueError(f"not a yawl campaign: {campaign_dir}")
    return campaign_dir, _read_json(manifest_path)


def begin_campaign(campaign_dir: str | Path) -> Path:
    campaign_dir, manifest = campaign_manifest(campaign_dir)
    start_path = campaign_dir / "start.json"
    if start_path.exists():
        previous = _read_json(start_path)
        when = previous.get("started_at", "previously")
        raise ValueError(f"campaign has already been started ({when}): {campaign_dir}")

    previously_attempted = []
    for name in manifest.get("tasks", []):
        task = _read_json(_task_path(campaign_dir, name))
        if task.get("state", "pending") != "pending" or int(task.get("attempts", 0)) > 0:
            previously_attempted.append(name)
    if previously_attempted:
        names = ", ".join(previously_attempted[:3])
        if len(previously_attempted) > 3:
            names += ", ..."
        raise ValueError(
            f"campaign already contains task attempts ({names}); create a new campaign"
        )

    _write_json(start_path, {
        "started_at": _utc_now(),
        "backend": manifest.get("backend", "local"),
        "execution": manifest.get("execution", {}),
    })
    return campaign_dir


def create_campaign(
    spec: CampaignSpec,
    root: str | Path,
    backend: str | None = None,
    local_jobs: int | None = None,
) -> Path:
    selected_backend = backend or spec.backend
    if selected_backend == "local":
        jobs = 1 if local_jobs is None else local_jobs
        if jobs < 1:
            raise ValueError("-j/--jobs must be positive")
        execution = {"local": {"jobs": jobs}}
    elif selected_backend in {"condor", "slurm", "pbs"}:
        if local_jobs is not None:
            raise ValueError("-j/--jobs is only valid for the local backend")
        execution = {selected_backend: {}}
    else:
        raise ValueError(f"unknown campaign backend: {selected_backend}")

    root = logical_absolute(root)
    campaign_dir = root / _campaign_id(spec)
    campaign_dir.mkdir(parents=True, exist_ok=False)
    (campaign_dir / "tasks").mkdir()
    launch_cwd = logical_cwd()

    manifest = {
        "schema": 6,
        "id": campaign_dir.name,
        "name": spec.name,
        "backend": selected_backend,
        "execution": execution,
        "created_at": _utc_now(),
        "yawl_version": __version__,
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
        task_cwd = logical_absolute(task.cwd, launch_cwd) if task.cwd else launch_cwd
        record = asdict(task)
        record["parents"] = list(task.parents)
        if not isinstance(task.command, str):
            record["command"] = list(task.command)
        record["cwd"] = str(task_cwd)
        record["inputs"] = [
            {"role": item.role, "path": str(logical_absolute(item.path, task_cwd))}
            for item in task.inputs
        ]
        record["outputs"] = [
            {"role": item.role, "path": str(logical_absolute(item.path, task_cwd))}
            for item in task.outputs
        ]
        record["state"] = "pending"
        record["attempts"] = 0
        _write_json(_task_path(campaign_dir, task.name), record)

    return campaign_dir


def _run_with_retries(campaign_dir: Path, task_name: str) -> int:
    task = _read_json(_task_path(campaign_dir, task_name))
    result = 1
    for _ in range(int(task.get("retries", 0)) + 1):
        result = run_task(campaign_dir, task_name)
        if result == 0:
            break
    return result


def _available_cpus() -> int | None:
    try:
        if hasattr(os, "sched_getaffinity"):
            return len(os.sched_getaffinity(0))
    except OSError:
        pass
    return os.cpu_count()


def _load_one() -> float | None:
    try:
        return os.getloadavg()[0]
    except (AttributeError, OSError):
        return None


def _attempt_timing(campaign_dir: Path, task_name: str, attempt: int) -> dict[str, float]:
    if attempt < 1:
        return {}
    path = campaign_dir / f"{task_name}_attempt_{attempt:03d}" / "attempt.json"
    try:
        record = _read_json(path)
    except (OSError, json.JSONDecodeError):
        return {}
    timing = record.get("timing")
    if not isinstance(timing, dict):
        return {}
    result: dict[str, float] = {}
    for key in ("real_seconds", "user_seconds", "sys_seconds"):
        value = timing.get(key)
        if isinstance(value, (int, float)):
            result[key] = float(value)
    return result


def _timing_suffix(timing: dict[str, float]) -> str:
    if not timing:
        return ""
    parts = []
    for label, key in (
        ("real", "real_seconds"),
        ("user", "user_seconds"),
        ("sys", "sys_seconds"),
    ):
        if key in timing:
            parts.append(f"{label}={timing[key]:.2f}s")
    return (" " + " ".join(parts)) if parts else ""


def start_local(campaign_dir: str | Path) -> Path:
    campaign_dir, manifest = campaign_manifest(campaign_dir)
    if manifest.get("backend", "local") != "local":
        raise ValueError(f"campaign backend is not local: {campaign_dir}")

    jobs = int(manifest.get("execution", {}).get("local", {}).get("jobs", 1))
    if jobs < 1:
        raise ValueError("invalid local concurrency stored in campaign")
    begin_campaign(campaign_dir)

    cpus = _available_cpus()
    load1 = _load_one()
    geek = [
        f"host={platform.node()}",
        f"pid={os.getpid()}",
        f"jobs={jobs}",
        f"cpus_available={cpus if cpus is not None else 'unknown'}",
    ]
    if load1 is not None:
        geek.append(f"load1={load1:.2f}")
    print("[local] " + " ".join(geek), flush=True)
    if cpus is not None and jobs > cpus:
        print(
            f"[local] warning jobs={jobs} exceeds cpus_available={cpus}; "
            "the operating system will time-slice runnable tasks",
            flush=True,
        )

    task_names = list(manifest["tasks"])
    remaining = set(task_names)
    running: dict[Future[int], tuple[str, float]] = {}

    with ThreadPoolExecutor(max_workers=jobs, thread_name_prefix="yawl") as pool:
        while remaining or running:
            progressed = False

            for name in task_names:
                if name not in remaining or len(running) >= jobs:
                    continue
                task = _read_json(_task_path(campaign_dir, name))
                parent_states = {
                    parent: _read_json(_task_path(campaign_dir, parent))["state"]
                    for parent in task.get("parents", [])
                }
                if any(state in {"failed", "blocked"} for state in parent_states.values()):
                    task["state"] = "blocked"
                    _write_json(_task_path(campaign_dir, name), task)
                    remaining.remove(name)
                    print(f"[block] {name} parent failed", flush=True)
                    progressed = True
                    continue
                if not all(state == "completed" for state in parent_states.values()):
                    continue

                print(f"[start] {name}", flush=True)
                future = pool.submit(_run_with_retries, campaign_dir, name)
                running[future] = (name, time.monotonic())
                remaining.remove(name)
                progressed = True

            if running:
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in done:
                    name, started = running.pop(future)
                    result = future.result()
                    elapsed = time.monotonic() - started
                    task = _read_json(_task_path(campaign_dir, name))
                    attempt = int(task.get("attempts", 0))
                    timing = _attempt_timing(campaign_dir, name, attempt)
                    timing_text = _timing_suffix(timing)
                    if result == 0:
                        print(
                            f"[done ] {name} attempt={attempt} elapsed={elapsed:.2f}s"
                            f"{timing_text}",
                            flush=True,
                        )
                    else:
                        stderr = f"{name}_attempt_{attempt:03d}/stderr.log"
                        print(
                            f"[FAIL ] {name} attempt={attempt} exit={result} "
                            f"elapsed={elapsed:.2f}s{timing_text} stderr={stderr}",
                            flush=True,
                        )
                progressed = True

            if not progressed and remaining:
                raise RuntimeError("campaign dependency graph made no progress")

    status = campaign_status(campaign_dir)
    counts = status["counts"]
    completed = counts.get("completed", 0)
    failed = counts.get("failed", 0)
    blocked = counts.get("blocked", 0)
    print(
        f"[local] finished completed={completed} failed={failed} blocked={blocked}",
        flush=True,
    )
    if failed or blocked:
        raise RuntimeError(
            f"local campaign failed: completed={completed} failed={failed} blocked={blocked}"
        )
    return campaign_dir


def campaign_status(campaign_dir: str | Path) -> dict[str, Any]:
    campaign_dir, manifest = campaign_manifest(campaign_dir)
    tasks = []
    counts: dict[str, int] = {}
    for name in manifest["tasks"]:
        task = _read_json(_task_path(campaign_dir, name))
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
