from __future__ import annotations

import json
from pathlib import Path
import shlex
from typing import Any

from .execution_trace import render_condor_execution_trace


PROBLEM_STATES = {"failed", "blocked", "interrupted", "unknown", "held", "suspended"}
SCHEDULER_PROBLEM_STATES = {"held", "suspended", "unknown", "removing"}


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _display_command(command: object) -> str:
    if isinstance(command, str):
        return command
    if isinstance(command, list):
        return shlex.join(str(item) for item in command)
    return str(command)


def _task_definition(campaign_dir: Path, manifest: dict[str, Any], name: str) -> dict[str, Any]:
    tasks = manifest.get("tasks", {})
    if isinstance(tasks, dict):
        task = tasks.get(name)
        return task if isinstance(task, dict) else {}
    path = campaign_dir / "tasks" / f"{name}.json"
    return _read_json(path) if path.is_file() else {}


def _attempt_dir(campaign_dir: Path, task: dict[str, Any], attempt: int | None = None) -> Path | None:
    number = int(task.get("attempts", 0) if attempt is None else attempt)
    if number < 1:
        return None
    path = campaign_dir / f"{task['name']}_attempt_{number:03d}"
    return path if path.is_dir() else None


def _attempt_record(campaign_dir: Path, task: dict[str, Any], attempt: int | None = None) -> tuple[Path | None, dict[str, Any] | None]:
    directory = _attempt_dir(campaign_dir, task, attempt)
    if directory is None:
        return None, None
    path = directory / "attempt.json"
    if not path.is_file():
        return directory, None
    try:
        return directory, _read_json(path)
    except (OSError, json.JSONDecodeError, ValueError):
        return directory, None


def _clip(text: str, limit: int = 2000) -> str:
    if len(text) <= limit:
        return text
    return "..." + text[-limit:]


def _tail(path: Path, limit: int) -> list[str]:
    """Read a bounded tail without loading a potentially huge batch log."""
    if limit < 1 or not path.is_file():
        return []
    byte_budget = max(64 * 1024, limit * 4096)
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            position = handle.tell()
            chunks = []
            read_bytes = 0
            newlines = 0
            while position > 0 and read_bytes < byte_budget and newlines <= limit:
                size = min(8192, position, byte_budget - read_bytes)
                position -= size
                handle.seek(position)
                chunk = handle.read(size)
                chunks.append(chunk)
                read_bytes += len(chunk)
                newlines += chunk.count(b"\n")
    except OSError:
        return []
    text = b"".join(reversed(chunks)).decode(errors="replace")
    return [_clip(line) for line in text.splitlines()[-limit:]]


def _last_nonempty(path: Path) -> str | None:
    for line in reversed(_tail(path, 50)):
        if line.strip():
            return line.strip()
    return None


def _failure_text(attempt: dict[str, Any] | None) -> str | None:
    if not attempt:
        return None
    failure = attempt.get("failure") or {}
    kind = failure.get("kind")
    if not kind:
        return None
    extras = []
    if kind != "command_failed" and failure.get("returncode") is not None:
        extras.append(f"returncode={failure['returncode']}")
    if failure.get("errno") is not None:
        extras.append(f"errno={failure['errno']}")
    if failure.get("paths"):
        extras.append("paths=" + ",".join(str(path) for path in failure["paths"]))
    if failure.get("message"):
        extras.append(str(failure["message"]))
    return str(kind) + ((" " + " ".join(extras)) if extras else "")


def _resource_text(resources: dict[str, Any]) -> str:
    parts = []
    for key in ("cpus", "memory", "disk", "walltime_seconds"):
        value = resources.get(key)
        if value is not None:
            parts.append(f"{key}={value}")
    return " ".join(parts) or "default"


def _timing_text(timing: dict[str, Any]) -> str | None:
    parts = []
    for key, label in (("real_seconds", "real"), ("user_seconds", "user"), ("sys_seconds", "sys")):
        value = timing.get(key)
        if isinstance(value, (int, float)):
            parts.append(f"{label}={value:.2f}s")
    return " ".join(parts) or None


def _file_line(ref: dict[str, Any]) -> str:
    path = str(ref.get("path", ""))
    role = ref.get("role")
    prefix = f"{role}: " if role else ""
    if "exists" in ref:
        exists = bool(ref.get("exists"))
    else:
        exists = Path(path).exists() if path else False
    status = "exists" if exists else "missing"
    details = []
    if ref.get("kind"):
        details.append(str(ref["kind"]))
    if ref.get("size_bytes") is not None:
        details.append(f"{ref['size_bytes']} B")
    suffix = ("; " + ", ".join(details)) if details else ""
    return f"{prefix}{path} [{status}{suffix}]"


def _problem_task(task: dict[str, Any], scheduler: dict[str, Any]) -> bool:
    if task.get("state") in PROBLEM_STATES:
        return True
    node = (scheduler.get("nodes") or {}).get(task["name"]) or {}
    return node.get("state") in SCHEDULER_PROBLEM_STATES


def _scheduler_detail(node: dict[str, Any]) -> str | None:
    if not node:
        return None
    parts = []
    for key in ("state", "job_id", "scheduler_state"):
        if node.get(key) is not None:
            parts.append(f"{key}={node[key]}")
    if node.get("hold_reason"):
        parts.append(f"reason={node['hold_reason']}")
    if node.get("hold_reason_code") is not None:
        code = str(node["hold_reason_code"])
        if node.get("hold_reason_subcode") is not None:
            code += f"/{node['hold_reason_subcode']}"
        parts.append(f"hold={code}")
    return " ".join(parts) or None


def _append_log_tail(lines: list[str], label: str, path: Path, count: int) -> None:
    tail = _tail(path, count)
    if not tail:
        return
    lines.append(f"    {label} tail ({len(tail)} lines):")
    lines.extend(f"      {line}" for line in tail)


def _attempt_amendment_numbers(
    directory: Path | None, attempt: dict[str, Any]
) -> tuple[int, ...]:
    if directory is None:
        return ()
    raw_path = attempt.get("provenance")
    provenance_path = Path(str(raw_path)) if raw_path else directory / "provenance.json"
    if not provenance_path.is_file():
        return ()
    try:
        provenance = _read_json(provenance_path)
    except (OSError, json.JSONDecodeError, ValueError):
        return ()
    task = provenance.get("task") or {}
    amendments = task.get("amendments") if isinstance(task, dict) else None
    if not isinstance(amendments, list):
        return ()
    numbers: list[int] = []
    for item in amendments:
        if not isinstance(item, dict) or item.get("number") is None:
            continue
        try:
            number = int(item["number"])
        except (TypeError, ValueError):
            continue
        if number not in numbers:
            numbers.append(number)
    return tuple(numbers)


def _attempt_history(campaign_dir: Path, task: dict[str, Any]) -> list[str]:
    lines = []
    attempts = int(task.get("attempts", 0))
    if attempts < 1:
        return lines
    lines.append("    attempt history:")
    show_command_transitions = attempts > 1
    previous_command: object = object()
    previous_amendments: tuple[int, ...] | None = None
    for number in range(1, attempts + 1):
        directory, attempt = _attempt_record(campaign_dir, task, number)
        if attempt is None:
            lines.append(f"      {number}: no readable attempt.json ({directory})")
            continue
        summary = [f"{number}: {attempt.get('state', 'unknown')}"]
        if attempt.get("returncode") is not None:
            summary.append(f"returncode={attempt['returncode']}")
        failure = _failure_text(attempt)
        if failure:
            summary.append(f"failure={failure}")
        timing = _timing_text(attempt.get("timing") or {})
        if timing:
            summary.append(timing)
        lines.append("      " + " ".join(summary))

        if show_command_transitions:
            command = attempt.get("command")
            amendments = _attempt_amendment_numbers(directory, attempt)
            if command is not None and command != previous_command:
                label = "command" if previous_amendments is None else "command changed"
                lines.append(f"        {label}: {_display_command(command)}")
            if amendments and amendments != previous_amendments:
                lines.append(
                    "        amendments: "
                    + ",".join(f"{item:04d}" for item in amendments)
                )
            previous_command = command
            previous_amendments = amendments

        stderr_path = Path(str(attempt.get("stderr") or (directory / "stderr.log" if directory else "")))
        last = _last_nonempty(stderr_path)
        if last:
            lines.append(f"        stderr last: {last}")
    return lines


def _resume_history(campaign_dir: Path, limit: int = 3) -> list[str]:
    root = campaign_dir / "resumes"
    if not root.is_dir():
        return []
    records: list[tuple[int, Path]] = []
    for path in root.glob("resume_*.json"):
        stem = path.stem.removeprefix("resume_")
        if stem.isdigit():
            records.append((int(stem), path))
    for directory in root.iterdir():
        if directory.is_dir() and directory.name.isdigit() and (directory / "resume.json").is_file():
            records.append((int(directory.name), directory / "resume.json"))
    lines = []
    for number, path in sorted(records)[-limit:]:
        try:
            record = _read_json(path)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        result = record.get("result", record.get("status", "unknown"))
        text = f"  resume {number:04d}: {result}"
        if record.get("reason"):
            text += f" reason={record['reason']}"
        selected = record.get("selected") or []
        if selected:
            shown = ",".join(str(item) for item in selected[:5])
            if len(selected) > 5:
                shown += ",..."
            text += f" selected={shown}"
        lines.append(text)
    return lines



def _condor_history_detail(job: dict[str, Any]) -> str:
    parts = [f"job={job.get('job_id', 'unknown')}", f"state={job.get('state', 'unknown')}"]
    if job.get("hold_reason_code") is not None:
        code = str(job["hold_reason_code"])
        if job.get("hold_reason_subcode") is not None:
            code += f"/{job['hold_reason_subcode']}"
        parts.append(f"hold={code}")
    if job.get("hold_reason"):
        parts.append(f"reason={job['hold_reason']}")
    if job.get("remove_reason"):
        parts.append(f"remove={job['remove_reason']}")
    if job.get("exit_code") is not None:
        parts.append(f"exit={job['exit_code']}")
    if job.get("exit_by_signal"):
        parts.append(f"signal={job.get('exit_signal', 'unknown')}")
    host = job.get("remote_host") or job.get("last_remote_host")
    if host:
        parts.append(f"host={host}")
    return " ".join(parts)


def _condor_submit_attributes(path: Path) -> dict[str, str]:
    wanted = {
        "executable", "arguments", "output", "error", "log",
        "request_cpus", "request_memory", "request_disk", "requirements",
    }
    values: dict[str, str] = {}
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return values
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lower()
        if key in wanted:
            values[key] = value.strip()
    return values


def _condor_artifact_lines(campaign_dir: Path, task_name: str) -> list[str]:
    render_path = campaign_dir / "condor" / "render.json"
    if not render_path.is_file():
        return []
    try:
        render = _read_json(render_path)
    except (OSError, json.JSONDecodeError, ValueError):
        return []
    node = (render.get("node_names") or {}).get(task_name)
    if not node:
        return []

    roots: list[tuple[str, Path]] = [("initial", campaign_dir / "condor")]
    resumes = campaign_dir / "resumes"
    if resumes.is_dir():
        for round_dir in sorted(resumes.iterdir()):
            condor_dir = round_dir / "condor"
            if round_dir.is_dir() and round_dir.name.isdigit() and condor_dir.is_dir():
                roots.append((f"resume {int(round_dir.name):04d}", condor_dir))
    existing = [(label, root) for label, root in roots if (root / f"{node}.sub").is_file()]
    if len(existing) > 4:
        existing = [existing[0], *existing[-3:]]
    if not existing:
        return []

    lines = ["    condor artifacts:"]
    for label, root in existing:
        submit = root / f"{node}.sub"
        lines.append(f"      {label}:")
        lines.append(f"        submit: {submit}")
        dag = root / "campaign.dag"
        if dag.is_file():
            lines.append(f"        dag: {dag}")
        rescues = sorted(root.glob("campaign.dag.rescue*"))
        if rescues:
            lines.append(f"        rescue: {rescues[-1]}")
        submit_record = root / "submit.json"
        if submit_record.is_file():
            lines.append(f"        submission: {submit_record}")
        for key, value in _condor_submit_attributes(submit).items():
            lines.append(f"        {key} = {value}")
    return lines


def render_status(campaign_dir: str | Path, data: dict[str, Any], verbosity: int = 0) -> str:
    """Render human-readable campaign status with bounded diagnostics."""
    campaign_dir = Path(campaign_dir).expanduser().absolute()
    manifest = _read_json(campaign_dir / "campaign.json")
    backend = str(data["backend"])
    scheduler = data.get("scheduler") or {}
    active_nodes = scheduler.get("nodes", {}) or {}

    lines = [f"Campaign {data['id']} ({backend})"]
    for task in data["tasks"]:
        suffix = ""
        active = active_nodes.get(task["name"])
        if active:
            suffix = f" {backend}={active['state']} job={active['job_id']}"
        lines.append(
            f"  {task['name']:<20} {task['state']:<10} "
            f"attempts={task['attempts']}{suffix}"
        )

    if scheduler.get("query_ok") is False:
        lines.append(f"  scheduler: unknown ({scheduler.get('error', 'query failed')})")
    elif data.get("scheduler") is not None:
        counts = scheduler.get("counts", {})
        node_summary = ", ".join(
            f"{name}={count}" for name, count in sorted(counts.items())
        ) or "no active nodes"
        if backend == "condor":
            dagman = scheduler.get("dagman") or "not-in-queue"
            lines.append(
                f"  scheduler: dagman={dagman} "
                f"cluster={scheduler.get('cluster_id')}; nodes: {node_summary}"
            )
        else:
            lines.append(f"  scheduler: {backend}; nodes: {node_summary}")

    if verbosity < 1:
        return "\n".join(lines)

    targets = [task for task in data["tasks"] if _problem_task(task, scheduler)]
    if verbosity >= 3:
        seen = {task["name"] for task in targets}
        targets.extend(
            task for task in data["tasks"]
            if int(task.get("attempts", 0)) > 1 and task["name"] not in seen
        )
    lines.append("")
    if not targets:
        lines.append("Diagnostics: no failed, interrupted, held, or unknown tasks")
    else:
        lines.append("Diagnostics:")

    task_states = {task["name"]: task["state"] for task in data["tasks"]}
    active_jobs = scheduler.get("active_jobs", {}) or {}
    scheduler_history = data.get("scheduler_history") or {}
    history_by_task: dict[str, list[dict[str, Any]]] = {}
    if scheduler_history.get("query_ok"):
        for job in scheduler_history.get("jobs", []):
            task_name = job.get("task")
            if task_name:
                history_by_task.setdefault(str(task_name), []).append(job)
    if scheduler.get("query_ok") is False:
        lines.append(f"  scheduler query: {scheduler.get('error', 'unknown')}")
    for job_id, job in sorted(active_jobs.items()):
        if job.get("task") is None and job.get("state") in SCHEDULER_PROBLEM_STATES:
            detail = _scheduler_detail({**job, "job_id": job_id})
            if detail:
                lines.append(f"  scheduler job: {detail}")

    for task in targets:
        definition = _task_definition(campaign_dir, manifest, task["name"])
        directory, attempt = _attempt_record(campaign_dir, task)
        number = int(task.get("attempts", 0))
        heading = f"  {task['name']}: {task['state']}"
        if number:
            heading += f" attempt={number}"
        lines.append(heading)
        recorded = task.get("recorded_state")
        if recorded and recorded != task.get("state"):
            lines.append(f"    recorded_state={recorded}")
        if task.get("state") == "blocked":
            parents = list(definition.get("parents") or [])
            if parents:
                parent_text = " ".join(
                    f"{parent}={task_states.get(parent, 'unknown')}" for parent in parents
                )
                lines.append(f"    parents: {parent_text}")
        if attempt is not None:
            status_parts = []
            if attempt.get("returncode") is not None:
                status_parts.append(f"returncode={attempt['returncode']}")
            if attempt.get("command_returncode") is not None:
                status_parts.append(f"command_returncode={attempt['command_returncode']}")
            failure = _failure_text(attempt)
            if failure:
                status_parts.append(f"failure={failure}")
            if status_parts:
                lines.append("    " + " ".join(status_parts))
        node = active_nodes.get(task["name"]) or {}
        scheduler_text = _scheduler_detail(node)
        if scheduler_text:
            lines.append(f"    scheduler: {scheduler_text}")
        if directory is not None:
            stderr_path = Path(str((attempt or {}).get("stderr") or directory / "stderr.log"))
            last = _last_nonempty(stderr_path)
            if last:
                lines.append(f"    stderr last: {last}")
            lines.append(f"    attempt: {directory}")

        if verbosity >= 2:
            command = (attempt or {}).get("command", definition.get("command"))
            if command is not None:
                lines.append(f"    command: {_display_command(command)}")
            cwd = (attempt or {}).get("cwd", definition.get("cwd"))
            if cwd:
                lines.append(f"    cwd: {cwd}")
            resources = definition.get("resources") or {}
            lines.append(f"    resources: {_resource_text(resources)}")
            if attempt is not None:
                timing = _timing_text(attempt.get("timing") or {})
                if timing:
                    lines.append(f"    timing: {timing}")
                inputs = list(attempt.get("inputs") or definition.get("inputs") or [])
                outputs = list(attempt.get("outputs") or attempt.get("outputs_before") or definition.get("outputs") or [])
                if inputs:
                    lines.append("    inputs:")
                    lines.extend(f"      {_file_line(ref)}" for ref in inputs)
                if outputs:
                    lines.append("    outputs:")
                    lines.extend(f"      {_file_line(ref)}" for ref in outputs)
                if directory is not None:
                    stdout_path = Path(str(attempt.get("stdout") or directory / "stdout.log"))
                    stderr_path = Path(str(attempt.get("stderr") or directory / "stderr.log"))
                    lines.append(f"    stdout: {stdout_path}")
                    lines.append(f"    stderr: {stderr_path}")
                    _append_log_tail(lines, "stderr", stderr_path, 20 if verbosity == 2 else 50)
                    _append_log_tail(lines, "stdout", stdout_path, 10 if verbosity == 2 else 50)

        if verbosity >= 3:
            lines.extend(_attempt_history(campaign_dir, task))
            if directory is not None:
                provenance_path = directory / "provenance.json"
                if provenance_path.is_file():
                    try:
                        provenance = _read_json(provenance_path)
                    except (OSError, json.JSONDecodeError, ValueError):
                        provenance = {}
                    execution = provenance.get("execution") or {}
                    ptask = provenance.get("task") or {}
                    launch = execution.get("launch_command")
                    if launch:
                        lines.append(f"    launch: {_display_command(launch)}")
                    wrapper = execution.get("wrapper")
                    if wrapper:
                        lines.append(f"    wrapper: {json.dumps(wrapper, sort_keys=True)}")
                    amendments = ptask.get("amendments") or []
                    if amendments:
                        numbers = ",".join(str(item.get("number")) for item in amendments)
                        lines.append(f"    amendments: {numbers}")
                    lines.append(f"    provenance: {provenance_path}")
            if backend == "condor":
                history = history_by_task.get(task["name"], [])
                if history:
                    lines.append("    condor history:")
                    for job in history[-5:]:
                        lines.append(f"      {_condor_history_detail(job)}")
                lines.extend(_condor_artifact_lines(campaign_dir, task["name"]))

    if verbosity >= 3:
        recovery = _resume_history(campaign_dir)
        if recovery:
            lines.append("")
            lines.append("Recent recovery rounds:")
            lines.extend(recovery)
        if backend == "condor" and scheduler_history and not scheduler_history.get("query_ok"):
            lines.append("")
            lines.append(
                f"Condor history: unavailable ({scheduler_history.get('error', 'query failed')})"
            )
        if data.get("scheduler") is not None:
            lines.append("")
            lines.append("Scheduler diagnostics:")
            if scheduler.get("query_ok") is False:
                lines.append(f"  query_error: {scheduler.get('error', 'unknown')}")
            else:
                lines.append(f"  backend={scheduler.get('backend', backend)} cluster={scheduler.get('cluster_id')}")
                if scheduler.get("dagman"):
                    lines.append(f"  dagman={scheduler['dagman']}")
                for name, node in sorted(active_nodes.items()):
                    detail = _scheduler_detail(node)
                    if detail:
                        lines.append(f"  {name}: {detail}")
                for job_id, job in sorted(active_jobs.items()):
                    if job.get("task") is not None:
                        continue
                    detail = _scheduler_detail({**job, "job_id": job_id})
                    if detail:
                        lines.append(f"  scheduler-job {job_id}: {detail}")

    if verbosity >= 4:
        lines.append("")
        lines.extend(render_condor_execution_trace(campaign_dir, data))

    return "\n".join(lines)
