from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def _inspect_file(ref: dict[str, Any]) -> dict[str, Any]:
    path = Path(ref["path"])
    result: dict[str, Any] = {"role": ref.get("role"), "path": str(path)}
    try:
        stat = path.stat()
    except OSError as exc:
        result.update({"exists": False, "stat_error": str(exc)})
        return result
    result.update({
        "exists": True,
        "kind": "directory" if path.is_dir() else "file",
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    })
    return result


def _task_path(campaign_dir: Path, task_name: str) -> Path:
    return campaign_dir / "tasks" / f"{task_name}.json"


def _next_attempt_number(campaign_dir: Path, task_name: str) -> int:
    prefix = f"{task_name}_attempt_"
    numbers: list[int] = []
    for path in campaign_dir.iterdir():
        if not path.is_dir() or not path.name.startswith(prefix):
            continue
        suffix = path.name[len(prefix):]
        if suffix.isdigit():
            numbers.append(int(suffix))
    return max(numbers, default=0) + 1


def run_task(campaign_dir: str | Path, task_name: str) -> int:
    campaign_dir = Path(campaign_dir).expanduser()
    if not campaign_dir.is_absolute():
        campaign_dir = Path.cwd() / campaign_dir
    campaign_dir = campaign_dir.absolute()

    manifest_path = campaign_dir / "campaign.json"
    if not manifest_path.exists():
        raise ValueError(f"not a yawl campaign: {campaign_dir}")
    manifest = _read_json(manifest_path)

    task_path = _task_path(campaign_dir, task_name)
    if not task_path.exists():
        raise ValueError(f"unknown task: {task_name}")

    task = _read_json(task_path)
    number = _next_attempt_number(campaign_dir, task_name)
    attempt_dir = campaign_dir / f"{task_name}_attempt_{number:03d}"
    attempt_dir.mkdir(parents=False, exist_ok=False)
    stdout_path = attempt_dir / "stdout.log"
    stderr_path = attempt_dir / "stderr.log"
    provenance_path = attempt_dir / "provenance.json"

    command = task["command"]
    inputs = [_inspect_file(ref) for ref in task.get("inputs", [])]
    started = _utc_now()

    launch_provenance = {
        "schema": 1,
        "campaign": {
            "id": manifest.get("id"),
            "name": manifest.get("name"),
            "backend": manifest.get("backend"),
            "yawl_version": manifest.get("yawl_version"),
            "directory": str(campaign_dir),
        },
        "task": {
            "name": task_name,
            "attempt": number,
            "parents": task.get("parents", []),
            "retries": task.get("retries", 0),
            "command": command,
            "cwd": task.get("cwd"),
            "resources": task.get("resources", {}),
            "inputs": inputs,
            "outputs": task.get("outputs", []),
        },
        "execution": {
            "started_at": started,
            "hostname": platform.node(),
            "platform": platform.platform(),
            "python": sys.version,
            "pid": os.getpid(),
        },
    }
    _write_json(provenance_path, launch_provenance)

    _write_json(attempt_dir / "attempt.json", {
        "task": task_name,
        "attempt": number,
        "state": "running",
        "started_at": started,
        "command": command,
        "cwd": task.get("cwd"),
        "inputs": inputs,
        "provenance": str(provenance_path),
    })
    task["state"] = "running"
    task["attempts"] = number
    _write_json(task_path, task)

    env = os.environ.copy()
    env.update({
        "YAWL_CAMPAIGN_ID": str(manifest.get("id", "")),
        "YAWL_CAMPAIGN_NAME": str(manifest.get("name", "")),
        "YAWL_CAMPAIGN_DIR": str(campaign_dir),
        "YAWL_BACKEND": str(manifest.get("backend", "")),
        "YAWL_TASK": task_name,
        "YAWL_ATTEMPT": str(number),
        "YAWL_PROVENANCE": str(provenance_path),
    })

    cwd = Path(task["cwd"]) if task.get("cwd") else None
    with stdout_path.open("w") as out, stderr_path.open("w") as err:
        proc = subprocess.run(
            command,
            shell=isinstance(command, str),
            cwd=str(cwd) if cwd else None,
            stdout=out,
            stderr=err,
            text=True,
            env=env,
        )

    state = "completed" if proc.returncode == 0 else "failed"
    finished = _utc_now()
    outputs = [_inspect_file(ref) for ref in task.get("outputs", [])]
    _write_json(attempt_dir / "attempt.json", {
        "task": task_name,
        "attempt": number,
        "state": state,
        "started_at": started,
        "finished_at": finished,
        "returncode": proc.returncode,
        "command": command,
        "cwd": task.get("cwd"),
        "inputs": inputs,
        "outputs": outputs,
        "provenance": str(provenance_path),
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
    })
    task["state"] = state
    task["attempts"] = number
    task["last_returncode"] = proc.returncode
    _write_json(task_path, task)
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if len(values) != 2:
        print("usage: yawl_worker.py CAMPAIGN_DIR TASK", file=sys.stderr)
        return 2
    try:
        return run_task(values[0], values[1])
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"yawl-worker: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
