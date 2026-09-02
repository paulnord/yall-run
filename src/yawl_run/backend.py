from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
from typing import Any

from .campaign import begin_campaign, create_campaign
from .model import CampaignSpec
from .paths import logical_absolute

_CLUSTER_RE = re.compile(r"cluster\s+(\d+)", re.IGNORECASE)
_STATUS_NAMES = {
    "1": "idle",
    "2": "running",
    "3": "removed",
    "4": "completed",
    "5": "held",
    "6": "transferring",
    "7": "suspended",
}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _slug(name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-.")
    return value or "task"


def _archive_wrapper(spec: CampaignSpec, campaign_dir: Path) -> dict[str, Any] | None:
    if not spec.condor.wrapper:
        return None
    source = logical_absolute(spec.condor.wrapper, spec.source.parent)
    if not source.is_file():
        raise ValueError(f"Condor wrapper does not exist: {source}")
    environment_dir = campaign_dir / "environment"
    environment_dir.mkdir(exist_ok=True)
    suffix = "".join(source.suffixes)
    archived = environment_dir / f"condor-wrapper{suffix}"
    shutil.copy2(source, archived)
    archived.chmod(archived.stat().st_mode | 0o100)
    digest = hashlib.sha256(archived.read_bytes()).hexdigest()
    return {
        "source": str(source),
        "path": str(archived),
        "sha256": digest,
        "size_bytes": archived.stat().st_size,
    }


def render_condor(spec: CampaignSpec, root: str | Path) -> Path:
    campaign_dir = create_campaign(spec, root, backend="condor")
    condor_dir = campaign_dir / "condor"
    condor_dir.mkdir()
    logs_dir = condor_dir / "logs"
    logs_dir.mkdir()

    worker_source = Path(__file__).with_name("worker.py").read_text()
    worker = condor_dir / "yawl_worker.py"
    worker.write_text(worker_source)
    worker.chmod(0o755)

    wrapper_record = _archive_wrapper(spec, campaign_dir)
    archived_wrapper = Path(wrapper_record["path"]) if wrapper_record else None

    node_names: dict[str, str] = {}
    dag_lines: list[str] = []
    for index, task in enumerate(spec.tasks):
        node = f"yawl_{index:04d}_{_slug(task.name)}"
        node_names[task.name] = node

        node_script = condor_dir / f"{node}.sh"
        worker_command = (
            f"/usr/bin/env python3 {shlex.quote(str(worker))} "
            f"{shlex.quote(str(campaign_dir))} {shlex.quote(task.name)}"
        )
        if archived_wrapper is not None:
            worker_command = f"{shlex.quote(str(archived_wrapper))} {worker_command}"
        node_script.write_text(
            "#!/bin/bash\n"
            "set -e\n"
            f"exec {worker_command}\n"
        )
        node_script.chmod(0o755)

        request_cpus = task.resources.cpus or spec.condor.request_cpus
        request_memory = task.resources.memory or spec.condor.request_memory
        request_disk = task.resources.disk or spec.condor.request_disk

        submit = condor_dir / f"{node}.sub"
        submit.write_text(
            "universe = vanilla\n"
            f"executable = {node_script}\n"
            f"output = {logs_dir / (node + '.out')}\n"
            f"error = {logs_dir / (node + '.err')}\n"
            f"log = {condor_dir / 'events.log'}\n"
            f"request_cpus = {request_cpus}\n"
            f"request_memory = {request_memory}\n"
            f"request_disk = {request_disk}\n"
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
        "wrapper": wrapper_record,
    })
    return campaign_dir


def submit_rendered(campaign_dir: str | Path) -> Path:
    campaign_dir = logical_absolute(campaign_dir)
    manifest_path = campaign_dir / "campaign.json"
    if not manifest_path.is_file():
        raise ValueError(f"not a yawl campaign: {campaign_dir}")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("backend") != "condor":
        raise ValueError(f"campaign backend is not condor: {campaign_dir}")

    condor_dir = campaign_dir / "condor"
    dag_path = condor_dir / "campaign.dag"
    if not dag_path.is_file():
        raise ValueError(f"rendered DAG not found: {dag_path}")
    submit_path = condor_dir / "submit.json"
    if submit_path.exists():
        previous = json.loads(submit_path.read_text())
        if previous.get("returncode") == 0:
            cluster = previous.get("cluster_id")
            suffix = f" (cluster {cluster})" if cluster is not None else ""
            raise ValueError(f"campaign has already been submitted{suffix}: {campaign_dir}")

    if (campaign_dir / "start.json").exists():
        raise ValueError(f"campaign has already been started: {campaign_dir}")

    command = ["condor_submit_dag", dag_path.name]
    print(f"[condor] submitting {dag_path}", flush=True)
    proc = subprocess.Popen(
        command,
        cwd=condor_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    lines: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        lines.append(line)
        print(line, end="", flush=True)
    returncode = proc.wait()
    output = "".join(lines)

    record: dict[str, Any] = {
        "command": command,
        "returncode": returncode,
        "stdout": output,
        "stderr": "",
    }
    match = _CLUSTER_RE.search(output)
    if match:
        record["cluster_id"] = int(match.group(1))
    _write_json(submit_path, record)
    if returncode != 0:
        raise RuntimeError(output.strip() or "condor_submit_dag failed")

    begin_campaign(campaign_dir)
    cluster = record.get("cluster_id")
    suffix = f" cluster={cluster}" if cluster is not None else ""
    print(f"[condor] submitted{suffix}", flush=True)
    return campaign_dir


def _condor_q(arguments: list[str]) -> subprocess.CompletedProcess[str] | None:
    try:
        proc = subprocess.run(
            ["condor_q", *arguments],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc if proc.returncode == 0 else None


def condor_queue_status(campaign_dir: str | Path) -> dict[str, Any] | None:
    campaign_dir = logical_absolute(campaign_dir)
    condor_dir = campaign_dir / "condor"
    submit_path = condor_dir / "submit.json"
    if not submit_path.exists():
        return None
    record = json.loads(submit_path.read_text())
    cluster_id = record.get("cluster_id")
    if cluster_id is None:
        return None
    cluster_id = int(cluster_id)

    dagman_state: str | None = None
    dagman = _condor_q([str(cluster_id), "-af", "JobStatus"])
    if dagman is not None:
        values = [line.strip() for line in dagman.stdout.splitlines() if line.strip()]
        if values:
            dagman_state = _STATUS_NAMES.get(values[0], "unknown")

    render_path = condor_dir / "render.json"
    reverse_nodes: dict[str, str] = {}
    if render_path.exists():
        render = json.loads(render_path.read_text())
        reverse_nodes = {
            node_name: task_name
            for task_name, node_name in render.get("node_names", {}).items()
        }

    nodes: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    node_query = _condor_q([
        "-constraint",
        f"DAGManJobId == {cluster_id}",
        "-af",
        "DAGNodeName",
        "JobStatus",
        "ClusterId",
        "ProcId",
    ])
    if node_query is not None:
        for line in node_query.stdout.splitlines():
            fields = line.split()
            if len(fields) < 4:
                continue
            node_name, status_code, child_cluster, proc_id = fields[:4]
            state = _STATUS_NAMES.get(status_code, "unknown")
            task_name = reverse_nodes.get(node_name, node_name)
            nodes[task_name] = {
                "node": node_name,
                "state": state,
                "job_id": f"{child_cluster}.{proc_id}",
            }
            counts[state] = counts.get(state, 0) + 1

    return {
        "cluster_id": cluster_id,
        "dagman": dagman_state,
        "counts": counts,
        "nodes": nodes,
    }
