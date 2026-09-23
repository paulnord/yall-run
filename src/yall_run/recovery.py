"""Conservative workflow recovery for queued campaigns.

A scheduler restart is not a workflow resume: keep successful tasks, preserve
attempt evidence, and reconnect dependencies using newly submitted job IDs.
Only successful queue queries may be used as evidence that a job has gone.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import subprocess
from typing import Any

QUEUED_BACKENDS = {"condor", "slurm", "pbs"}
_TERMINAL = {"completed", "failed", "removed"}
_CONDOR_STATES = {1: "idle", 2: "running", 3: "removing", 4: "completed",
                  5: "held", 6: "transferring", 7: "suspended"}
_SLURM_TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "NODE_FAIL",
                   "OUT_OF_MEMORY", "PREEMPTED", "BOOT_FAIL", "DEADLINE", "REVOKED"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temp.replace(path)


def _load(campaign_dir: str | Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    # Keep logical AFS paths, just as campaign creation does.
    cdir = Path(os.path.abspath(os.path.expanduser(str(campaign_dir))))
    manifest = _read(cdir / "campaign.json")
    stored = manifest["tasks"]
    order = manifest.get("task_order", list(stored))
    tasks = {name: stored[name] if isinstance(stored, dict)
             else _read(cdir / "tasks" / f"{name}.json") for name in order}
    return cdir, manifest, tasks


def _state(cdir: Path, manifest: dict[str, Any], name: str) -> dict[str, Any]:
    path = cdir / "state" / f"{name}.json"
    if path.is_file():
        return _read(path)
    if not isinstance(manifest["tasks"], dict):
        return _read(cdir / "tasks" / f"{name}.json")
    return {"state": "pending", "attempts": 0}


def _records(cdir: Path, backend: str) -> list[tuple[Path, dict[str, Any]]]:
    paths = [cdir / backend / "submit.json"]
    paths += sorted((cdir / "resumes").glob(f"[0-9]*/{backend}/submit.json"))
    return [(path, _read(path)) for path in paths if path.is_file()]


def _run(argv: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=60)


def _checked(argv: list[str], cwd: Path | None = None) -> str:
    try:
        result = _run(argv, cwd)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"cannot verify scheduler state ({argv[0]}): {exc}") from exc
    if result.returncode:
        raise RuntimeError(f"{argv[0]} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def scheduler_snapshot(campaign_dir: str | Path) -> dict[str, Any]:
    """Read live state for every recorded submission, including older rounds.

    Do not confuse missing jobs with a failed query. Retained terminal jobs do
    not block recovery, but held/suspended/unknown jobs do. Run this against the
    same scheduler/server context used for the original submission.
    """
    cdir, manifest, _ = _load(campaign_dir)
    backend = manifest["backend"]
    records = _records(cdir, backend)
    snapshot: dict[str, Any] = {"backend": backend, "query_ok": False,
                               "nodes": {}, "counts": {}, "active_jobs": {},
                               "cluster_id": None, "dagman": None}
    try:
        if not records:
            raise RuntimeError("no recorded scheduler submission")
        if any(record.get("in_flight") for _, record in records):
            raise RuntimeError("an earlier submission has an unconfirmed scheduler response; "
                               "inspect its submit.json and the scheduler before recovery")
        nodes = snapshot["nodes"]
        active = snapshot["active_jobs"]
        if backend == "condor":
            submit_hosts = {
                str(record.get("submitter", {}).get("submit_host", "")).strip()
                for _, record in records
                if isinstance(record.get("submitter"), dict)
            }
            submit_hosts.discard("")
            current_host = socket.getfqdn()
            if submit_hosts and current_host not in submit_hosts:
                raise RuntimeError(
                    "campaign was submitted from "
                    f"{', '.join(sorted(submit_hosts))}; current host is {current_host}; "
                    "query that schedd from the submitting host"
                )
            clusters = {int(record["cluster_id"]) for _, record in records
                        if record.get("cluster_id") is not None}
            if not clusters:
                raise RuntimeError("no recorded DAGMan cluster ID")
            snapshot["cluster_id"] = next(record["cluster_id"] for _, record in reversed(records)
                                          if record.get("cluster_id") is not None)
            render = _read(cdir / "condor" / "render.json")
            names = {node: task for task, node in render["node_names"].items()}
            clauses = [f"(ClusterId == {i} || DAGManJobId == {i})" for i in sorted(clusters)]
            # Also find a manually restarted DAG in a known campaign directory.
            clauses += [f"Iwd == {json.dumps(str(path.parent))}" for path, _ in records]
            raw = _checked(["condor_q", "-json", "-constraint", " || ".join(clauses)])
            # Some Condor pools return success with empty stdout when no ads match.
            ads = [] if not raw.strip() else json.loads(raw)
            if not isinstance(ads, list):
                raise ValueError("condor_q did not return a JSON array")
            for ad in ads:
                cluster = int(ad["ClusterId"])
                job_id = f"{cluster}.{int(ad.get('ProcId', 0))}"
                state = _CONDOR_STATES.get(int(ad["JobStatus"]), "unknown")
                task = names.get(ad.get("DAGNodeName"))
                diagnostic: dict[str, Any] = {}
                hold_reason = ad.get("HoldReason") or ad.get("LastHoldReason")
                if hold_reason:
                    diagnostic["hold_reason"] = str(hold_reason)
                if ad.get("HoldReasonCode") is not None:
                    diagnostic["hold_reason_code"] = ad.get("HoldReasonCode")
                subcode = ad.get("HoldReasonSubCode", ad.get("HoldReasonSubcode"))
                if subcode is not None:
                    diagnostic["hold_reason_subcode"] = subcode
                if ad.get("RemoveReason"):
                    diagnostic["remove_reason"] = str(ad["RemoveReason"])
                for source, target in (
                    ("YallDAGRetry", "dag_retry"),
                    ("NumJobStarts", "num_job_starts"),
                    ("GlobalJobId", "global_job_id"),
                    ("RemoteHost", "remote_host"),
                    ("LastRemoteHost", "last_remote_host"),
                    ("JobStartDate", "job_start_date"),
                    ("JobCurrentStartDate", "job_current_start_date"),
                ):
                    if ad.get(source) is not None:
                        diagnostic[target] = ad.get(source)
                if state not in _TERMINAL:
                    active[job_id] = {"state": state, "task": task, **diagnostic}
                    if task is None:
                        snapshot.update(cluster_id=cluster, dagman=state)
                if task is not None:
                    item = {"state": state, "job_id": job_id, **diagnostic}
                    if task not in nodes or state not in _TERMINAL:
                        nodes[task] = item
        elif backend in {"slurm", "pbs"}:
            jobs = {str(job_id): task for _, record in records
                    for task, job_id in record.get("jobs", {}).items()}
            if backend == "slurm":
                # Query the user's queue, not purged IDs that can make squeue fail.
                raw = _checked(["squeue", "-h", "-u", getpass.getuser(), "-o", "%i|%T"])
                states = {}
                for line in raw.splitlines():
                    if not line.strip():
                        continue
                    parts = line.strip().split("|")
                    if len(parts) != 2:
                        raise ValueError("malformed squeue response")
                    states[parts[0].strip()] = parts[1].strip()
            else:
                # A full successful query distinguishes purged jobs from an
                # unavailable PBS server. Do not suppress qstat errors.
                raw = _checked(["qstat", "-f"])
                states = {}
                current = None
                headers = set()
                for line in raw.splitlines():
                    line = line.strip()
                    if line.startswith("Job Id:"):
                        current = line.split(":", 1)[1].strip()
                        headers.add(current)
                    elif current and re.match(r"job_state\s*=", line):
                        states[current] = line.split("=", 1)[1].strip()
                if (raw.strip() and not states) or headers != set(states):
                    raise ValueError("malformed or incomplete qstat response")
            for job_id, raw_state in states.items():
                task = jobs.get(job_id)
                if task is None:
                    continue
                if backend == "slurm":
                    state = ("completed" if raw_state == "COMPLETED" else
                             "failed" if raw_state in _SLURM_TERMINAL else
                             "idle" if raw_state == "PENDING" else
                             "running" if raw_state in {"RUNNING", "COMPLETING", "CONFIGURING"}
                             else "unknown")
                else:
                    state = ({"F": "completed", "C": "completed", "Q": "idle",
                              "W": "idle", "H": "held", "R": "running",
                              "E": "running", "B": "running"}.get(raw_state, "unknown"))
                item = {"state": state, "job_id": job_id, "scheduler_state": raw_state}
                if task not in nodes or state not in _TERMINAL:
                    nodes[task] = item
                if state not in _TERMINAL:
                    active[job_id] = {
                        "state": state, "task": task, "scheduler_state": raw_state
                    }
        else:
            raise ValueError(f"unsupported queued backend: {backend}")
        for item in active.values():
            if item["task"] is not None:
                state = item["state"]
                snapshot["counts"][state] = snapshot["counts"].get(state, 0) + 1
        snapshot["query_ok"] = True
    except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
        snapshot["error"] = str(exc)
        snapshot.update(nodes={}, counts={}, active_jobs={}, dagman=None)
    return snapshot



def condor_history_snapshot(campaign_dir: str | Path) -> dict[str, Any]:
    """Return normalized historical Condor ads for this campaign.

    This is diagnostic evidence only. It never changes reconciled task state and
    is intentionally separate from the live condor_q query used by recovery.
    """
    cdir, manifest, _ = _load(campaign_dir)
    result: dict[str, Any] = {"query_ok": False, "jobs": []}
    try:
        if manifest.get("backend") != "condor":
            raise ValueError("Condor history is only available for Condor campaigns")
        records = _records(cdir, "condor")
        clusters = {
            int(record["cluster_id"])
            for _, record in records
            if record.get("cluster_id") is not None
        }
        if not clusters:
            raise RuntimeError("no recorded DAGMan cluster ID")
        render = _read(cdir / "condor" / "render.json")
        names = {node: task for task, node in render["node_names"].items()}
        clauses = [f"(ClusterId == {i} || DAGManJobId == {i})" for i in sorted(clusters)]
        raw = _checked(["condor_history", "-json", "-constraint", " || ".join(clauses)])
        ads = [] if not raw.strip() else json.loads(raw)
        if not isinstance(ads, list):
            raise ValueError("condor_history did not return a JSON array")

        jobs: list[dict[str, Any]] = []
        for ad in ads:
            if ad.get("ClusterId") is None or ad.get("JobStatus") is None:
                continue
            cluster = int(ad["ClusterId"])
            proc = int(ad.get("ProcId", 0))
            item: dict[str, Any] = {
                "job_id": f"{cluster}.{proc}",
                "state": ("removed" if int(ad["JobStatus"]) == 3 else _CONDOR_STATES.get(int(ad["JobStatus"]), "unknown")),
                "task": names.get(ad.get("DAGNodeName")),
                "dagman_job_id": ad.get("DAGManJobId"),
            }
            for source, target in (
                ("YallDAGRetry", "dag_retry"),
                ("NumJobStarts", "num_job_starts"),
                ("GlobalJobId", "global_job_id"),
                ("JobStartDate", "job_start_date"),
                ("JobCurrentStartDate", "job_current_start_date"),
            ):
                if ad.get(source) is not None:
                    item[target] = ad.get(source)
            hold_reason = ad.get("HoldReason") or ad.get("LastHoldReason")
            if hold_reason:
                item["hold_reason"] = str(hold_reason)
            hold_code = ad.get("HoldReasonCode", ad.get("LastHoldReasonCode"))
            if hold_code is not None:
                item["hold_reason_code"] = hold_code
            subcode = ad.get(
                "HoldReasonSubCode",
                ad.get("HoldReasonSubcode", ad.get("LastHoldReasonSubCode")),
            )
            if subcode is not None:
                item["hold_reason_subcode"] = subcode
            if ad.get("RemoveReason"):
                item["remove_reason"] = str(ad["RemoveReason"])
            for source, target in (
                ("ExitCode", "exit_code"),
                ("ExitBySignal", "exit_by_signal"),
                ("ExitSignal", "exit_signal"),
                ("RemoteHost", "remote_host"),
                ("LastRemoteHost", "last_remote_host"),
                ("EnteredCurrentStatus", "entered_current_status"),
                ("CompletionDate", "completion_date"),
            ):
                if ad.get(source) is not None:
                    item[target] = ad.get(source)
            jobs.append(item)

        jobs.sort(
            key=lambda item: (
                int(item.get("entered_current_status") or item.get("completion_date") or 0),
                item["job_id"],
            )
        )
        result.update(query_ok=True, jobs=jobs)
    except (RuntimeError, ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
        result["error"] = str(exc)
    return result


def _effective(cdir: Path, name: str, state: dict[str, Any], snapshot: dict[str, Any]) -> str:
    recorded = state.get("state", "pending")
    node = snapshot.get("nodes", {}).get(name)
    if node and node["state"] not in _TERMINAL:
        return "queued" if node["state"] == "idle" else node["state"]
    # A terminal attempt can survive a failure to write the small state file.
    attempt = cdir / f"{name}_attempt_{int(state.get('attempts', 0)):03d}" / "attempt.json"
    if attempt.is_file():
        result = _read(attempt)
        if (result.get("state") in {"completed", "failed"}
                and result.get("finished_at") and result.get("returncode") is not None):
            if (result["state"] == "completed") != (result["returncode"] == 0):
                raise ValueError(f"inconsistent terminal attempt for {name}")
            return result["state"]
    if recorded == "running":
        return "interrupted" if snapshot.get("query_ok") else "unknown"
    return str(recorded)


def reconcile_status(campaign_dir: str | Path, data: dict[str, Any]) -> dict[str, Any]:
    """Overlay scheduler evidence without rewriting historical attempts."""
    cdir, _, _ = _load(campaign_dir)
    snapshot = scheduler_snapshot(cdir)
    data["scheduler"] = snapshot
    counts: dict[str, int] = {}
    for task in data["tasks"]:
        task["recorded_state"] = task["state"]
        task["state"] = _effective(cdir, task["name"], task, snapshot)
        counts[task["state"]] = counts.get(task["state"], 0) + 1
    data["counts"] = counts
    return data


@contextmanager
def _resume_lock(cdir: Path):
    lock = cdir / "state" / "resume.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise ValueError(f"resume already in progress or interrupted: {lock}; "
                         "inspect the lock and submission records before removing it") from exc
    try:
        _write(lock / "owner.json", {"pid": os.getpid(), "hostname": socket.gethostname(),
                                     "started_at": _now()})
        yield
    finally:
        (lock / "owner.json").unlink(missing_ok=True)
        lock.rmdir()


def _plan(cdir: Path, manifest: dict[str, Any], tasks: dict[str, Any],
          snapshot: dict[str, Any]) -> dict[str, Any]:
    if not snapshot.get("query_ok"):
        raise RuntimeError(f"resume refused: {snapshot.get('error', 'scheduler state unknown')}")
    states = {name: _state(cdir, manifest, name) for name in tasks}
    effective = {name: _effective(cdir, name, state, snapshot) for name, state in states.items()}
    completed = [name for name in tasks if effective[name] == "completed"]
    selected = [name for name in tasks if name not in completed]
    for name in completed:
        for ref in tasks[name].get("outputs", []):
            if not Path(ref["path"]).exists():
                raise ValueError(f"completed task {name} has a missing output: {ref['path']}; "
                                 "restore it or create a new campaign")
        if any(parent not in completed for parent in tasks[name].get("parents", [])):
            raise ValueError(f"inconsistent completed dependency chain at {name}")
    # Check external inputs, not files that an unfinished ancestor will produce.
    produced = {ref["path"] for name in selected for ref in tasks[name].get("outputs", [])}
    overwrite = bool(_read(cdir / "start.json").get("overwrite", False))
    for name in selected:
        for ref in tasks[name].get("inputs", []):
            if ref["path"] not in produced and not Path(ref["path"]).exists():
                raise ValueError(f"task {name}: input missing: {ref['path']}")
        if not (overwrite or tasks[name].get("overwrite", False)):
            for ref in tasks[name].get("outputs", []):
                path = Path(ref["path"])
                if path.exists() or path.is_symlink():
                    raise ValueError(f"unfinished task {name} has an existing output: {path}; "
                                     "inspect and move partial output aside before resume")
    plan = {"backend": manifest["backend"], "completed": completed, "selected": selected,
            "recorded_states": states, "reconciled_states": effective,
            "scheduler_before": snapshot}
    if manifest["backend"] == "condor" and selected:
        rescue, number = _latest_rescue(cdir)
        render = _read(cdir / "condor" / "render.json")
        done = {line.split()[1] for line in rescue.read_text().splitlines()
                if line.strip().startswith("DONE ")}
        expected = {render["node_names"][name] for name in completed}
        if done != expected:
            raise ValueError("Rescue DAG DONE nodes disagree with verified Yall completions; "
                             "inspect the rescue file and attempt records before restarting")
        plan.update(rescue_source=str(rescue), rescue_number=number,
                    rescue_sha256=hashlib.sha256(rescue.read_bytes()).hexdigest())
    return plan


def _latest_rescue(cdir: Path) -> tuple[Path, int]:
    records = _records(cdir, "condor")
    directory = next((path.parent for path, record in reversed(records)
                      if record.get("cluster_id") is not None), cdir / "condor")
    rescues = []
    for path in directory.glob("campaign.dag.rescue*"):
        match = re.fullmatch(r"campaign\.dag\.rescue(\d+)", path.name)
        if match:
            rescues.append((int(match[1]), path))
    if not rescues:
        raise ValueError(f"no Rescue DAG in {directory}; let DAGMan finish writing it "
                         "before resuming")
    number, path = max(rescues)
    return path, number


def _stage(cdir: Path, directory: Path, plan: dict[str, Any]) -> dict[str, Any]:
    backend = plan["backend"]
    source = cdir / backend
    render = _read(source / "render.json")
    logs = directory / "logs"
    logs.mkdir(parents=True)
    if backend == "condor":
        rescue, number = Path(plan["rescue_source"]), plan["rescue_number"]
        if hashlib.sha256(rescue.read_bytes()).hexdigest() != plan["rescue_sha256"]:
            raise ValueError("Rescue DAG changed during recovery; retry after DAGMan has stopped")
        shutil.copy2(source / "campaign.dag", directory / "campaign.dag")
        shutil.copy2(rescue, directory / rescue.name)
        for file in source.glob("*.sub"):
            if file.name == "campaign.dag.condor.sub":
                continue
            text = file.read_text()
            for key, target in {"output": logs / (file.stem + ".out"),
                                "error": logs / (file.stem + ".err"),
                                "log": directory / "events.log"}.items():
                text = re.sub(rf"(?im)^{key}\s*=.*$", f"{key} = {target}", text)
            (directory / file.name).write_text(text)
        return {"rescue_source": str(rescue), "rescue_number": number,
                "rescue_sha256": hashlib.sha256(rescue.read_bytes()).hexdigest()}
    scripts = {}
    for name in plan["selected"]:
        file = source / render["scripts"][name]
        text = file.read_text()
        if backend == "slurm":
            for option, suffix in (("output", "out"), ("error", "err")):
                text = re.sub(rf"(?m)^#SBATCH --{option}=.*$",
                              f"#SBATCH --{option}={shlex.quote(str(logs / (file.stem + '.' + suffix)))}", text)
        else:
            for option, suffix in (("o", "out"), ("e", "err")):
                text = re.sub(rf"(?m)^#PBS -{option} .*$",
                              f"#PBS -{option} {shlex.quote(str(logs / (file.stem + '.' + suffix)))}", text)
        target = directory / file.name
        target.write_text(text)
        target.chmod(file.stat().st_mode & 0o777)
        scripts[name] = file.name
    return {"scripts": scripts}


def _submit(directory: Path, tasks: dict[str, Any], plan: dict[str, Any],
            staged: dict[str, Any]) -> dict[str, Any]:
    backend = plan["backend"]
    path = directory / "submit.json"
    record: dict[str, Any] = {"backend": backend, "jobs": {}, "commands": [],
                              "returncode": None, "in_flight": None}
    _write(path, record)

    def submit(argv: list[str]) -> str:
        record["in_flight"] = argv
        record["commands"].append(argv)
        _write(path, record)  # Durable intent before contacting the scheduler.
        try:
            result = _run(argv, directory)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"unconfirmed scheduler submission; inspect {path}: {exc}") from exc
        record["last_stdout"] = result.stdout
        record["last_stderr"] = result.stderr
        if result.returncode:
            record.update(in_flight=None, returncode=result.returncode)
            _write(path, record)
            raise RuntimeError(f"{argv[0]} failed: {result.stderr.strip() or result.stdout.strip()}; "
                               f"known jobs are recorded in {path}")
        # Keep in_flight until a parseable job ID has been written to disk.
        return result.stdout

    if backend == "condor":
        output = submit(["condor_submit_dag", "-dorescuefrom", str(staged["rescue_number"]),
                         "campaign.dag"])
        match = re.search(r"cluster\s+(\d+)", output, re.I)
        if not match:
            raise RuntimeError(f"unconfirmed DAGMan submission; inspect {path}")
        record.update(cluster_id=int(match[1]), returncode=0, in_flight=None)
        _write(path, record)
        return record

    remaining = set(plan["selected"])
    while remaining:
        progressed = False
        for name in plan["selected"]:
            if name not in remaining:
                continue
            parents = [p for p in tasks[name].get("parents", []) if p in plan["selected"]]
            if not all(p in record["jobs"] for p in parents):
                continue
            parent_ids = ":".join(record["jobs"][p] for p in parents)
            if backend == "slurm":
                argv = ["sbatch", "--parsable", "--hold"]
                if parents:
                    argv.append(f"--dependency=afterok:{parent_ids}")
            else:
                argv = ["qsub", "-h"]
                if parents:
                    argv += ["-W", f"depend=afterok:{parent_ids}"]
            output = submit([*argv, staged["scripts"][name]])
            first = output.strip().splitlines()[0] if output.strip() else ""
            pattern = r"\d+" if backend == "slurm" else r"\d+(?:\.[A-Za-z0-9_.-]+)?"
            if not re.fullmatch(pattern, first):
                raise RuntimeError(f"unconfirmed {backend} job ID {first!r}; inspect {path}; "
                                   "federated/multi-server submissions are not supported by resume")
            record["jobs"][name] = first
            record["in_flight"] = None
            _write(path, record)
            remaining.remove(name)
            progressed = True
        if not progressed:
            raise ValueError("recovery dependency graph made no progress")
    ids = list(record["jobs"].values())
    release = (["scontrol", "release", ",".join(ids)] if backend == "slurm"
               else ["qrls", *ids])
    record["release_command"] = release
    _write(path, record)
    try:
        result = _run(release, directory)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"release response unavailable; inspect recorded jobs in {path}: {exc}") from exc
    record.update(returncode=result.returncode, release_stdout=result.stdout,
                  release_stderr=result.stderr)
    _write(path, record)
    if result.returncode:
        raise RuntimeError(f"{release[0]} failed; do not submit duplicates: inspect {path}")
    return record


def _amendable_source_changes(cdir: Path) -> list[str]:
    try:
        from .amend import amend_campaign
        proposal = amend_campaign(cdir, dry_run=True)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        return []
    return [str(change["task"]) for change in proposal.get("changes", [])]


def _raise_if_source_needs_amendment(cdir: Path) -> None:
    changed = _amendable_source_changes(cdir)
    if not changed:
        return
    names = ", ".join(changed)
    raise ValueError(
        "source Yallfile has amendable command changes for unfinished task(s): "
        + names
        + "; run yall-run amend "
        + shlex.quote(str(cdir))
        + " before resume"
    )


def resume_campaign(campaign_dir: str | Path, *, dry_run: bool = False,
                    cancel_pending: bool = False, reason: str | None = None) -> int:
    cdir, manifest, tasks = _load(campaign_dir)
    backend = manifest.get("backend", "local")
    if backend == "local":
        if dry_run or cancel_pending:
            raise ValueError("--dry-run and --cancel-pending are queued-backend options")
        _raise_if_source_needs_amendment(cdir)
        from .campaign import resume_local
        resume_local(cdir, reason=reason)
        return 0
    if backend not in QUEUED_BACKENDS:
        raise ValueError(f"unsupported backend: {backend}")
    if not (cdir / "start.json").is_file():
        raise ValueError("campaign has not been started; use yall-run start")

    with _resume_lock(cdir):
        snapshot = scheduler_snapshot(cdir)
        if not snapshot["query_ok"]:
            raise RuntimeError(f"resume refused: {snapshot.get('error')}")
        active = snapshot["active_jobs"]
        if active:
            cancellable = backend != "condor" and all(
                job["state"] in {"idle", "held"} for job in active.values())
            if not cancellable or not cancel_pending:
                raise ValueError("campaign still has active scheduler jobs; no replacements submitted. "
                                 "Let running jobs finish. For Slurm/PBS pending or held descendants "
                                 "only, use --cancel-pending to rebuild their dependencies.")
        for job in active.values():
            if job["task"] and _state(cdir, manifest, job["task"]).get("state") == "completed":
                raise ValueError("a completed task has an active job; inspect the conflicting state")
        _raise_if_source_needs_amendment(cdir)
        plan = _plan(cdir, manifest, tasks, snapshot)
        print(f"[{backend}] resume: keep {len(plan['completed'])} completed; "
              f"retry/continue {len(plan['selected'])} tasks", flush=True)
        if not plan["selected"]:
            return 0
        if dry_run:
            if active:
                print(f"[{backend}] would cancel {len(active)} pending/held jobs", flush=True)
            print("[resume] dry run: nothing submitted or cancelled", flush=True)
            return 0
        root = cdir / "resumes"
        root.mkdir(exist_ok=True)
        numbers = [int(p.name) for p in root.iterdir() if p.is_dir() and p.name.isdigit()]
        round_dir = root / f"{max(numbers, default=0) + 1:04d}"
        directory = round_dir / backend
        directory.mkdir(parents=True)
        record = {
            **plan,
            "started_at": _now(),
            "status": "preparing",
            "reason": reason,
        }
        record_path = round_dir / "resume.json"
        _write(record_path, record)
        try:
            staged = _stage(cdir, directory, plan)
            record.update(staged)
            _write(record_path, record)
            if active:
                fresh = scheduler_snapshot(cdir)
                if (not fresh["query_ok"] or fresh["active_jobs"] != active):
                    raise RuntimeError("scheduler state changed before cancellation; retry resume")
                cancel = (["scancel", "--state=PENDING", *active] if backend == "slurm"
                          else ["qdel", *active])
                record["cancel_command"] = cancel
                _write(record_path, record)
                _checked(cancel)
                after = scheduler_snapshot(cdir)
                if not after["query_ok"] or after["active_jobs"]:
                    raise RuntimeError("cancellation not yet confirmed; nothing resubmitted. "
                                       "Retry resume after the old jobs have left the queue")
                plan = _plan(cdir, manifest, tasks, after)
                record["scheduler_after_cancel"] = after
                record["reconciled_states"] = plan["reconciled_states"]
            else:
                fresh = scheduler_snapshot(cdir)
                if not fresh["query_ok"] or fresh["active_jobs"]:
                    raise RuntimeError("scheduler state changed before submission; nothing resubmitted")
            # Reconcile mutable state only; leave all old attempt evidence intact.
            for name, value in plan["reconciled_states"].items():
                old = plan["recorded_states"][name]
                if value in {"interrupted", "completed", "failed"} and value != old.get("state"):
                    path = (cdir / "state" / f"{name}.json" if isinstance(manifest["tasks"], dict)
                            else cdir / "tasks" / f"{name}.json")
                    _write(path, {**old, "state": value, "reconciled_at": _now(),
                                  "recovery_record": str(record_path)})
            record["status"] = "submitting"
            _write(record_path, record)
            result = _submit(directory, tasks, plan, staged)
            record.update(status="submitted", submission=result)
            print(f"[{backend}] resumed {cdir.name}; record: {record_path}", flush=True)
        except Exception as exc:
            record.update(status="failed", error=str(exc))
            raise
        finally:
            record["finished_at"] = _now()
            _write(record_path, record)
    return 0
