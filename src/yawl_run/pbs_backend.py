from __future__ import annotations

from pathlib import Path
import re
import subprocess
from typing import Any

from .batch_common import (
    archive_wrapper,
    bundle_worker,
    campaign_task_definition,
    campaign_task_names,
    load_backend_campaign,
    normalize_memory,
    read_json,
    retry_shell,
    slug,
    worker_command,
    write_json,
)
from .campaign import begin_campaign, create_campaign
from .model import CampaignSpec

_PBS_STATES = {
    "Q": "idle",
    "W": "idle",
    "T": "idle",
    "R": "running",
    "B": "running",
    "E": "running",
    "H": "held",
    "S": "suspended",
    "U": "suspended",
    "C": "completed",
    "F": "completed",
}


def _pbs_job_name(index: int, task_name: str) -> str:
    # Conservative length for older PBS installations while remaining readable.
    return f"y{index:04d}_{slug(task_name)[:8]}"[:15]


def render_pbs(spec: CampaignSpec, root: str | Path) -> Path:
    campaign_dir = create_campaign(spec, root, backend="pbs")
    pbs_dir = campaign_dir / "pbs"
    pbs_dir.mkdir()
    logs_dir = pbs_dir / "logs"
    logs_dir.mkdir()

    worker = bundle_worker(campaign_dir, "pbs")
    wrapper_record = archive_wrapper(spec, campaign_dir, "pbs")
    archived_wrapper = Path(wrapper_record["path"]) if wrapper_record else None

    scripts: dict[str, str] = {}
    for index, task in enumerate(spec.tasks):
        node = f"yawl_{index:04d}_{slug(task.name)}"
        job_name = _pbs_job_name(index, task.name)
        script = pbs_dir / f"{node}.sh"
        cpus = task.resources.cpus or spec.condor.request_cpus
        memory = normalize_memory(
            task.resources.memory or spec.condor.request_memory,
            "pbs",
        )
        disk = task.resources.disk or spec.condor.request_disk
        command = worker_command(worker, campaign_dir, task.name, archived_wrapper)
        getenv_line = "#PBS -V\n" if spec.condor.getenv else ""
        script.write_text(
            "#!/bin/bash\n"
            f"#PBS -N {job_name}\n"
            f"#PBS -l select=1:ncpus={cpus}:mem={memory}\n"
            f"#PBS -o {logs_dir / (node + '.out')}\n"
            f"#PBS -e {logs_dir / (node + '.err')}\n"
            + getenv_line
            + f"# yawl requested disk={disk}; no portable PBS disk request is emitted\n"
            "set -u\n"
            + retry_shell(command, task.retries)
        )
        script.chmod(0o755)
        scripts[task.name] = script.name

    write_json(pbs_dir / "render.json", {
        "backend": "pbs",
        "scripts": scripts,
        "resources": {
            "cpus": spec.condor.request_cpus,
            "memory": spec.condor.request_memory,
            "disk": spec.condor.request_disk,
        },
        "wrapper": wrapper_record,
        "experimental": True,
    })
    return campaign_dir


def _cancel_jobs(job_ids: list[str]) -> None:
    if not job_ids:
        return
    try:
        subprocess.run(
            ["qdel", *job_ids],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _parse_job_id(output: str) -> str:
    first = output.strip().splitlines()[0] if output.strip() else ""
    job_id = first.split()[0] if first else ""
    if not re.fullmatch(r"\d+(?:\.[A-Za-z0-9_.-]+)?", job_id):
        raise RuntimeError(f"could not parse PBS job id from qsub output: {output!r}")
    return job_id


def submit_pbs(campaign_dir: str | Path) -> Path:
    campaign_dir, manifest = load_backend_campaign(campaign_dir, "pbs")
    pbs_dir = campaign_dir / "pbs"
    render_path = pbs_dir / "render.json"
    if not render_path.is_file():
        raise ValueError(f"rendered PBS campaign not found: {render_path}")
    render = read_json(render_path)
    submit_path = pbs_dir / "submit.json"
    if submit_path.exists():
        previous = read_json(submit_path)
        if previous.get("returncode") == 0:
            raise ValueError(f"campaign has already been submitted: {campaign_dir}")
    if (campaign_dir / "start.json").exists():
        raise ValueError(f"campaign has already been started: {campaign_dir}")

    scripts = render.get("scripts", {})
    task_names = campaign_task_names(manifest)
    remaining = set(task_names)
    job_ids: dict[str, str] = {}
    commands: list[list[str]] = []
    print(f"[pbs] submitting {len(remaining)} held jobs", flush=True)

    try:
        while remaining:
            progressed = False
            for name in task_names:
                if name not in remaining:
                    continue
                task = campaign_task_definition(campaign_dir, manifest, name)
                parents = list(task.get("parents", []))
                if not all(parent in job_ids for parent in parents):
                    continue
                script_name = scripts.get(name)
                if not script_name:
                    raise ValueError(f"missing rendered PBS script for task {name}")
                command = ["qsub", "-h"]
                if parents:
                    parent_ids = ":".join(job_ids[parent] for parent in parents)
                    command.extend(["-W", f"depend=afterok:{parent_ids}"])
                command.append(script_name)
                proc = subprocess.run(
                    command,
                    cwd=pbs_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                commands.append(command)
                if proc.returncode != 0:
                    raise RuntimeError(
                        proc.stderr.strip() or proc.stdout.strip() or "qsub failed"
                    )
                job_id = _parse_job_id(proc.stdout)
                job_ids[name] = job_id
                remaining.remove(name)
                progressed = True
                print(f"[pbs] {name} job={job_id} held", flush=True)
            if not progressed:
                raise RuntimeError("PBS submission graph made no progress")
    except Exception:
        _cancel_jobs(list(job_ids.values()))
        write_json(submit_path, {
            "backend": "pbs",
            "returncode": 1,
            "commands": commands,
            "jobs": job_ids,
            "cancelled_after_submission_failure": True,
        })
        raise

    write_json(submit_path, {
        "backend": "pbs",
        "returncode": None,
        "commands": commands,
        "jobs": job_ids,
        "held": True,
    })

    try:
        begin_campaign(campaign_dir)
    except Exception:
        _cancel_jobs(list(job_ids.values()))
        raise

    release_command = ["qrls", *job_ids.values()]
    release = subprocess.run(
        release_command,
        cwd=pbs_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    record = read_json(submit_path)
    record["release_command"] = release_command
    record["release_returncode"] = release.returncode
    record["release_stdout"] = release.stdout
    record["release_stderr"] = release.stderr
    record["held"] = release.returncode != 0
    record["returncode"] = release.returncode
    write_json(submit_path, record)
    if release.returncode != 0:
        raise RuntimeError(
            release.stderr.strip() or release.stdout.strip() or "qrls failed"
        )

    print(f"[pbs] released {len(job_ids)} jobs", flush=True)
    return campaign_dir


def _parse_qstat_full(output: str) -> dict[str, str]:
    states: dict[str, str] = {}
    current: str | None = None
    for raw in output.splitlines():
        line = raw.strip()
        if line.startswith("Job Id:"):
            current = line.split(":", 1)[1].strip()
            continue
        if current is not None and line.startswith("job_state") and "=" in line:
            states[current] = line.split("=", 1)[1].strip()
    return states


def pbs_queue_status(campaign_dir: str | Path) -> dict[str, Any] | None:
    campaign_dir, _ = load_backend_campaign(campaign_dir, "pbs")
    submit_path = campaign_dir / "pbs" / "submit.json"
    if not submit_path.exists():
        return None
    record = read_json(submit_path)
    jobs = record.get("jobs", {})
    if not jobs:
        return None

    reverse = {str(job_id): task for task, job_id in jobs.items()}
    command = ["qstat", "-f", *reverse]
    try:
        proc = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    # Some PBS installations return nonzero when one requested historical job has
    # already been purged while still printing the remaining active jobs.
    states = _parse_qstat_full(proc.stdout)
    if proc.returncode != 0 and not states:
        return None

    nodes: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    for job_id, raw_state in states.items():
        task = reverse.get(job_id)
        if task is None:
            continue
        state = _PBS_STATES.get(raw_state.upper(), raw_state.lower() or "unknown")
        nodes[task] = {
            "state": state,
            "scheduler_state": raw_state,
            "job_id": job_id,
        }
        counts[state] = counts.get(state, 0) + 1

    return {
        "backend": "pbs",
        "counts": counts,
        "nodes": nodes,
        "jobs": jobs,
    }
