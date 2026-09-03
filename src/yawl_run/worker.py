from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, indent=2, sort_keys=True) + "\n"
    temp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temp.write_text(text)
    temp.replace(path)


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


def _missing_paths(records: list[dict[str, Any]]) -> list[str]:
    return [str(record["path"]) for record in records if not record.get("exists", False)]


def _legacy_task_path(campaign_dir: Path, task_name: str) -> Path:
    return campaign_dir / "tasks" / f"{task_name}.json"


def _state_path(campaign_dir: Path, task_name: str) -> Path:
    return campaign_dir / "state" / f"{task_name}.json"


def _task_definition(
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
    path = _legacy_task_path(campaign_dir, task_name)
    if not path.is_file():
        raise ValueError(f"unknown task: {task_name}")
    return _read_json(path)


def _write_task_state(
    campaign_dir: Path,
    manifest: dict[str, Any],
    task_name: str,
    state: dict[str, Any],
) -> None:
    if isinstance(manifest.get("tasks"), dict):
        _write_json(_state_path(campaign_dir, task_name), state)
        return
    legacy_path = _legacy_task_path(campaign_dir, task_name)
    task = _read_json(legacy_path)
    task.update(state)
    _write_json(legacy_path, task)


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


def _run_command(
    command: str | list[str],
    *,
    cwd: Path | None,
    stdout: Any,
    stderr: Any,
    env: dict[str, str],
) -> tuple[int, dict[str, float | None]]:
    started = time.monotonic()
    proc = subprocess.Popen(
        command,
        shell=isinstance(command, str),
        cwd=str(cwd) if cwd else None,
        stdout=stdout,
        stderr=stderr,
        text=True,
        env=env,
    )

    user_seconds: float | None = None
    sys_seconds: float | None = None
    if hasattr(os, "wait4"):
        while True:
            try:
                _, status, usage = os.wait4(proc.pid, 0)
                break
            except InterruptedError:
                continue
        proc.returncode = os.waitstatus_to_exitcode(status)
        user_seconds = float(usage.ru_utime)
        sys_seconds = float(usage.ru_stime)
    else:
        proc.wait()

    timing = {
        "real_seconds": time.monotonic() - started,
        "user_seconds": user_seconds,
        "sys_seconds": sys_seconds,
    }
    return int(proc.returncode), timing


def run_task(campaign_dir: str | Path, task_name: str) -> int:
    campaign_dir = Path(campaign_dir).expanduser()
    if not campaign_dir.is_absolute():
        campaign_dir = Path.cwd() / campaign_dir
    campaign_dir = campaign_dir.absolute()

    manifest_path = campaign_dir / "campaign.json"
    if not manifest_path.exists():
        raise ValueError(f"not a yawl campaign: {campaign_dir}")
    manifest = _read_json(manifest_path)
    task = _task_definition(campaign_dir, manifest, task_name)

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

    missing_inputs = _missing_paths(inputs)
    if missing_inputs:
        stdout_path.touch()
        stderr_path.write_text(
            "yawl-worker: declared input missing: " + ", ".join(missing_inputs) + "\n"
        )
        finished = _utc_now()
        outputs = [_inspect_file(ref) for ref in task.get("outputs", [])]
        failure = {"kind": "missing_inputs", "paths": missing_inputs}
        _write_json(attempt_dir / "attempt.json", {
            "task": task_name,
            "attempt": number,
            "state": "failed",
            "started_at": started,
            "finished_at": finished,
            "returncode": 2,
            "command_returncode": None,
            "command": command,
            "cwd": task.get("cwd"),
            "inputs": inputs,
            "outputs": outputs,
            "failure": failure,
            "provenance": str(provenance_path),
            "stdout": str(stdout_path),
            "stderr": str(stderr_path),
        })
        _write_task_state(campaign_dir, manifest, task_name, {
            "state": "failed",
            "attempts": number,
            "last_returncode": 2,
        })
        return 2

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
    _write_task_state(campaign_dir, manifest, task_name, {
        "state": "running",
        "attempts": number,
    })

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
        command_returncode, timing = _run_command(
            command,
            cwd=cwd,
            stdout=out,
            stderr=err,
            env=env,
        )

    finished = _utc_now()
    outputs = [_inspect_file(ref) for ref in task.get("outputs", [])]
    missing_outputs = _missing_paths(outputs)
    returncode = command_returncode
    failure: dict[str, Any] | None = None
    if command_returncode == 0 and missing_outputs:
        returncode = 1
        failure = {"kind": "missing_outputs", "paths": missing_outputs}
        with stderr_path.open("a") as err:
            err.write(
                "yawl-worker: declared output missing: "
                + ", ".join(missing_outputs)
                + "\n"
            )
    elif command_returncode != 0:
        failure = {"kind": "command_failed", "returncode": command_returncode}

    state = "completed" if returncode == 0 else "failed"
    attempt_record: dict[str, Any] = {
        "task": task_name,
        "attempt": number,
        "state": state,
        "started_at": started,
        "finished_at": finished,
        "returncode": returncode,
        "command_returncode": command_returncode,
        "command": command,
        "cwd": task.get("cwd"),
        "inputs": inputs,
        "outputs": outputs,
        "timing": timing,
        "provenance": str(provenance_path),
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
    }
    if failure is not None:
        attempt_record["failure"] = failure
    _write_json(attempt_dir / "attempt.json", attempt_record)
    _write_task_state(campaign_dir, manifest, task_name, {
        "state": state,
        "attempts": number,
        "last_returncode": returncode,
    })
    return returncode


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
