from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import re
import shlex
import subprocess
from typing import Any

from .batch_common import archive_wrapper, worker_command
from .campaign import (
    begin_campaign,
    cancel_prepared_start,
    create_campaign,
    prepare_campaign_start,
)
from .model import CampaignSpec
from .paths import logical_absolute
from .walltime import effective_walltime

_CLUSTER_RE = re.compile(r"cluster\s+(\d+)", re.IGNORECASE)
_STARTUP_FAILURE_EXIT = 100
_PAYLOAD_FAILURE_EXIT = 101
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


def render_condor(spec: CampaignSpec, root: str | Path) -> Path:
    campaign_dir = create_campaign(spec, root, backend="condor")
    condor_dir = campaign_dir / "condor"
    condor_dir.mkdir()
    logs_dir = condor_dir / "logs"
    logs_dir.mkdir()
    startup_dir = condor_dir / "startup"
    startup_dir.mkdir()

    worker_source = Path(__file__).with_name("worker.py").read_text()
    worker = condor_dir / "yall_worker.py"
    worker.write_text(worker_source)
    worker.chmod(0o755)

    wrapper_record = archive_wrapper(spec, campaign_dir, "condor")
    archived_wrapper = Path(wrapper_record["path"]) if wrapper_record else None

    node_names: dict[str, str] = {}
    dag_lines: list[str] = []
    for index, task in enumerate(spec.tasks):
        node = f"yall_{index:04d}_{_slug(task.name)}"
        node_names[task.name] = node

        marker = startup_dir / f"{node}.started"
        command = worker_command(
            worker, campaign_dir, task.name, archived_wrapper,
            spec.condor.wrapper_args,
        )

        node_script = condor_dir / f"{node}.sh"
        marker_word = shlex.quote(str(marker))
        node_script.write_text(
            "#!/bin/bash\n"
            "set -u\n"
            f"marker={marker_word}\n"
            "export YALL_STARTUP_MARKER=\"$marker\"\n"
            "if ! rm -f \"$marker\"; then\n"
            "    echo \"yall: could not clear startup marker: $marker\" >&2\n"
            f"    exit {_STARTUP_FAILURE_EXIT}\n"
            "fi\n"
            "rc=0\n"
            f"if {command}; then\n"
            "    rc=0\n"
            "else\n"
            "    rc=$?\n"
            "fi\n"
            "if [ ! -e \"$marker\" ]; then\n"
            "    echo \"yall: startup failed before payload marker (wrapper exit=$rc)\" >&2\n"
            f"    exit {_STARTUP_FAILURE_EXIT}\n"
            "fi\n"
            "if [ \"$rc\" -ne 0 ]; then\n"
            "    echo \"yall: payload failed after startup (exit=$rc)\" >&2\n"
            f"    exit {_PAYLOAD_FAILURE_EXIT}\n"
            "fi\n"
            "exit 0\n"
        )
        node_script.chmod(0o755)

        request_cpus = task.resources.cpus or spec.condor.request_cpus
        request_memory = task.resources.memory or spec.condor.request_memory
        request_disk = task.resources.disk or spec.condor.request_disk
        walltime = effective_walltime(
            task.resources.walltime_seconds, spec.condor.request_walltime_seconds
        )
        time_line = f"+MaxRuntime = {walltime}\n" if walltime is not None else ""
        startup_retry_lines = ""
        if task.startup_retries:
            startup_retry_lines = (
                f"max_retries = {task.startup_retries}\n"
                f"retry_until = ExitCode =!= {_STARTUP_FAILURE_EXIT}\n"
                'requirements = (Machine =!= split(LastRemoteHost, "@")[1])\n'
            )

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
            f"{time_line}"
            f"{startup_retry_lines}"
            f"getenv = {'True' if spec.condor.getenv else 'False'}\n"
            "should_transfer_files = NO\n"
            "queue 1\n"
        )
        dag_lines.append(f"JOB {node} {submit.name}")
        if task.retries:
            dag_lines.append(
                f"RETRY {node} {task.retries} UNLESS-EXIT {_STARTUP_FAILURE_EXIT}"
            )

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


def submit_rendered(campaign_dir: str | Path, *, overwrite: bool = False) -> Path:
    campaign_dir = logical_absolute(campaign_dir)
    manifest_path = campaign_dir / "campaign.json"
    if not manifest_path.is_file():
        raise ValueError(f"not a yall campaign: {campaign_dir}")
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

    # DAGMan children may become runnable as soon as condor_submit_dag returns
    # the DAGMan job to the scheduler. Record the requested start policy before
    # submission so an early worker sees the same overwrite choice.
    prepare_campaign_start(campaign_dir, overwrite=overwrite)

    command = ["condor_submit_dag", dag_path.name]
    print(f"[condor] submitting {dag_path}", flush=True)
    try:
        proc = subprocess.Popen(
            command,
            cwd=condor_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except Exception:
        cancel_prepared_start(campaign_dir)
        raise

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
        "overwrite": bool(overwrite),
    }
    match = _CLUSTER_RE.search(output)
    if match:
        record["cluster_id"] = int(match.group(1))
    _write_json(submit_path, record)
    if returncode != 0:
        cancel_prepared_start(campaign_dir)
        raise RuntimeError(output.strip() or "condor_submit_dag failed")

    begin_campaign(campaign_dir, overwrite=overwrite)
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
