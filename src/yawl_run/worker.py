from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
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


def run_task(campaign_dir: str | Path, task_name: str) -> int:
    campaign_dir = Path(campaign_dir).expanduser()
    if not campaign_dir.is_absolute():
        campaign_dir = Path.cwd() / campaign_dir
    task_dir = campaign_dir / "tasks" / task_name
    task_path = task_dir / "task.json"
    if not task_path.exists():
        raise ValueError(f"unknown task: {task_name}")

    task = _read_json(task_path)
    attempts_dir = task_dir / "attempts"
    existing = [
        int(path.name)
        for path in attempts_dir.iterdir()
        if path.is_dir() and path.name.isdigit()
    ]
    number = max(existing, default=0) + 1
    attempt_dir = attempts_dir / f"{number:03d}"
    attempt_dir.mkdir(parents=True, exist_ok=False)
    stdout_path = attempt_dir / "stdout.log"
    stderr_path = attempt_dir / "stderr.log"

    started = _utc_now()
    _write_json(attempt_dir / "attempt.json", {
        "attempt": number,
        "state": "running",
        "started_at": started,
        "command": task["command"],
    })
    task["state"] = "running"
    task["attempts"] = number
    _write_json(task_path, task)

    cwd = Path(task["cwd"]) if task.get("cwd") else None
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
    _write_json(attempt_dir / "attempt.json", {
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
