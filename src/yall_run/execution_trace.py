from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
from typing import Any


MAX_EVENT_LOG_BYTES = 64 * 1024 * 1024
EVENT_NAMES = {
    0: "submit",
    1: "execute",
    2: "executable-error",
    4: "evicted",
    5: "terminated",
    7: "shadow-exception",
    9: "aborted",
    10: "suspended",
    11: "unsuspended",
    12: "held",
    13: "released",
    21: "remote-error",
    22: "disconnected",
    23: "reconnected",
    24: "reconnect-failed",
    28: "job-ad-information",
}
_END_EXECUTION_CODES = {2, 4, 5, 7, 9, 12, 21, 24}
_INTERESTING_CODES = {2, 4, 7, 9, 12, 21, 24}
_HEADER_RE = re.compile(
    r"^(?P<code>\d{3}) \((?P<cluster>\d+)\.(?P<proc>\d+)\.(?P<subproc>\d+)\)\s+(?P<rest>.*)$"
)
_NORMAL_EXIT_RE = re.compile(r"Normal termination \(return value (-?\d+)\)", re.I)
_SIGNAL_EXIT_RE = re.compile(r"Abnormal termination \(signal (\d+)\)", re.I)
_HOST_RE = re.compile(r"(?:executing on host|host):\s*<([^>]+)>", re.I)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _clip(text: str, limit: int = 500) -> str:
    value = " ".join(text.split())
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _split_timestamp(rest: str) -> tuple[str | None, str]:
    parts = rest.split(maxsplit=2)
    if not parts:
        return None, ""
    if "T" in parts[0] and (
        re.match(r"^\d{4}-\d{2}-\d{2}T", parts[0]) or parts[0].endswith("Z")
    ):
        return parts[0], parts[1] if len(parts) > 1 else ""
    if len(parts) >= 2 and (
        re.match(r"^\d{2}/\d{2}$", parts[0])
        or re.match(r"^\d{4}-\d{2}-\d{2}$", parts[0])
    ):
        message = parts[2] if len(parts) > 2 else ""
        return f"{parts[0]} {parts[1]}", message
    return None, rest


def _bounded_event_bytes(path: Path) -> tuple[bytes, bool, str | None]:
    try:
        info = path.stat()
    except OSError as exc:
        return b"", False, str(exc)
    if not stat.S_ISREG(info.st_mode):
        return b"", False, "event log is not a regular file"
    truncated = info.st_size > MAX_EVENT_LOG_BYTES
    try:
        with path.open("rb") as handle:
            if truncated:
                handle.seek(info.st_size - MAX_EVENT_LOG_BYTES)
            data = handle.read(MAX_EVENT_LOG_BYTES + 1)
    except OSError as exc:
        return b"", truncated, str(exc)
    if len(data) > MAX_EVENT_LOG_BYTES:
        data = data[:MAX_EVENT_LOG_BYTES]
        truncated = True
    if truncated:
        # A tail may begin in the middle of one event. Drop through the first
        # complete separator so every retained event begins at a real header.
        marker = data.find(b"\n...\n")
        if marker < 0:
            return b"", True, "bounded tail contains no complete event separator"
        data = data[marker + len(b"\n...\n") :]
    return data, truncated, None


def parse_condor_event_log(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    data, truncated, error = _bounded_event_bytes(path)
    result: dict[str, Any] = {
        "path": str(path),
        "read_ok": error is None,
        "truncated": truncated,
        "events": [],
        "parse_errors": [],
    }
    if error is not None:
        result["error"] = error
        return result

    blocks: list[list[str]] = []
    current: list[str] = []
    for raw in data.decode(errors="replace").splitlines():
        if raw.strip() == "...":
            if current:
                blocks.append(current)
                current = []
            continue
        current.append(raw.rstrip())
    if current:
        blocks.append(current)

    for index, block in enumerate(blocks, 1):
        if not block:
            continue
        match = _HEADER_RE.match(block[0])
        if match is None:
            result["parse_errors"].append(
                f"event {index}: unrecognized header: {_clip(block[0], 160)}"
            )
            continue
        code = int(match.group("code"))
        rest = match.group("rest")
        timestamp, message = _split_timestamp(rest)
        details = [line.strip() for line in block[1:] if line.strip()]
        joined = "\n".join(block)
        event: dict[str, Any] = {
            "code": code,
            "type": EVENT_NAMES.get(code, f"event-{code:03d}"),
            "job_id": f"{int(match.group('cluster'))}.{int(match.group('proc'))}",
            "timestamp": timestamp,
            "message": _clip(message, 500),
        }
        if details:
            event["detail"] = _clip(details[0], 500)
        host = _HOST_RE.search(joined)
        if host:
            event["host"] = host.group(1)
        normal = _NORMAL_EXIT_RE.search(joined)
        signal = _SIGNAL_EXIT_RE.search(joined)
        if normal:
            event["exit_code"] = int(normal.group(1))
        elif signal:
            event["exit_signal"] = int(signal.group(1))
        result["events"].append(event)
    return result


def _event_summary(event: dict[str, Any]) -> str:
    label = str(event.get("type", "event"))
    parts = [label]
    if event.get("timestamp"):
        parts.append(str(event["timestamp"]))
    if event.get("host"):
        parts.append(f"host={event['host']}")
    if event.get("exit_code") is not None:
        parts.append(f"exit={event['exit_code']}")
    if event.get("exit_signal") is not None:
        parts.append(f"signal={event['exit_signal']}")
    reason = event.get("detail") or event.get("message")
    if reason and event.get("code") in _INTERESTING_CODES:
        parts.append(f"reason={_clip(str(reason), 220)}")
    return " ".join(parts)


def _execution_result(event: dict[str, Any] | None) -> str:
    if event is None:
        return "execution end not recorded"
    code = int(event.get("code", -1))
    if code == 5:
        exit_code = event.get("exit_code")
        if exit_code == 0:
            return "completed exit=0"
        if exit_code == 100:
            return "startup failure before Yall payload marker exit=100"
        if exit_code == 101:
            return "Yall worker failure after startup classification exit=101"
        if exit_code is not None:
            return f"terminated exit={exit_code}"
        if event.get("exit_signal") is not None:
            return f"terminated signal={event['exit_signal']}"
        return "terminated"
    labels = {
        2: "executable error",
        4: "scheduler eviction",
        7: "shadow exception",
        9: "job aborted/removed",
        12: "job held",
        21: "remote execution error",
        24: "reconnect failed",
    }
    return labels.get(code, str(event.get("type", "execution ended")))


def _job_executions(events: list[dict[str, Any]], incomplete: bool) -> dict[str, Any]:
    ordered: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        ordered.setdefault(str(event["job_id"]), []).append(event)

    jobs: dict[str, Any] = {}
    for job_id, job_events in ordered.items():
        executions: list[dict[str, Any]] = []
        outside: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        submit_seen = False
        for event in job_events:
            code = int(event["code"])
            if code == 0:
                submit_seen = True
                outside.append(event)
                continue
            if code == 1:
                current = {
                    "observed_index": len(executions) + 1,
                    "execute": event,
                    "events": [event],
                    "end": None,
                }
                executions.append(current)
                continue
            if current is not None:
                current["events"].append(event)
                if code in _END_EXECUTION_CODES:
                    current["end"] = event
                    current = None
            else:
                outside.append(event)
        jobs[job_id] = {
            "job_id": job_id,
            "complete_from_submit": bool(submit_seen and not incomplete),
            "executions": executions,
            "outside_events": outside,
        }
    return jobs


def _generation_records(campaign_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    def add(number: int, root: Path, reason: str | None = None) -> None:
        submit: dict[str, Any] = {}
        submit_path = root / "submit.json"
        if submit_path.is_file():
            try:
                submit = _read_json(submit_path)
            except (OSError, json.JSONDecodeError, ValueError):
                submit = {}
        records.append(
            {
                "number": number,
                "label": "initial" if number == 0 else f"resume {number:04d}",
                "root": root,
                "cluster_id": submit.get("cluster_id"),
                "reason": reason,
                "event_log_path": root / "events.log",
            }
        )

    add(0, campaign_dir / "condor")
    resumes = campaign_dir / "resumes"
    if resumes.is_dir():
        for directory in sorted(resumes.iterdir()):
            if not directory.is_dir() or not directory.name.isdigit():
                continue
            condor = directory / "condor"
            if not condor.is_dir():
                continue
            reason = None
            resume_path = directory / "resume.json"
            if resume_path.is_file():
                try:
                    reason = _read_json(resume_path).get("reason")
                except (OSError, json.JSONDecodeError, ValueError):
                    pass
            add(int(directory.name), condor, str(reason) if reason else None)
    return records


def _attempt_records(campaign_dir: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    order = manifest.get("task_order")
    if not isinstance(order, list):
        tasks = manifest.get("tasks", {})
        order = list(tasks) if isinstance(tasks, dict) else []
    result: list[dict[str, Any]] = []
    for raw_name in order:
        name = str(raw_name)
        prefix = f"{name}_attempt_"
        try:
            candidates = sorted(
                directory
                for directory in campaign_dir.iterdir()
                if directory.is_dir() and directory.name.startswith(prefix)
            )
        except OSError:
            candidates = []
        for directory in candidates:
            suffix = directory.name[len(prefix) :]
            if not suffix.isdigit():
                continue
            number = int(suffix)
            attempt: dict[str, Any] = {}
            provenance: dict[str, Any] = {}
            try:
                if (directory / "attempt.json").is_file():
                    attempt = _read_json(directory / "attempt.json")
            except (OSError, json.JSONDecodeError, ValueError):
                pass
            try:
                if (directory / "provenance.json").is_file():
                    provenance = _read_json(directory / "provenance.json")
            except (OSError, json.JSONDecodeError, ValueError):
                pass
            scheduler = provenance.get("scheduler")
            if not isinstance(scheduler, dict) or scheduler.get("backend") != "condor":
                scheduler = {}
            failure = attempt.get("failure")
            failure_kind = failure.get("kind") if isinstance(failure, dict) else None
            task_prov = provenance.get("task")
            amendments = task_prov.get("amendments") if isinstance(task_prov, dict) else []
            result.append(
                {
                    "task": name,
                    "attempt": number,
                    "state": attempt.get("state", "unknown"),
                    "returncode": attempt.get("returncode"),
                    "command_returncode": attempt.get("command_returncode"),
                    "failure_kind": failure_kind,
                    "started_at": attempt.get("started_at"),
                    "finished_at": attempt.get("finished_at"),
                    "scheduler": scheduler,
                    "amendments": amendments if isinstance(amendments, list) else [],
                    "directory": str(directory),
                }
            )
    return result


def _merge_evidence(target: dict[str, Any], source: dict[str, Any], origin: str) -> None:
    for key in (
        "task",
        "dagman_job_id",
        "dag_retry",
        "num_job_starts",
        "global_job_id",
        "remote_host",
        "last_remote_host",
    ):
        value = source.get(key)
        if value is None:
            continue
        if key in target and target[key] != value:
            target.setdefault("conflicts", {}).setdefault(key, [target[key]])
            if value not in target["conflicts"][key]:
                target["conflicts"][key].append(value)
            if key == "task":
                target["task"] = None
        else:
            target[key] = value
    target.setdefault("evidence", []).append(origin)


def _job_evidence(data: dict[str, Any], attempts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    jobs: dict[str, dict[str, Any]] = {}

    for attempt in attempts:
        scheduler = attempt.get("scheduler") or {}
        job_id = scheduler.get("job_id")
        if not job_id:
            continue
        item = jobs.setdefault(str(job_id), {"job_id": str(job_id), "attempts": []})
        item["attempts"].append(attempt)
        _merge_evidence(
            item,
            {
                "task": attempt["task"],
                "dagman_job_id": scheduler.get("dagman_job_id"),
                "dag_retry": scheduler.get("dag_retry"),
                "num_job_starts": scheduler.get("num_job_starts"),
                "global_job_id": scheduler.get("global_job_id"),
                "remote_host": scheduler.get("remote_host"),
                "last_remote_host": scheduler.get("last_remote_host"),
            },
            f"Yall attempt {attempt['attempt']}",
        )

    history = data.get("scheduler_history") or {}
    if history.get("query_ok"):
        for record in history.get("jobs", []):
            job_id = record.get("job_id")
            if not job_id:
                continue
            item = jobs.setdefault(str(job_id), {"job_id": str(job_id), "attempts": []})
            _merge_evidence(item, record, "condor_history")
            item["history"] = record

    scheduler = data.get("scheduler") or {}
    for task, record in (scheduler.get("nodes") or {}).items():
        job_id = record.get("job_id")
        if not job_id:
            continue
        item = jobs.setdefault(str(job_id), {"job_id": str(job_id), "attempts": []})
        _merge_evidence(item, {**record, "task": task}, "condor_q")
        item["live"] = record
    for job_id, record in (scheduler.get("active_jobs") or {}).items():
        item = jobs.setdefault(str(job_id), {"job_id": str(job_id), "attempts": []})
        _merge_evidence(item, record, "condor_q")
        item["live"] = record
    return jobs


def build_condor_execution_trace(
    campaign_dir: str | Path, data: dict[str, Any]
) -> dict[str, Any]:
    campaign_dir = Path(campaign_dir).expanduser().absolute()
    result: dict[str, Any] = {
        "backend": data.get("backend"),
        "generations": [],
        "orphan_attempts": [],
        "warnings": [],
    }
    if data.get("backend") != "condor":
        return result
    try:
        manifest = _read_json(campaign_dir / "campaign.json")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        result["warnings"].append(f"cannot read campaign manifest: {exc}")
        return result

    attempts = _attempt_records(campaign_dir, manifest)
    evidence = _job_evidence(data, attempts)
    seen_jobs: set[str] = set()
    cluster_to_generation: dict[int, dict[str, Any]] = {}

    for generation in _generation_records(campaign_dir):
        cluster = generation.get("cluster_id")
        if isinstance(cluster, int):
            cluster_to_generation[cluster] = generation
        event_log = parse_condor_event_log(generation["event_log_path"])
        generation["event_log"] = event_log
        jobs = _job_executions(
            event_log.get("events", []),
            bool(event_log.get("truncated") or event_log.get("parse_errors")),
        )
        output_jobs: list[dict[str, Any]] = []
        for job_id, parsed in jobs.items():
            info = evidence.get(job_id, {"job_id": job_id, "attempts": []})
            job = {**parsed}
            for key, value in info.items():
                if key not in {"job_id"}:
                    job[key] = value
            attached_ids: set[tuple[str, int]] = set()
            for attempt in list(job.get("attempts", [])):
                scheduler = attempt.get("scheduler") or {}
                starts = scheduler.get("num_job_starts")
                if (
                    job.get("complete_from_submit")
                    and isinstance(starts, int)
                    and 1 <= starts <= len(job["executions"])
                ):
                    execution = job["executions"][starts - 1]
                    execution.setdefault("attempts", []).append(
                        {**attempt, "association": "direct"}
                    )
                    attached_ids.add((attempt["task"], attempt["attempt"]))
            if attached_ids:
                job["attempts_unlinked"] = [
                    attempt
                    for attempt in job.get("attempts", [])
                    if (attempt["task"], attempt["attempt"]) not in attached_ids
                ]
            else:
                job["attempts_unlinked"] = list(job.get("attempts", []))
            output_jobs.append(job)
            seen_jobs.add(job_id)
        generation["jobs"] = output_jobs
        result["generations"].append(generation)

    # Preserve evidence for jobs whose event log is missing, rotated away, or
    # otherwise did not contain a parseable event for the job.
    for job_id, info in evidence.items():
        if job_id in seen_jobs:
            continue
        dagman = info.get("dagman_job_id")
        generation = cluster_to_generation.get(dagman) if isinstance(dagman, int) else None
        if generation is None:
            continue
        generation["jobs"].append(
            {
                "job_id": job_id,
                "complete_from_submit": False,
                "executions": [],
                "outside_events": [],
                **{key: value for key, value in info.items() if key != "job_id"},
                "attempts_unlinked": list(info.get("attempts", [])),
            }
        )
        seen_jobs.add(job_id)

    linked_attempts = {
        (attempt["task"], attempt["attempt"])
        for info in evidence.values()
        for attempt in info.get("attempts", [])
        if info["job_id"] in seen_jobs
    }
    result["orphan_attempts"] = [
        attempt
        for attempt in attempts
        if (attempt["task"], attempt["attempt"]) not in linked_attempts
    ]
    return result


def _attempt_line(attempt: dict[str, Any], association: str | None = None) -> str:
    parts = [
        f"Yall attempt {attempt['attempt']}",
        f"state={attempt.get('state', 'unknown')}",
    ]
    if attempt.get("returncode") is not None:
        parts.append(f"returncode={attempt['returncode']}")
    if attempt.get("command_returncode") is not None:
        parts.append(f"command_returncode={attempt['command_returncode']}")
    if attempt.get("failure_kind"):
        parts.append(f"failure={attempt['failure_kind']}")
    amendments = attempt.get("amendments") or []
    if amendments:
        nums = ",".join(
            str(item.get("number"))
            for item in amendments
            if item.get("number") is not None
        )
        if nums:
            parts.append(f"amendments={nums}")
    if association:
        parts.append(f"association={association}")
    return " ".join(parts)


def _job_interesting(
    job: dict[str, Any],
    generation_number: int,
    task_states: dict[str, str],
) -> bool:
    task = job.get("task")
    if task and task_states.get(str(task)) in {
        "failed",
        "blocked",
        "interrupted",
        "unknown",
        "held",
        "suspended",
    }:
        return True
    if generation_number > 0:
        return True
    if isinstance(job.get("dag_retry"), int) and int(job["dag_retry"]) > 0:
        return True
    if len(job.get("executions", [])) > 1:
        return True
    attempts = list(job.get("attempts", []))
    if len(attempts) > 1 or any(a.get("state") == "failed" for a in attempts):
        return True
    return any(
        int(event.get("code", -1)) in _INTERESTING_CODES
        for execution in job.get("executions", [])
        for event in execution.get("events", [])
    ) or any(
        int(event.get("code", -1)) in _INTERESTING_CODES
        for event in job.get("outside_events", [])
    )


def render_condor_execution_trace(
    campaign_dir: str | Path, data: dict[str, Any]
) -> list[str]:
    if data.get("backend") != "condor":
        return ["Execution trace: detailed scheduler execution trace is currently Condor-only"]

    trace = build_condor_execution_trace(campaign_dir, data)
    task_states = {
        str(task["name"]): str(task["state"]) for task in data.get("tasks", [])
    }
    interesting: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for generation in trace["generations"]:
        jobs = [
            job
            for job in generation.get("jobs", [])
            if _job_interesting(job, int(generation["number"]), task_states)
        ]
        if jobs:
            interesting.append((generation, jobs))

    lines = ["Execution trace:"]
    if not interesting and not trace.get("orphan_attempts"):
        lines.append("  no retry, restart, hold, failure, or resume activity observed")
        return lines

    for generation, jobs in interesting:
        heading = f"  generation {generation['number']} {generation['label']}"
        if generation.get("cluster_id") is not None:
            heading += f" DAGMan={generation['cluster_id']}"
        if generation.get("reason"):
            heading += f" reason={generation['reason']}"
        lines.append(heading)
        event_log = generation.get("event_log") or {}
        if not event_log.get("read_ok"):
            lines.append(
                f"    event log unavailable: {event_log.get('path')} "
                f"({event_log.get('error', 'unknown error')})"
            )
        else:
            suffix = (
                " [bounded tail; earlier events unavailable]"
                if event_log.get("truncated")
                else ""
            )
            lines.append(f"    event log: {event_log.get('path')}{suffix}")
            for error in event_log.get("parse_errors", [])[:3]:
                lines.append(f"      parse warning: {error}")

        for job in jobs:
            task = job.get("task") or "task=unknown"
            job_line = f"    {task} job={job['job_id']}"
            if job.get("dag_retry") is not None:
                job_line += f" dag-retry={job['dag_retry']}"
            if job.get("num_job_starts") is not None:
                job_line += f" NumJobStarts={job['num_job_starts']}"
            if job.get("conflicts"):
                job_line += " evidence-conflict"
            lines.append(job_line)

            for event in job.get("outside_events", []):
                code = int(event.get("code", -1))
                if code in {0, 28}:
                    continue
                if code in _INTERESTING_CODES or code in {13, 22, 23}:
                    lines.append(f"      {_event_summary(event)}")

            for execution in job.get("executions", []):
                execute = execution["execute"]
                if job.get("complete_from_submit"):
                    label = f"start {execution['observed_index']}"
                else:
                    label = f"observed start {execution['observed_index']}"
                if execute.get("timestamp"):
                    label += f" {execute['timestamp']}"
                if execute.get("host"):
                    label += f" host={execute['host']}"
                lines.append(f"      {label}")
                for event in execution.get("events", [])[1:-1]:
                    if int(event.get("code", -1)) in {10, 11, 22, 23}:
                        lines.append(f"        {_event_summary(event)}")
                for attempt in execution.get("attempts", []):
                    lines.append(f"        {_attempt_line(attempt, 'direct')}")
                end = execution.get("end")
                if end is not None:
                    text = _execution_result(end)
                    if end.get("timestamp"):
                        text += f" at {end['timestamp']}"
                    reason = end.get("detail") or (
                        end.get("message")
                        if int(end.get("code", -1)) in _INTERESTING_CODES
                        else None
                    )
                    if reason and int(end.get("code", -1)) in _INTERESTING_CODES:
                        text += f" reason={_clip(str(reason), 220)}"
                    lines.append(f"        {text}")
                else:
                    lines.append("        execution end not recorded")

            for attempt in job.get("attempts_unlinked", []):
                lines.append(
                    f"      {_attempt_line(attempt)} "
                    "execution=unknown (job matched; start association unavailable)"
                )

        if event_log.get("read_ok") and not event_log.get("events") and jobs:
            lines.append("    no parseable scheduler events for the jobs above")

    if trace.get("orphan_attempts"):
        lines.append("  Yall attempts without scheduler execution identity:")
        for attempt in trace["orphan_attempts"]:
            lines.append(f"    {attempt['task']}: {_attempt_line(attempt)}")
    for warning in trace.get("warnings", []):
        lines.append(f"  warning: {warning}")
    return lines
