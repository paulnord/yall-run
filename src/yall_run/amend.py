from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
from typing import Any

from .campaign import normalized_task_definition
from .model import CampaignSpec, load_spec
from .paths import logical_absolute


QUEUED_BACKENDS = {"condor", "slurm", "pbs"}
_AMENDMENT_WORKER_MARKER = "YALL_AMENDMENT_SUPPORT = 1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, indent=2, sort_keys=True) + "\n"
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(text)
    temp.replace(path)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _env_names(source: Path) -> list[str]:
    names: list[str] = []
    for raw in source.read_text().splitlines():
        stripped = raw.strip()
        if not stripped.startswith("@env "):
            continue
        parts = shlex.split(stripped)
        if len(parts) == 2:
            names.append(parts[1])
    return names


def _load_spec_with_frozen_env(
    source: Path, manifest: dict[str, Any]
) -> CampaignSpec:
    frozen_values = {
        str(name): str(value)
        for name, value in (manifest.get("set_values") or {}).items()
    }
    names = _env_names(source)
    missing = [name for name in names if name not in frozen_values]
    if missing:
        raise ValueError(
            "current Yallfile introduces @env value(s) not frozen in the campaign: "
            + ", ".join(missing)
        )

    previous = {name: os.environ.get(name) for name in names}
    try:
        for name in names:
            os.environ[name] = frozen_values[name]
        spec = load_spec(source)
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    current_values = dict(spec.set_values)
    changed_values = [
        name
        for name, value in current_values.items()
        if frozen_values.get(name) != value
    ]
    removed_values = [name for name in frozen_values if name not in current_values]
    if changed_values or removed_values:
        changed = sorted(set(changed_values + removed_values))
        raise ValueError(
            "current Yallfile changes frozen @set/@env value(s): " + ", ".join(changed)
        )
    return spec


def _task_state(campaign_dir: Path, task_name: str) -> dict[str, Any]:
    path = campaign_dir / "state" / f"{task_name}.json"
    if not path.is_file():
        return {"state": "pending", "attempts": 0}
    return _read_json(path)


def _normalized_command(command: Any) -> str | list[str]:
    if isinstance(command, str):
        return command
    return [str(item) for item in command]


def _amendment_records(campaign_dir: Path) -> list[tuple[int, Path, dict[str, Any]]]:
    root = campaign_dir / "amendments"
    if not root.is_dir():
        return []
    records: list[tuple[int, Path, dict[str, Any]]] = []
    for directory in sorted(root.iterdir()):
        if not directory.is_dir() or not directory.name.isdigit():
            continue
        path = directory / "amendment.json"
        if not path.is_file():
            continue
        record = _read_json(path)
        number = int(record.get("number", int(directory.name)))
        records.append((number, path, record))
    records.sort(key=lambda item: item[0])
    return records


def _effective_command(
    campaign_dir: Path,
    task_name: str,
    frozen_command: Any,
) -> tuple[str | list[str], list[dict[str, Any]]]:
    command = _normalized_command(frozen_command)
    applied: list[dict[str, Any]] = []
    for number, path, record in _amendment_records(campaign_dir):
        for change in record.get("changes", []):
            if change.get("task") != task_name:
                continue
            if change.get("field") != "command":
                raise ValueError(
                    f"amendment {number:04d} contains unsupported field "
                    f"{change.get('field')!r} for task {task_name!r}"
                )
            before = change.get("before")
            after = change.get("after")
            if command != before:
                raise ValueError(
                    f"amendment chain mismatch for task {task_name!r} at {path}"
                )
            command = _normalized_command(after)
            applied.append({
                "number": number,
                "path": str(path),
                "sha256": _sha256_file(path),
            })
    return command, applied


def _validate_wrapper(spec: CampaignSpec, manifest: dict[str, Any]) -> None:
    frozen = (manifest.get("execution") or {}).get("wrapper")
    current = spec.execution.wrapper
    if current is None:
        if frozen is not None:
            raise ValueError("current Yallfile removes the frozen payload wrapper")
        return
    if not isinstance(frozen, dict):
        raise ValueError("current Yallfile adds a payload wrapper to the campaign")
    source = str(logical_absolute(current, spec.source.parent))
    if source != frozen.get("source") or list(spec.execution.wrapper_args) != list(
        frozen.get("args") or []
    ):
        raise ValueError("current Yallfile changes the frozen payload wrapper")


def _validate_preflight(spec: CampaignSpec, manifest: dict[str, Any]) -> None:
    frozen = [
        {"command": record.get("command"), "cwd": record.get("cwd")}
        for record in manifest.get("preflight", [])
    ]
    current = [
        {"command": _normalized_command(command), "cwd": str(spec.source.parent)}
        for command in spec.preflight
    ]
    if current != frozen:
        raise ValueError(
            "current Yallfile changes frozen preflight commands or working directory; "
            "preflight runs only during creation; create a new campaign"
        )


def _validate_postflight(spec: CampaignSpec, manifest: dict[str, Any]) -> None:
    frozen = [
        {"command": record.get("command"), "cwd": record.get("cwd")}
        for record in manifest.get("postflight", [])
    ]
    current = [
        {"command": _normalized_command(command), "cwd": "campaign_dir"}
        for command in spec.postflight
    ]
    if current != frozen:
        raise ValueError(
            "current Yallfile changes frozen postflight commands or working directory; "
            "postflight is frozen at creation; create a new campaign"
        )


def _validate_backend_defaults(
    campaign_dir: Path,
    spec: CampaignSpec,
    backend: str,
) -> None:
    if backend == "condor":
        render_path = campaign_dir / "condor" / "render.json"
        if render_path.is_file():
            frozen = _read_json(render_path).get("condor") or {}
            if frozen != asdict(spec.condor):
                raise ValueError("current Yallfile changes frozen Condor defaults")
        return
    if backend in {"slurm", "pbs"}:
        render_path = campaign_dir / backend / "render.json"
        if not render_path.is_file():
            return
        frozen = _read_json(render_path).get("resources") or {}
        current = {
            "cpus": spec.condor.request_cpus,
            "memory": spec.condor.request_memory,
            "disk": spec.condor.request_disk,
            "walltime_seconds": spec.condor.request_walltime_seconds,
        }
        if frozen != current:
            raise ValueError(f"current Yallfile changes frozen {backend} defaults")


def _validate_bundled_worker(campaign_dir: Path, backend: str) -> None:
    if backend not in QUEUED_BACKENDS:
        return
    worker = campaign_dir / backend / "yall_worker.py"
    if not worker.is_file() or _AMENDMENT_WORKER_MARKER not in worker.read_text():
        raise ValueError(
            "campaign bundled worker predates amendment support; "
            "queued campaigns created by older yall-run versions cannot be amended safely"
        )


def _validate_inactive(campaign_dir: Path, backend: str) -> None:
    running = []
    state_dir = campaign_dir / "state"
    if state_dir.is_dir():
        for path in state_dir.glob("*.json"):
            if path.name == "start-pending.json":
                continue
            state = _read_json(path).get("state")
            if state == "running":
                running.append(path.stem)
    if running:
        raise ValueError(
            "cannot amend while tasks are running: " + ", ".join(sorted(running))
        )

    if backend not in QUEUED_BACKENDS:
        return
    from .campaign import campaign_status
    from .recovery import reconcile_status

    status = reconcile_status(campaign_dir, campaign_status(campaign_dir))
    scheduler = status.get("scheduler") or {}
    if scheduler.get("query_ok") is False:
        raise ValueError(
            "cannot amend while scheduler state is unknown: "
            + str(scheduler.get("error") or "query failed")
        )
    active_states = {"queued", "running", "transferring", "suspended", "unknown"}
    active = [
        str(task["name"])
        for task in status.get("tasks", [])
        if task.get("state") in active_states
    ]
    if active:
        raise ValueError(
            "cannot amend while scheduler jobs are active: " + ", ".join(active)
        )


def amend_campaign(
    campaign_dir: str | Path,
    *,
    spec_path: str | Path | None = None,
    dry_run: bool = False,
    reason: str | None = None,
) -> dict[str, Any]:
    campaign_dir = logical_absolute(campaign_dir)
    manifest_path = campaign_dir / "campaign.json"
    if not manifest_path.is_file():
        raise ValueError(f"not a yall campaign: {campaign_dir}")
    if not (campaign_dir / "start.json").is_file():
        raise ValueError("campaign has not been started; edit the Yallfile and create a new campaign")

    manifest = _read_json(manifest_path)
    if not isinstance(manifest.get("tasks"), dict):
        raise ValueError("legacy campaign format cannot be amended")
    backend = str(manifest.get("backend", "local"))
    _validate_bundled_worker(campaign_dir, backend)
    _validate_inactive(campaign_dir, backend)

    if spec_path is None:
        recorded = manifest.get("spec_source")
        if not recorded:
            raise ValueError("campaign does not record its source Yallfile; use --from PATH")
        source = Path(str(recorded)).expanduser()
    else:
        source = Path(spec_path).expanduser()
    source = logical_absolute(source)
    if not source.is_file():
        raise ValueError(f"Yallfile not found: {source}")

    spec = _load_spec_with_frozen_env(source, manifest)
    if spec.name != manifest.get("name"):
        raise ValueError(
            f"current Yallfile campaign name {spec.name!r} does not match "
            f"frozen campaign {manifest.get('name')!r}"
        )
    if spec.backend != backend:
        raise ValueError(
            f"current Yallfile backend {spec.backend!r} does not match frozen backend {backend!r}"
        )
    _validate_wrapper(spec, manifest)
    _validate_preflight(spec, manifest)
    _validate_postflight(spec, manifest)
    _validate_backend_defaults(campaign_dir, spec, backend)

    frozen_tasks = manifest["tasks"]
    frozen_order = [str(name) for name in manifest.get("task_order") or frozen_tasks]
    current_order = [task.name for task in spec.tasks]
    if current_order != frozen_order:
        raise ValueError(
            "current Yallfile changes the frozen task set or order; create a new campaign"
        )

    changes: list[dict[str, Any]] = []
    for task in spec.tasks:
        frozen = frozen_tasks.get(task.name)
        if not isinstance(frozen, dict):
            raise ValueError(f"frozen task definition missing: {task.name}")
        current = normalized_task_definition(spec, task)
        baseline = {
            key: frozen.get(key)
            for key in (
                "parents",
                "command",
                "cwd",
                "retries",
                "startup_retries",
                "outputs",
                "resources",
                "overwrite",
            )
        }
        baseline["inputs"] = [
            {"role": ref.get("role"), "path": ref.get("path")}
            for ref in frozen.get("inputs", [])
        ]
        baseline["command"], applied = _effective_command(
            campaign_dir, task.name, baseline["command"]
        )

        unsupported = [
            key
            for key in (
                "parents",
                "cwd",
                "retries",
                "startup_retries",
                "inputs",
                "outputs",
                "resources",
                "overwrite",
            )
            if current[key] != baseline[key]
        ]
        if unsupported:
            raise ValueError(
                f"current Yallfile changes unsupported field(s) for task {task.name!r}: "
                + ", ".join(unsupported)
                + "; create a new campaign"
            )

        if current["command"] == baseline["command"]:
            continue
        state = _task_state(campaign_dir, task.name)
        task_state = str(state.get("state", "pending"))
        if task_state == "completed":
            raise ValueError(
                f"current Yallfile changes completed task {task.name!r}; create a new campaign"
            )
        changes.append({
            "task": task.name,
            "field": "command",
            "state": task_state,
            "attempts": int(state.get("attempts", 0)),
            "before": baseline["command"],
            "after": current["command"],
            "prior_amendments": applied,
        })

    if not changes:
        return {
            "status": "no_changes",
            "campaign": str(campaign_dir),
            "source": str(source),
            "changes": [],
        }

    existing = _amendment_records(campaign_dir)
    number = max((item[0] for item in existing), default=0) + 1
    source_bytes = source.read_bytes()
    parent = None
    if existing:
        parent_number, parent_path, _ = existing[-1]
        parent = {
            "number": parent_number,
            "path": str(parent_path),
            "sha256": _sha256_file(parent_path),
        }
    record: dict[str, Any] = {
        "schema": 1,
        "number": number,
        "created_at": _utc_now(),
        "campaign_id": manifest.get("id") or campaign_dir.name,
        "reason": reason,
        "source": str(source),
        "base_spec_sha256": (manifest.get("spec_archive") or {}).get("sha256"),
        "parent_amendment": parent,
        "revised_yallfile": {
            "path": "Yallfile",
            "sha256": _sha256_bytes(source_bytes),
        },
        "changes": changes,
    }

    result = {
        "status": "dry_run" if dry_run else "written",
        "campaign": str(campaign_dir),
        "source": str(source),
        "number": number,
        "changes": changes,
    }
    if dry_run:
        return result

    directory = campaign_dir / "amendments" / f"{number:04d}"
    if directory.exists():
        raise ValueError(f"amendment directory already exists: {directory}")
    directory.mkdir(parents=True)
    try:
        (directory / "Yallfile").write_bytes(source_bytes)
        _write_json(directory / "amendment.json", record)
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    result["path"] = str(directory / "amendment.json")
    result["sha256"] = _sha256_file(directory / "amendment.json")
    return result
