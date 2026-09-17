from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import sys
import time
from typing import Any


YALL_AMENDMENT_SUPPORT = 1


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


# The worker is bundled as a standalone file. Do not depend on ClassAd bindings.
# Only accept literal identity fields; never evaluate ClassAd expressions here.
_CONDOR_JOB_AD_MAX_BYTES = 1024 * 1024


def _classad_scalar(value: str) -> Any:
    text = value.strip()
    if len(text) <= 19 and re.fullmatch(r"[0-9]+", text):
        value = int(text)
        return value if value <= 2**63 - 1 else None
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        try:
            decoded = json.loads(text)
        except (ValueError, RecursionError):
            return None
        return decoded if isinstance(decoded, str) else None
    # undefined, error, booleans, expressions, and unsupported escapes are not
    # usable job identifiers. Preserve the failure to interpret, not a guess.
    return None


def _condor_job_context() -> dict[str, Any] | None:
    """Read a bounded, allowlisted snapshot; telemetry must not fail a task."""
    value = os.environ.get("_CONDOR_JOB_AD")
    if not value:
        return None
    path = Path(value)
    result: dict[str, Any] = {"backend": "condor", "job_ad_path": str(path)}
    try:
        # O_NONBLOCK also protects against a bad environment pointing at a FIFO.
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ValueError("job ad is not a regular file")
            raw = source.read(_CONDOR_JOB_AD_MAX_BYTES + 1)
            if len(raw) > _CONDOR_JOB_AD_MAX_BYTES:
                raise ValueError("job ad exceeds the 1 MiB diagnostic limit")
    except (OSError, ValueError) as exc:
        result["read_error"] = str(exc)
        return result
    result["job_ad_sha256"] = hashlib.sha256(raw).hexdigest()
    wanted = {
        "clusterid": ("cluster_id", int),
        "procid": ("proc_id", int),
        "globaljobid": ("global_job_id", str),
        "dagmanjobid": ("dagman_job_id", int),
        "dagnodename": ("dag_node_name", str),
        "yalldagretry": ("dag_retry", int),
        "numjobstarts": ("num_job_starts", int),
        "remotehost": ("remote_host", str),
        "lastremotehost": ("last_remote_host", str),
        "jobstartdate": ("job_start_date", int),
        "jobcurrentstartdate": ("job_current_start_date", int),
    }
    seen: set[str] = set()
    errors: dict[str, str] = {}
    for line in raw.decode(errors="replace").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        name, raw_value = line.split("=", 1)
        field = wanted.get(name.strip().lower())
        if field is None:
            continue
        target, expected = field
        if target in seen:
            result.pop(target, None)
            errors[target] = "duplicate attribute"
            continue
        seen.add(target)
        parsed = _classad_scalar(raw_value)
        if type(parsed) is not expected or (expected is str and not parsed):
            errors[target] = "not a supported literal of the expected type"
        else:
            result[target] = parsed
    if errors:
        result["parse_errors"] = errors
    if "cluster_id" in result and "proc_id" in result:
        result["job_id"] = f"{result['cluster_id']}.{result['proc_id']}"
    return result


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


def _existing_paths(records: list[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    for record in records:
        path = Path(str(record["path"]))
        if record.get("exists", False) or path.is_symlink():
            result.append(str(path))
    return result


def _campaign_overwrite(campaign_dir: Path) -> bool:
    start_path = campaign_dir / "start.json"
    if start_path.is_file():
        try:
            return bool(_read_json(start_path).get("overwrite", False))
        except (OSError, json.JSONDecodeError):
            return False

    # Condor can start a node immediately after DAG submission, before the
    # submit command returns and start.json is committed. A prepared start
    # record carries the requested policy across that short race window.
    pending_path = campaign_dir / "state" / "start-pending.json"
    if pending_path.is_file():
        try:
            return bool(_read_json(pending_path).get("overwrite", False))
        except (OSError, json.JSONDecodeError):
            return False
    return False


def _legacy_task_path(campaign_dir: Path, task_name: str) -> Path:
    return campaign_dir / "tasks" / f"{task_name}.json"


def _state_path(campaign_dir: Path, task_name: str) -> Path:
    return campaign_dir / "state" / f"{task_name}.json"


def _amendment_paths(campaign_dir: Path) -> list[Path]:
    root = campaign_dir / "amendments"
    if not root.is_dir():
        return []
    result = []
    for directory in sorted(root.iterdir()):
        if directory.is_dir() and directory.name.isdigit():
            path = directory / "amendment.json"
            if path.is_file():
                result.append(path)
    return result


def _effective_task_definition(
    campaign_dir: Path,
    manifest: dict[str, Any],
    task_name: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    tasks = manifest.get("tasks", {})
    if isinstance(tasks, dict):
        original = tasks.get(task_name)
        if not isinstance(original, dict):
            raise ValueError(f"unknown task: {task_name}")
        task = dict(original)
    else:
        path = _legacy_task_path(campaign_dir, task_name)
        if not path.is_file():
            raise ValueError(f"unknown task: {task_name}")
        task = _read_json(path)

    applied: list[dict[str, Any]] = []
    for path in _amendment_paths(campaign_dir):
        record = _read_json(path)
        number = int(record.get("number", int(path.parent.name)))
        for change in record.get("changes", []):
            if change.get("task") != task_name:
                continue
            if change.get("field") != "command":
                raise ValueError(
                    f"amendment {number:04d} contains unsupported field "
                    f"{change.get('field')!r} for task {task_name!r}"
                )
            if task.get("command") != change.get("before"):
                raise ValueError(
                    f"amendment chain mismatch for task {task_name!r} at {path}"
                )
            task["command"] = change.get("after")
            applied.append({
                "number": number,
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "revised_yallfile_sha256": (record.get("revised_yallfile") or {}).get("sha256"),
            })
    return task, applied


def _task_definition(
    campaign_dir: Path,
    manifest: dict[str, Any],
    task_name: str,
) -> dict[str, Any]:
    task, _ = _effective_task_definition(campaign_dir, manifest, task_name)
    return task


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


def _current_attempt_count(
    campaign_dir: Path,
    manifest: dict[str, Any],
    task_name: str,
) -> int:
    state_path = _state_path(campaign_dir, task_name)
    if state_path.is_file():
        state = _read_json(state_path)
        return int(state.get("attempts", 0))

    legacy_path = _legacy_task_path(campaign_dir, task_name)
    if legacy_path.is_file():
        task = _read_json(legacy_path)
        return int(task.get("attempts", 0))

    _task_definition(campaign_dir, manifest, task_name)
    return 0


def _next_attempt_number(
    campaign_dir: Path,
    manifest: dict[str, Any],
    task_name: str,
) -> int:
    number = _current_attempt_count(campaign_dir, manifest, task_name) + 1
    # A worker can die after creating an attempt directory but before updating
    # state. Avoid a full campaign-directory scan while still stepping around
    # such an orphaned attempt directory.
    while (campaign_dir / f"{task_name}_attempt_{number:03d}").exists():
        number += 1
    return number


def _payload_command(command: str | list[str], wrapper: dict[str, Any] | None, startup_marker: Path | None = None) -> list[str]:
    payload = ["/bin/sh", "-c", command] if isinstance(command, str) else list(command)
    if startup_marker is not None and wrapper is not None:
        entry = 'marker=$1; shift; : > "$marker" || exit 125; exec "$@"'
        payload = ["/bin/sh", "-c", entry, "yall-payload-start", str(startup_marker), *payload]
    if wrapper is not None:
        return [str(wrapper["path"]), *wrapper.get("args", []), *payload]
    return payload



def _waitstatus_to_exitcode(status: int) -> int:
    """Python 3.8-compatible equivalent of os.waitstatus_to_exitcode."""
    converter = getattr(os, "waitstatus_to_exitcode", None)
    if converter is not None:
        return int(converter(status))
    if os.WIFEXITED(status):
        return int(os.WEXITSTATUS(status))
    if os.WIFSIGNALED(status):
        return -int(os.WTERMSIG(status))
    raise ValueError(f"invalid wait status: {status}")

def _run_command(
    command: list[str],
    *,
    cwd: Path | None,
    stdout: Any,
    stderr: Any,
    env: dict[str, str],
) -> tuple[int, dict[str, float | None], int]:
    started = time.monotonic()
    proc = subprocess.Popen(
        command,
        shell=False,
        cwd=str(cwd) if cwd else None,
        stdout=stdout,
        stderr=stderr,
        text=True,
        env=env,
    )
    launch_pid = int(proc.pid)

    user_seconds: float | None = None
    sys_seconds: float | None = None
    if hasattr(os, "wait4"):
        while True:
            try:
                _, status, usage = os.wait4(proc.pid, 0)
                break
            except InterruptedError:
                continue
        proc.returncode = _waitstatus_to_exitcode(status)
        user_seconds = float(usage.ru_utime)
        sys_seconds = float(usage.ru_stime)
    else:
        proc.wait()

    timing = {
        "real_seconds": time.monotonic() - started,
        "user_seconds": user_seconds,
        "sys_seconds": sys_seconds,
    }
    return int(proc.returncode), timing, launch_pid


def run_task(campaign_dir: str | Path, task_name: str) -> int:
    campaign_dir = Path(campaign_dir).expanduser()
    if not campaign_dir.is_absolute():
        campaign_dir = Path.cwd() / campaign_dir
    campaign_dir = campaign_dir.absolute()

    manifest_path = campaign_dir / "campaign.json"
    if not manifest_path.exists():
        raise ValueError(f"not a yall campaign: {campaign_dir}")
    manifest = _read_json(manifest_path)
    task, task_amendments = _effective_task_definition(
        campaign_dir, manifest, task_name
    )
    startup_marker = _startup_marker(campaign_dir, task_name)

    number = _next_attempt_number(campaign_dir, manifest, task_name)
    attempt_dir = campaign_dir / f"{task_name}_attempt_{number:03d}"
    attempt_dir.mkdir(parents=False, exist_ok=False)
    stdout_path = attempt_dir / "stdout.log"
    stderr_path = attempt_dir / "stderr.log"
    provenance_path = attempt_dir / "provenance.json"

    command = task["command"]
    wrapper = manifest.get("execution", {}).get("wrapper")
    launch_command = _payload_command(command, wrapper, startup_marker)
    inputs = [_inspect_file(ref) for ref in task.get("inputs", [])]
    started = _utc_now()
    worker_pid = os.getpid()
    task_overwrite = bool(task.get("overwrite", False))
    campaign_overwrite = _campaign_overwrite(campaign_dir)
    scheduler_context = (
        _condor_job_context() if manifest.get("backend") == "condor" else None
    )

    launch_provenance = {
        "schema": 1,
        "campaign": {
            "id": manifest.get("id"),
            "name": manifest.get("name"),
            "backend": manifest.get("backend"),
            "yall_version": manifest.get("yall_version"),
            "directory": str(campaign_dir),
        },
        "task": {
            "name": task_name,
            "attempt": number,
            "amendments": task_amendments,
            "parents": task.get("parents", []),
            "retries": task.get("retries", 0),
            "startup_retries": task.get("startup_retries", 0),
            "command": command,
            "cwd": task.get("cwd"),
            "resources": task.get("resources", {}),
            "inputs": inputs,
            "outputs": task.get("outputs", []),
            "overwrite": task_overwrite,
        },
        "execution": {
            "context": "host",
            "wrapper": wrapper,
            "launch_command": launch_command,
            "started_at": started,
            "hostname": platform.node(),
            "platform": platform.platform(),
            "python": sys.version,
            "python_executable": sys.executable,
            "pid": worker_pid,
            "worker_pid": worker_pid,
            "campaign_overwrite": campaign_overwrite,
        },
    }
    if scheduler_context is not None:
        launch_provenance["scheduler"] = scheduler_context
    _write_json(provenance_path, launch_provenance)

    missing_inputs = _missing_paths(inputs)
    if missing_inputs:
        stdout_path.touch()
        stderr_path.write_text(
            "yall-worker: declared input missing: " + ", ".join(missing_inputs) + "\n"
        )
        _mark_nonstartup_failure(startup_marker, stderr_path)
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
            "worker_pid": worker_pid,
            "launch_pid": None,
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

    outputs_before = [_inspect_file(ref) for ref in task.get("outputs", [])]
    existing_outputs = _existing_paths(outputs_before)
    if existing_outputs and not (task_overwrite or campaign_overwrite):
        stdout_path.touch()
        stderr_path.write_text(
            "yall-worker: declared output already exists: "
            + ", ".join(existing_outputs)
            + "\n"
        )
        _mark_nonstartup_failure(startup_marker, stderr_path)
        finished = _utc_now()
        failure = {"kind": "outputs_exist", "paths": existing_outputs}
        _write_json(attempt_dir / "attempt.json", {
            "task": task_name,
            "attempt": number,
            "state": "failed",
            "started_at": started,
            "finished_at": finished,
            "returncode": 2,
            "command_returncode": None,
            "worker_pid": worker_pid,
            "launch_pid": None,
            "command": command,
            "cwd": task.get("cwd"),
            "inputs": inputs,
            "outputs": outputs_before,
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
        "worker_pid": worker_pid,
        "command": command,
        "cwd": task.get("cwd"),
        "inputs": inputs,
        "outputs_before": outputs_before,
        "provenance": str(provenance_path),
    })
    _write_task_state(campaign_dir, manifest, task_name, {
        "state": "running",
        "attempts": number,
    })

    env = os.environ.copy()
    env.update({
        "YALL_CAMPAIGN_ID": str(manifest.get("id", "")),
        "YALL_CAMPAIGN_NAME": str(manifest.get("name", "")),
        "YALL_CAMPAIGN_DIR": str(campaign_dir),
        "YALL_BACKEND": str(manifest.get("backend", "")),
        "YALL_TASK": task_name,
        "YALL_ATTEMPT": str(number),
        "YALL_PROVENANCE": str(provenance_path),
        "YALL_TASK_CWD": str(task.get("cwd") or Path.cwd()),
    })

    cwd = Path(task["cwd"]) if task.get("cwd") else None
    launch_error: OSError | None = None
    with stdout_path.open("w") as out, stderr_path.open("w") as err:
        if startup_marker is not None and wrapper is None:
            try:
                startup_marker.touch()
            except OSError as exc:
                err.write(f"yall-worker: could not write startup marker {startup_marker}: {exc}\n")
        try:
            command_returncode, timing, launch_pid = _run_command(
                launch_command,
                cwd=cwd,
                stdout=out,
                stderr=err,
                env=env,
            )
        except OSError as exc:
            # exec/cwd failures must produce a terminal attempt, not stale
            # "running" state with the only error in a scheduler-level log.
            launch_error = exc
            command_returncode = None
            launch_pid = None
            timing = {"real_seconds": None, "user_seconds": None, "sys_seconds": None}
            err.write(f"yall-worker: launch failed: {exc}\n")

    finished = _utc_now()
    outputs = [_inspect_file(ref) for ref in task.get("outputs", [])]
    missing_outputs = _missing_paths(outputs)
    returncode = 2 if launch_error is not None else command_returncode
    failure: dict[str, Any] | None = None
    if launch_error is not None:
        failure = {"kind": "launch_failed", "errno": launch_error.errno, "message": str(launch_error)}
    elif command_returncode == 0 and missing_outputs:
        returncode = 1
        failure = {"kind": "missing_outputs", "paths": missing_outputs}
        with stderr_path.open("a") as err:
            err.write(
                "yall-worker: declared output missing: "
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
        "worker_pid": worker_pid,
        "launch_pid": launch_pid,
        "launch_command": launch_command,
        "command": command,
        "cwd": task.get("cwd"),
        "inputs": inputs,
        "outputs_before": outputs_before,
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


def _startup_marker(campaign_dir: Path, task_name: str) -> Path | None:
    marker_dir = campaign_dir / "condor" / "startup"
    if not marker_dir.is_dir():
        return None
    marker_id = hashlib.sha256(task_name.encode("utf-8")).hexdigest()
    return marker_dir / f"{marker_id}.started"


def _mark_nonstartup_failure(marker: Path | None, stderr_path: Path) -> None:
    if marker is None:
        return
    try:
        marker.touch()
    except OSError as exc:
        with stderr_path.open("a") as err:
            err.write(f"yall-worker: could not write startup marker {marker}: {exc}\n")


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if len(values) != 2:
        print("usage: yall_worker.py CAMPAIGN_DIR TASK", file=sys.stderr)
        return 2
    try:
        return run_task(values[0], values[1])
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"yall-worker: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
