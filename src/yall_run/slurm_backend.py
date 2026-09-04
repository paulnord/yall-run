from __future__ import annotations

import json
from pathlib import Path
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
from .campaign import (
    begin_campaign,
    cancel_prepared_start,
    create_campaign,
    prepare_campaign_start,
)
from .model import CampaignSpec

_SLURM_STATES = {
    "PENDING": "idle",
    "CONFIGURING": "idle",
    "RUNNING": "running",
    "COMPLETING": "running",
    "SUSPENDED": "suspended",
    "STOPPED": "suspended",
    "COMPLETED": "completed",
    "FAILED": "failed",
    "CANCELLED": "failed",
    "TIMEOUT": "failed",
    "NODE_FAIL": "failed",
    "OUT_OF_MEMORY": "failed",
    "PREEMPTED": "failed",
}


def render_slurm(spec: CampaignSpec, root: str | Path) -> Path:
    campaign_dir = create_campaign(spec, root, backend="slurm")
    slurm_dir = campaign_dir / "slurm"
    slurm_dir.mkdir()
    logs_dir = slurm_dir / "logs"
    logs_dir.mkdir()

    worker = bundle_worker(campaign_dir, "slurm")
    wrapper_record = archive_wrapper(spec, campaign_dir, "slurm")
    archived_wrapper = Path(wrapper_record["path"]) if wrapper_record else None

    scripts: dict[str, str] = {}
    for index, task in enumerate(spec.tasks):
        node = f"yall_{index:04d}_{slug(task.name)}"
        script = slurm_dir / f"{node}.sh"
        cpus = task.resources.cpus or spec.condor.request_cpus
        memory = normalize_memory(
            task.resources.memory or spec.condor.request_memory,
            "slurm",
        )
        disk = task.resources.disk or spec.condor.request_disk
        command = worker_command(worker, campaign_dir, task.name, archived_wrapper)
        script.write_text(
            "#!/bin/bash\n"
            f"#SBATCH --job-name={node}\n"
            f"#SBATCH --cpus-per-task={cpus}\n"
            f"#SBATCH --mem={memory}\n"
            f"#SBATCH --output={logs_dir / (node + '.out')}\n"
            f"#SBATCH --error={logs_dir / (node + '.err')}\n"
            f"# yall requested disk={disk}; no portable Slurm disk request is emitted\n"
            "set -u\n"
            + retry_shell(command, task.retries)
        )
        script.chmod(0o755)
        scripts[task.name] = script.name

    write_json(slurm_dir / "render.json", {
        "backend": "slurm",
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
            ["scancel", *job_ids],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _parse_job_id(output: str) -> str:
    first = output.strip().splitlines()[0] if output.strip() else ""
    job_id = first.split(";", 1)[0].strip()
    if not job_id.isdigit():
        raise RuntimeError(f"could not parse Slurm job id from sbatch output: {output!r}")
    return job_id


def submit_slurm(campaign_dir: str | Path, *, overwrite: bool = False) -> Path:
    campaign_dir, manifest = load_backend_campaign(campaign_dir, "slurm")
    slurm_dir = campaign_dir / "slurm"
    render_path = slurm_dir / "render.json"
    if not render_path.is_file():
        raise ValueError(f"rendered Slurm campaign not found: {render_path}")
    render = read_json(render_path)
    submit_path = slurm_dir / "submit.json"
    if submit_path.exists():
        previous = read_json(submit_path)
        if previous.get("returncode") == 0:
            raise ValueError(f"campaign has already been submitted: {campaign_dir}")
    if (campaign_dir / "start.json").exists():
        raise ValueError(f"campaign has already been started: {campaign_dir}")

    prepare_campaign_start(campaign_dir, overwrite=overwrite)

    scripts = render.get("scripts", {})
    task_names = campaign_task_names(manifest)
    remaining = set(task_names)
    job_ids: dict[str, str] = {}
    commands: list[list[str]] = []
    print(f"[slurm] submitting {len(remaining)} held jobs", flush=True)

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
                    raise ValueError(f"missing rendered Slurm script for task {name}")
                command = ["sbatch", "--parsable", "--hold"]
                if parents:
                    parent_ids = ":".join(job_ids[parent] for parent in parents)
                    command.append(f"--dependency=afterok:{parent_ids}")
                command.append(script_name)
                proc = subprocess.run(
                    command,
                    cwd=slurm_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                commands.append(command)
                if proc.returncode != 0:
                    raise RuntimeError(
                        proc.stderr.strip() or proc.stdout.strip() or "sbatch failed"
                    )
                job_id = _parse_job_id(proc.stdout)
                job_ids[name] = job_id
                remaining.remove(name)
                progressed = True
                print(f"[slurm] {name} job={job_id} held", flush=True)
            if not progressed:
                raise RuntimeError("Slurm submission graph made no progress")
    except Exception:
        _cancel_jobs(list(job_ids.values()))
        cancel_prepared_start(campaign_dir)
        write_json(submit_path, {
            "backend": "slurm",
            "returncode": 1,
            "commands": commands,
            "jobs": job_ids,
            "overwrite": bool(overwrite),
            "cancelled_after_submission_failure": True,
        })
        raise

    write_json(submit_path, {
        "backend": "slurm",
        "returncode": None,
        "commands": commands,
        "jobs": job_ids,
        "held": True,
        "overwrite": bool(overwrite),
    })

    try:
        begin_campaign(campaign_dir, overwrite=overwrite)
    except Exception:
        _cancel_jobs(list(job_ids.values()))
        cancel_prepared_start(campaign_dir)
        raise

    release_command = ["scontrol", "release", *job_ids.values()]
    release = subprocess.run(
        release_command,
        cwd=slurm_dir,
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
            release.stderr.strip() or release.stdout.strip() or "scontrol release failed"
        )

    print(f"[slurm] released {len(job_ids)} jobs", flush=True)
    return campaign_dir


def slurm_queue_status(campaign_dir: str | Path) -> dict[str, Any] | None:
    campaign_dir, _ = load_backend_campaign(campaign_dir, "slurm")
    submit_path = campaign_dir / "slurm" / "submit.json"
    if not submit_path.exists():
        return None
    record = read_json(submit_path)
    jobs = record.get("jobs", {})
    if not jobs:
        return None

    reverse = {str(job_id): task for task, job_id in jobs.items()}
    command = [
        "squeue",
        "-h",
        "-j",
        ",".join(reverse),
        "-o",
        "%i|%T",
    ]
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
    if proc.returncode != 0:
        return None

    nodes: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    for line in proc.stdout.splitlines():
        if "|" not in line:
            continue
        job_id, raw_state = (item.strip() for item in line.split("|", 1))
        task = reverse.get(job_id)
        if task is None:
            continue
        state = _SLURM_STATES.get(raw_state.upper(), raw_state.lower() or "unknown")
        nodes[task] = {
            "state": state,
            "scheduler_state": raw_state,
            "job_id": job_id,
        }
        counts[state] = counts.get(state, 0) + 1

    return {
        "backend": "slurm",
        "counts": counts,
        "nodes": nodes,
        "jobs": jobs,
    }
