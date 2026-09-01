from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
from typing import Any

from .campaign import create_campaign
from .model import CampaignSpec


_CLUSTER_RE = re.compile(r"cluster\s+(\d+)", re.IGNORECASE)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _slug(name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-.")
    return value or "task"


def render_condor(spec: CampaignSpec, root: str | Path) -> Path:
    campaign_dir = create_campaign(spec, root, backend="condor")
    condor_dir = campaign_dir / "condor"
    condor_dir.mkdir()
    logs_dir = condor_dir / "logs"
    logs_dir.mkdir()

    # The source tree is normally on a shared filesystem. Putting it first in
    # PYTHONPATH makes the worker independent of the user's interactive PATH.
    source_root = Path(__file__).resolve().parents[1]
    python = Path(sys.executable).resolve()
    node_names: dict[str, str] = {}

    dag_lines: list[str] = []
    for index, task in enumerate(spec.tasks):
        node = f"yawl_{index:04d}_{_slug(task.name)}"
        node_names[task.name] = node

        wrapper = condor_dir / f"{node}.sh"
        wrapper.write_text(
            "#!/bin/bash\n"
            "set -e\n"
            f"export PYTHONPATH={shlex.quote(str(source_root))}:${{PYTHONPATH:-}}\n"
            f"exec {shlex.quote(str(python))} -m yawl_run.cli worker "
            f"{shlex.quote(str(campaign_dir))} {shlex.quote(task.name)}\n"
        )
        wrapper.chmod(0o755)

        submit = condor_dir / f"{node}.sub"
        submit.write_text(
            "universe = vanilla\n"
            f"executable = {wrapper}\n"
            f"output = {logs_dir / (node + '.out')}\n"
            f"error = {logs_dir / (node + '.err')}\n"
            f"log = {condor_dir / 'events.log'}\n"
            f"request_cpus = {spec.condor.request_cpus}\n"
            f"request_memory = {spec.condor.request_memory}\n"
            f"request_disk = {spec.condor.request_disk}\n"
            f"getenv = {'True' if spec.condor.getenv else 'False'}\n"
            "should_transfer_files = NO\n"
            "queue 1\n"
        )
        dag_lines.append(f"JOB {node} {submit.name}")
        if task.retries:
            dag_lines.append(f"RETRY {node} {task.retries}")

    for task in spec.tasks:
        if task.parents:
            parents = " ".join(node_names[name] for name in task.parents)
            dag_lines.append(f"PARENT {parents} CHILD {node_names[task.name]}")

    dag_path = condor_dir / "campaign.dag"
    dag_path.write_text("\n".join(dag_lines) + "\n")
    _write_json(condor_dir / "render.json", {
        "backend": "condor",
        "dag": str(dag_path),
        "node_names": node_names,
        "condor": asdict(spec.condor),
    })
    return campaign_dir


def submit_condor(spec: CampaignSpec, root: str | Path, submit: bool = True) -> Path:
    campaign_dir = render_condor(spec, root)
    if not submit:
        return campaign_dir

    condor_dir = campaign_dir / "condor"
    dag_path = condor_dir / "campaign.dag"
    proc = subprocess.run(
        ["condor_submit_dag", dag_path.name],
        cwd=condor_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    record: dict[str, Any] = {
        "command": ["condor_submit_dag", dag_path.name],
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }
    match = _CLUSTER_RE.search(proc.stdout)
    if match:
        record["cluster_id"] = int(match.group(1))
    _write_json(condor_dir / "submit.json", record)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "condor_submit_dag failed")
    return campaign_dir


def condor_queue_status(campaign_dir: str | Path) -> dict[str, int] | None:
    campaign_dir = Path(campaign_dir).resolve()
    submit_path = campaign_dir / "condor" / "submit.json"
    if not submit_path.exists():
        return None
    record = json.loads(submit_path.read_text())
    cluster_id = record.get("cluster_id")
    if cluster_id is None:
        return None
    try:
        proc = subprocess.run(
            ["condor_q", str(cluster_id), "-af", "JobStatus"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None

    names = {
        "1": "idle",
        "2": "running",
        "3": "removed",
        "4": "completed",
        "5": "held",
        "6": "transferring",
        "7": "suspended",
    }
    counts: dict[str, int] = {}
    for line in proc.stdout.splitlines():
        key = names.get(line.strip(), "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts
