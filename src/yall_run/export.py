from __future__ import annotations

import csv
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable


TABLES: dict[str, tuple[tuple[str, str], ...]] = {
    "campaign": (
        ("campaign_id", "TEXT PRIMARY KEY"),
        ("name", "TEXT NOT NULL"),
        ("backend", "TEXT NOT NULL"),
        ("schema_version", "INTEGER"),
        ("yall_version", "TEXT"),
        ("created_at", "TEXT"),
        ("campaign_dir", "TEXT NOT NULL"),
        ("spec_source", "TEXT"),
        ("spec_archive_path", "TEXT"),
        ("spec_source_name", "TEXT"),
        ("spec_sha256", "TEXT"),
        ("input_hash_algorithm", "TEXT"),
        ("input_hash_max_bytes", "INTEGER"),
        ("creation_hostname", "TEXT"),
        ("creation_platform", "TEXT"),
        ("creation_python", "TEXT"),
        ("creation_cwd", "TEXT"),
        ("creation_argv_json", "TEXT"),
    ),
    "campaign_set": (
        ("campaign_id", "TEXT NOT NULL"),
        ("name", "TEXT NOT NULL"),
        ("value", "TEXT"),
        ("PRIMARY KEY (campaign_id, name)", ""),
        ("FOREIGN KEY (campaign_id) REFERENCES campaign(campaign_id)", ""),
    ),
    "task": (
        ("campaign_id", "TEXT NOT NULL"),
        ("task_name", "TEXT NOT NULL"),
        ("task_index", "INTEGER NOT NULL"),
        ("cwd", "TEXT"),
        ("command_json", "TEXT"),
        ("retries", "INTEGER NOT NULL"),
        ("overwrite", "INTEGER NOT NULL"),
        ("cpus", "INTEGER"),
        ("memory", "TEXT"),
        ("disk", "TEXT"),
        ("walltime_seconds", "INTEGER"),
        ("PRIMARY KEY (campaign_id, task_name)", ""),
        ("FOREIGN KEY (campaign_id) REFERENCES campaign(campaign_id)", ""),
    ),
    "task_parent": (
        ("campaign_id", "TEXT NOT NULL"),
        ("task_name", "TEXT NOT NULL"),
        ("parent_task_name", "TEXT NOT NULL"),
        ("PRIMARY KEY (campaign_id, task_name, parent_task_name)", ""),
        ("FOREIGN KEY (campaign_id, task_name) REFERENCES task(campaign_id, task_name)", ""),
        ("FOREIGN KEY (campaign_id, parent_task_name) REFERENCES task(campaign_id, task_name)", ""),
    ),
    "task_input": (
        ("campaign_id", "TEXT NOT NULL"),
        ("task_name", "TEXT NOT NULL"),
        ("input_index", "INTEGER NOT NULL"),
        ("role", "TEXT"),
        ("path", "TEXT NOT NULL"),
        ("creation_size_bytes", "INTEGER"),
        ("creation_sha256", "TEXT"),
        ("creation_sha256_skipped", "TEXT"),
        ("creation_hash_error", "TEXT"),
        ("PRIMARY KEY (campaign_id, task_name, input_index)", ""),
        ("FOREIGN KEY (campaign_id, task_name) REFERENCES task(campaign_id, task_name)", ""),
    ),
    "task_output": (
        ("campaign_id", "TEXT NOT NULL"),
        ("task_name", "TEXT NOT NULL"),
        ("output_index", "INTEGER NOT NULL"),
        ("role", "TEXT"),
        ("path", "TEXT NOT NULL"),
        ("PRIMARY KEY (campaign_id, task_name, output_index)", ""),
        ("FOREIGN KEY (campaign_id, task_name) REFERENCES task(campaign_id, task_name)", ""),
    ),
    "task_executable": (
        ("campaign_id", "TEXT NOT NULL"),
        ("task_name", "TEXT NOT NULL"),
        ("argv0", "TEXT"),
        ("resolution", "TEXT"),
        ("resolved", "INTEGER"),
        ("path", "TEXT"),
        ("realpath", "TEXT"),
        ("size_bytes", "INTEGER"),
        ("sha256", "TEXT"),
        ("stat_error", "TEXT"),
        ("PRIMARY KEY (campaign_id, task_name)", ""),
        ("FOREIGN KEY (campaign_id, task_name) REFERENCES task(campaign_id, task_name)", ""),
    ),
    "campaign_start": (
        ("campaign_id", "TEXT PRIMARY KEY"),
        ("started_at", "TEXT"),
        ("backend", "TEXT"),
        ("overwrite", "INTEGER"),
        ("execution_json", "TEXT"),
        ("FOREIGN KEY (campaign_id) REFERENCES campaign(campaign_id)", ""),
    ),
    "task_state": (
        ("campaign_id", "TEXT NOT NULL"),
        ("task_name", "TEXT NOT NULL"),
        ("state", "TEXT NOT NULL"),
        ("attempts", "INTEGER NOT NULL"),
        ("last_returncode", "INTEGER"),
        ("PRIMARY KEY (campaign_id, task_name)", ""),
        ("FOREIGN KEY (campaign_id, task_name) REFERENCES task(campaign_id, task_name)", ""),
    ),
    "attempt": (
        ("campaign_id", "TEXT NOT NULL"),
        ("task_name", "TEXT NOT NULL"),
        ("attempt", "INTEGER NOT NULL"),
        ("state", "TEXT"),
        ("started_at", "TEXT"),
        ("finished_at", "TEXT"),
        ("returncode", "INTEGER"),
        ("command_returncode", "INTEGER"),
        ("worker_pid", "INTEGER"),
        ("launch_pid", "INTEGER"),
        ("cwd", "TEXT"),
        ("command_json", "TEXT"),
        ("provenance_path", "TEXT"),
        ("stdout_path", "TEXT"),
        ("stderr_path", "TEXT"),
        ("failure_kind", "TEXT"),
        ("failure_paths_json", "TEXT"),
        ("real_seconds", "REAL"),
        ("user_seconds", "REAL"),
        ("sys_seconds", "REAL"),
        ("resource_usage_json", "TEXT"),
        ("PRIMARY KEY (campaign_id, task_name, attempt)", ""),
        ("FOREIGN KEY (campaign_id, task_name) REFERENCES task(campaign_id, task_name)", ""),
    ),
    "attempt_input": (
        ("campaign_id", "TEXT NOT NULL"),
        ("task_name", "TEXT NOT NULL"),
        ("attempt", "INTEGER NOT NULL"),
        ("input_index", "INTEGER NOT NULL"),
        ("role", "TEXT"),
        ("path", "TEXT NOT NULL"),
        ("path_exists", "INTEGER"),
        ("kind", "TEXT"),
        ("size_bytes", "INTEGER"),
        ("mtime_ns", "INTEGER"),
        ("PRIMARY KEY (campaign_id, task_name, attempt, input_index)", ""),
        ("FOREIGN KEY (campaign_id, task_name, attempt) REFERENCES attempt(campaign_id, task_name, attempt)", ""),
    ),
    "attempt_output": (
        ("campaign_id", "TEXT NOT NULL"),
        ("task_name", "TEXT NOT NULL"),
        ("attempt", "INTEGER NOT NULL"),
        ("output_index", "INTEGER NOT NULL"),
        ("role", "TEXT"),
        ("path", "TEXT NOT NULL"),
        ("path_exists", "INTEGER"),
        ("kind", "TEXT"),
        ("size_bytes", "INTEGER"),
        ("mtime_ns", "INTEGER"),
        ("PRIMARY KEY (campaign_id, task_name, attempt, output_index)", ""),
        ("FOREIGN KEY (campaign_id, task_name, attempt) REFERENCES attempt(campaign_id, task_name, attempt)", ""),
    ),
    "attempt_provenance": (
        ("campaign_id", "TEXT NOT NULL"),
        ("task_name", "TEXT NOT NULL"),
        ("attempt", "INTEGER NOT NULL"),
        ("hostname", "TEXT"),
        ("platform", "TEXT"),
        ("architecture", "TEXT"),
        ("cpu_vendor", "TEXT"),
        ("cpu_model_name", "TEXT"),
        ("cpu_family", "INTEGER"),
        ("cpu_model", "INTEGER"),
        ("cpu_stepping", "INTEGER"),
        ("affinity_count", "INTEGER"),
        ("machine_json", "TEXT"),
        ("environment_json", "TEXT"),
        ("resource_limits_json", "TEXT"),
        ("scheduler_json", "TEXT"),
        ("scheduler_machine", "TEXT"),
        ("scheduler_cpu_family", "INTEGER"),
        ("scheduler_cpu_model_number", "INTEGER"),
        ("python", "TEXT"),
        ("worker_pid", "INTEGER"),
        ("campaign_overwrite", "INTEGER"),
        ("task_overwrite", "INTEGER"),
        ("resources_json", "TEXT"),
        ("amendments_json", "TEXT"),
        ("PRIMARY KEY (campaign_id, task_name, attempt)", ""),
        ("FOREIGN KEY (campaign_id, task_name, attempt) REFERENCES attempt(campaign_id, task_name, attempt)", ""),
    ),
    "amendment": (
        ("campaign_id", "TEXT NOT NULL"),
        ("amendment_number", "INTEGER NOT NULL"),
        ("created_at", "TEXT"),
        ("reason", "TEXT"),
        ("source", "TEXT"),
        ("base_spec_sha256", "TEXT"),
        ("revised_yallfile_path", "TEXT"),
        ("revised_yallfile_sha256", "TEXT"),
        ("parent_amendment_number", "INTEGER"),
        ("parent_amendment_sha256", "TEXT"),
        ("amendment_sha256", "TEXT NOT NULL"),
        ("PRIMARY KEY (campaign_id, amendment_number)", ""),
        ("FOREIGN KEY (campaign_id) REFERENCES campaign(campaign_id)", ""),
    ),
    "amendment_change": (
        ("campaign_id", "TEXT NOT NULL"),
        ("amendment_number", "INTEGER NOT NULL"),
        ("change_index", "INTEGER NOT NULL"),
        ("task_name", "TEXT NOT NULL"),
        ("field_name", "TEXT NOT NULL"),
        ("prior_state", "TEXT"),
        ("prior_attempts", "INTEGER"),
        ("before_json", "TEXT"),
        ("after_json", "TEXT"),
        ("PRIMARY KEY (campaign_id, amendment_number, change_index)", ""),
        ("FOREIGN KEY (campaign_id, amendment_number) REFERENCES amendment(campaign_id, amendment_number)", ""),
    ),
    "resume": (
        ("campaign_id", "TEXT NOT NULL"),
        ("resume_number", "INTEGER NOT NULL"),
        ("started_at", "TEXT"),
        ("finished_at", "TEXT"),
        ("backend", "TEXT"),
        ("result", "TEXT"),
        ("reason", "TEXT"),
        ("PRIMARY KEY (campaign_id, resume_number)", ""),
        ("FOREIGN KEY (campaign_id) REFERENCES campaign(campaign_id)", ""),
    ),
    "resume_count": (
        ("campaign_id", "TEXT NOT NULL"),
        ("resume_number", "INTEGER NOT NULL"),
        ("phase", "TEXT NOT NULL"),
        ("state", "TEXT NOT NULL"),
        ("count", "INTEGER NOT NULL"),
        ("PRIMARY KEY (campaign_id, resume_number, phase, state)", ""),
        ("FOREIGN KEY (campaign_id, resume_number) REFERENCES resume(campaign_id, resume_number)", ""),
    ),
}


def _column_names(table: str) -> list[str]:
    return [name for name, decl in TABLES[table] if decl]


def _json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _bool(value: Any) -> int | None:
    return None if value is None else int(bool(value))


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def discover_campaigns(sources: Iterable[str | Path]) -> list[Path]:
    found: dict[str, Path] = {}
    for source in sources:
        path = Path(source).expanduser().absolute()
        if (path / "campaign.json").is_file():
            manifest = _read_json(path / "campaign.json")
            campaign_id = str(manifest.get("id") or path.name)
            found[campaign_id] = path
            continue
        if not path.is_dir():
            raise ValueError(f"export source is not a campaign or directory: {path}")
        for manifest_path in path.rglob("campaign.json"):
            campaign_dir = manifest_path.parent
            manifest = _read_json(manifest_path)
            campaign_id = str(manifest.get("id") or campaign_dir.name)
            previous = found.get(campaign_id)
            if previous is not None and previous != campaign_dir:
                raise ValueError(
                    f"duplicate campaign id {campaign_id!r}: {previous} and {campaign_dir}"
                )
            found[campaign_id] = campaign_dir
    return [found[key] for key in sorted(found)]


def _append_file_rows(
    rows: dict[str, list[dict[str, Any]]],
    table: str,
    campaign_id: str,
    task_name: str,
    attempt_number: int,
    values: list[Any],
    index_name: str,
) -> None:
    for index, value in enumerate(values):
        ref = value if isinstance(value, dict) else {"path": value}
        rows[table].append({
            "campaign_id": campaign_id,
            "task_name": task_name,
            "attempt": attempt_number,
            index_name: index,
            "role": ref.get("role"),
            "path": str(ref.get("path", "")),
            "path_exists": _bool(ref.get("exists")),
            "kind": ref.get("kind"),
            "size_bytes": ref.get("size_bytes"),
            "mtime_ns": ref.get("mtime_ns"),
        })


def scrape_campaign(campaign_dir: str | Path) -> dict[str, list[dict[str, Any]]]:
    campaign_dir = Path(campaign_dir).expanduser().absolute()
    manifest = _read_json(campaign_dir / "campaign.json")
    campaign_id = str(manifest.get("id") or campaign_dir.name)
    rows = {name: [] for name in TABLES}

    archive = manifest.get("spec_archive") or {}
    hash_policy = manifest.get("input_hash_policy") or {}
    creation = manifest.get("creation") or {}
    rows["campaign"].append({
        "campaign_id": campaign_id,
        "name": str(manifest.get("name", "")),
        "backend": str(manifest.get("backend", "local")),
        "schema_version": manifest.get("schema"),
        "yall_version": manifest.get("yall_version"),
        "created_at": manifest.get("created_at"),
        "campaign_dir": str(campaign_dir),
        "spec_source": manifest.get("spec_source"),
        "spec_archive_path": archive.get("path"),
        "spec_source_name": archive.get("source_name"),
        "spec_sha256": archive.get("sha256"),
        "input_hash_algorithm": hash_policy.get("algorithm"),
        "input_hash_max_bytes": hash_policy.get("max_bytes"),
        "creation_hostname": creation.get("hostname"),
        "creation_platform": creation.get("platform"),
        "creation_python": creation.get("python"),
        "creation_cwd": creation.get("cwd"),
        "creation_argv_json": _json(creation.get("argv")),
    })

    for name, value in sorted((manifest.get("set_values") or {}).items()):
        rows["campaign_set"].append({
            "campaign_id": campaign_id,
            "name": str(name),
            "value": str(value),
        })

    task_defs = manifest.get("tasks") or {}
    task_order = manifest.get("task_order") or list(task_defs)
    if not isinstance(task_defs, dict):
        task_defs = {}
    for task_index, task_name_value in enumerate(task_order):
        task_name = str(task_name_value)
        task = task_defs.get(task_name) or {}
        resources = task.get("resources") or {}
        rows["task"].append({
            "campaign_id": campaign_id,
            "task_name": task_name,
            "task_index": task_index,
            "cwd": task.get("cwd"),
            "command_json": _json(task.get("command")),
            "retries": int(task.get("retries", 0)),
            "overwrite": _bool(task.get("overwrite", False)),
            "cpus": resources.get("cpus"),
            "memory": resources.get("memory"),
            "disk": resources.get("disk"),
            "walltime_seconds": resources.get("walltime_seconds"),
        })
        for parent in task.get("parents", []):
            rows["task_parent"].append({
                "campaign_id": campaign_id,
                "task_name": task_name,
                "parent_task_name": str(parent),
            })
        for index, value in enumerate(task.get("inputs", [])):
            ref = value if isinstance(value, dict) else {"path": value}
            fp = ref.get("creation_fingerprint") or {}
            rows["task_input"].append({
                "campaign_id": campaign_id,
                "task_name": task_name,
                "input_index": index,
                "role": ref.get("role"),
                "path": str(ref.get("path", "")),
                "creation_size_bytes": fp.get("size_bytes"),
                "creation_sha256": fp.get("sha256"),
                "creation_sha256_skipped": fp.get("sha256_skipped"),
                "creation_hash_error": fp.get("hash_error"),
            })
        for index, value in enumerate(task.get("outputs", [])):
            ref = value if isinstance(value, dict) else {"path": value}
            rows["task_output"].append({
                "campaign_id": campaign_id,
                "task_name": task_name,
                "output_index": index,
                "role": ref.get("role"),
                "path": str(ref.get("path", "")),
            })
        executable = task.get("executable")
        if isinstance(executable, dict):
            rows["task_executable"].append({
                "campaign_id": campaign_id,
                "task_name": task_name,
                "argv0": executable.get("argv0"),
                "resolution": executable.get("resolution"),
                "resolved": _bool(executable.get("resolved")),
                "path": executable.get("path"),
                "realpath": executable.get("realpath"),
                "size_bytes": executable.get("size_bytes"),
                "sha256": executable.get("sha256"),
                "stat_error": executable.get("stat_error"),
            })

    start_path = campaign_dir / "start.json"
    if start_path.is_file():
        start = _read_json(start_path)
        rows["campaign_start"].append({
            "campaign_id": campaign_id,
            "started_at": start.get("started_at"),
            "backend": start.get("backend"),
            "overwrite": _bool(start.get("overwrite")),
            "execution_json": _json(start.get("execution")),
        })

    amendments_dir = campaign_dir / "amendments"
    if amendments_dir.is_dir():
        for amendment_dir in sorted(amendments_dir.iterdir()):
            if not amendment_dir.is_dir() or not amendment_dir.name.isdigit():
                continue
            amendment_path = amendment_dir / "amendment.json"
            if not amendment_path.is_file():
                continue
            amendment = _read_json(amendment_path)
            number = int(amendment.get("number", int(amendment_dir.name)))
            revised = amendment.get("revised_yallfile") or {}
            parent = amendment.get("parent_amendment") or {}
            rows["amendment"].append({
                "campaign_id": campaign_id,
                "amendment_number": number,
                "created_at": amendment.get("created_at"),
                "reason": amendment.get("reason"),
                "source": amendment.get("source"),
                "base_spec_sha256": amendment.get("base_spec_sha256"),
                "revised_yallfile_path": revised.get("path"),
                "revised_yallfile_sha256": revised.get("sha256"),
                "parent_amendment_number": parent.get("number"),
                "parent_amendment_sha256": parent.get("sha256"),
                "amendment_sha256": __import__("hashlib").sha256(
                    amendment_path.read_bytes()
                ).hexdigest(),
            })
            for index, change in enumerate(amendment.get("changes", [])):
                rows["amendment_change"].append({
                    "campaign_id": campaign_id,
                    "amendment_number": number,
                    "change_index": index,
                    "task_name": str(change.get("task", "")),
                    "field_name": str(change.get("field", "")),
                    "prior_state": change.get("state"),
                    "prior_attempts": change.get("attempts"),
                    "before_json": _json(change.get("before")),
                    "after_json": _json(change.get("after")),
                })

    state_dir = campaign_dir / "state"
    for task_name_value in task_order:
        task_name = str(task_name_value)
        state_path = state_dir / f"{task_name}.json"
        if not state_path.is_file():
            continue
        state = _read_json(state_path)
        rows["task_state"].append({
            "campaign_id": campaign_id,
            "task_name": task_name,
            "state": str(state.get("state", "pending")),
            "attempts": int(state.get("attempts", 0)),
            "last_returncode": state.get("last_returncode"),
        })

    attempt_re = re.compile(r"^(?P<task>.+)_attempt_(?P<number>\d+)$")
    for attempt_dir in sorted(campaign_dir.glob("*_attempt_*")):
        if not attempt_dir.is_dir():
            continue
        match = attempt_re.match(attempt_dir.name)
        if match is None:
            continue
        attempt_path = attempt_dir / "attempt.json"
        if not attempt_path.is_file():
            continue
        attempt = _read_json(attempt_path)
        task_name = str(attempt.get("task") or match.group("task"))
        number = int(attempt.get("attempt") or match.group("number"))
        timing = attempt.get("timing") or {}
        failure = attempt.get("failure") or {}
        rows["attempt"].append({
            "campaign_id": campaign_id,
            "task_name": task_name,
            "attempt": number,
            "state": attempt.get("state"),
            "started_at": attempt.get("started_at"),
            "finished_at": attempt.get("finished_at"),
            "returncode": attempt.get("returncode"),
            "command_returncode": attempt.get("command_returncode"),
            "worker_pid": attempt.get("worker_pid"),
            "launch_pid": attempt.get("launch_pid"),
            "cwd": attempt.get("cwd"),
            "command_json": _json(attempt.get("command")),
            "provenance_path": attempt.get("provenance"),
            "stdout_path": attempt.get("stdout"),
            "stderr_path": attempt.get("stderr"),
            "failure_kind": failure.get("kind"),
            "failure_paths_json": _json(failure.get("paths")),
            "real_seconds": timing.get("real_seconds"),
            "user_seconds": timing.get("user_seconds"),
            "sys_seconds": timing.get("sys_seconds"),
            "resource_usage_json": _json(timing.get("resource_usage")),
        })
        _append_file_rows(
            rows, "attempt_input", campaign_id, task_name, number,
            list(attempt.get("inputs") or []), "input_index"
        )
        _append_file_rows(
            rows, "attempt_output", campaign_id, task_name, number,
            list(attempt.get("outputs") or []), "output_index"
        )
        provenance_path = attempt_dir / "provenance.json"
        if provenance_path.is_file():
            provenance = _read_json(provenance_path)
            execution = provenance.get("execution") or {}
            ptask = provenance.get("task") or {}
            machine = execution.get("machine") or {}
            cpu = machine.get("cpu") or {}
            affinity = machine.get("affinity") or {}
            scheduler = provenance.get("scheduler") or {}
            scheduler_machine = scheduler.get("machine") or {}
            rows["attempt_provenance"].append({
                "campaign_id": campaign_id,
                "task_name": task_name,
                "attempt": number,
                "hostname": execution.get("hostname"),
                "platform": execution.get("platform"),
                "architecture": machine.get("architecture"),
                "cpu_vendor": cpu.get("vendor_id", cpu.get("implementer")),
                "cpu_model_name": cpu.get("model_name"),
                "cpu_family": cpu.get("family"),
                "cpu_model": cpu.get("model"),
                "cpu_stepping": cpu.get("stepping"),
                "affinity_count": affinity.get("count"),
                "machine_json": _json(machine),
                "environment_json": _json(execution.get("environment")),
                "resource_limits_json": _json(execution.get("resource_limits")),
                "scheduler_json": _json(scheduler),
                "scheduler_machine": scheduler_machine.get("machine"),
                "scheduler_cpu_family": scheduler_machine.get("cpu_family"),
                "scheduler_cpu_model_number": scheduler_machine.get("cpu_model_number"),
                "python": execution.get("python"),
                "worker_pid": execution.get("worker_pid", execution.get("pid")),
                "campaign_overwrite": _bool(execution.get("campaign_overwrite")),
                "task_overwrite": _bool(ptask.get("overwrite")),
                "resources_json": _json(ptask.get("resources")),
                "amendments_json": _json(ptask.get("amendments")),
            })

    resume_re = re.compile(r"^resume_(\d+)\.json$")
    resumes_dir = campaign_dir / "resumes"
    if resumes_dir.is_dir():
        resume_paths: list[tuple[int, Path]] = []
        for resume_path in sorted(resumes_dir.glob("resume_*.json")):
            match = resume_re.match(resume_path.name)
            if match is not None:
                resume_paths.append((int(match.group(1)), resume_path))
        for round_dir in sorted(resumes_dir.iterdir()):
            if not round_dir.is_dir() or not round_dir.name.isdigit():
                continue
            resume_path = round_dir / "resume.json"
            if resume_path.is_file():
                resume_paths.append((int(round_dir.name), resume_path))

        for number, resume_path in sorted(resume_paths):
            resume = _read_json(resume_path)
            rows["resume"].append({
                "campaign_id": campaign_id,
                "resume_number": number,
                "started_at": resume.get("started_at"),
                "finished_at": resume.get("finished_at"),
                "backend": resume.get("backend"),
                "result": resume.get("result", resume.get("status")),
                "reason": resume.get("reason"),
            })
            for phase, key in (("initial", "initial_counts"), ("final", "final_counts")):
                for state, count in sorted((resume.get(key) or {}).items()):
                    rows["resume_count"].append({
                        "campaign_id": campaign_id,
                        "resume_number": number,
                        "phase": phase,
                        "state": str(state),
                        "count": int(count),
                    })

    return rows


def collect_rows(campaign_dirs: Iterable[str | Path]) -> dict[str, list[dict[str, Any]]]:
    combined = {name: [] for name in TABLES}
    for campaign_dir in campaign_dirs:
        rows = scrape_campaign(campaign_dir)
        for table in TABLES:
            combined[table].extend(rows[table])
    return combined


def schema_sql() -> str:
    statements = ["PRAGMA foreign_keys = ON;"]
    for table, columns in TABLES.items():
        body = []
        for name, declaration in columns:
            body.append(f"    {name} {declaration}".rstrip())
        statements.append(f"CREATE TABLE IF NOT EXISTS {table} (\n" + ",\n".join(body) + "\n);")
    return "\n\n".join(statements) + "\n"


def write_sqlite(path: str | Path, rows: dict[str, list[dict[str, Any]]]) -> Path:
    path = Path(path).expanduser().absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA foreign_keys = ON")
        db.executescript(schema_sql())
        # Additive migration for databases exported before %time existed.
        columns = {row[1] for row in db.execute("PRAGMA table_info(task)")}
        if "walltime_seconds" not in columns:
            db.execute("ALTER TABLE task ADD COLUMN walltime_seconds INTEGER")
        attempt_columns = {row[1] for row in db.execute("PRAGMA table_info(attempt)")}
        if "resource_usage_json" not in attempt_columns:
            db.execute("ALTER TABLE attempt ADD COLUMN resource_usage_json TEXT")
        provenance_columns = {
            row[1] for row in db.execute("PRAGMA table_info(attempt_provenance)")
        }
        for column, declaration in (
            ("architecture", "TEXT"),
            ("cpu_vendor", "TEXT"),
            ("cpu_model_name", "TEXT"),
            ("cpu_family", "INTEGER"),
            ("cpu_model", "INTEGER"),
            ("cpu_stepping", "INTEGER"),
            ("affinity_count", "INTEGER"),
            ("machine_json", "TEXT"),
            ("environment_json", "TEXT"),
            ("resource_limits_json", "TEXT"),
            ("scheduler_json", "TEXT"),
            ("scheduler_machine", "TEXT"),
            ("scheduler_cpu_family", "INTEGER"),
            ("scheduler_cpu_model_number", "INTEGER"),
            ("amendments_json", "TEXT"),
        ):
            if column not in provenance_columns:
                db.execute(
                    f"ALTER TABLE attempt_provenance ADD COLUMN {column} {declaration}"
                )
        resume_columns = {row[1] for row in db.execute("PRAGMA table_info(resume)")}
        if "reason" not in resume_columns:
            db.execute("ALTER TABLE resume ADD COLUMN reason TEXT")
        for table in TABLES:
            columns = _column_names(table)
            if not rows[table]:
                continue
            placeholders = ", ".join("?" for _ in columns)
            sql = (
                f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) "
                f"VALUES ({placeholders})"
            )
            db.executemany(sql, [[row.get(name) for name in columns] for row in rows[table]])
        db.commit()
    return path


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def write_sql(path: str | Path, rows: dict[str, list[dict[str, Any]]]) -> Path:
    path = Path(path).expanduser().absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [schema_sql().rstrip(), "", "BEGIN TRANSACTION;"]
    for table in TABLES:
        columns = _column_names(table)
        for row in rows[table]:
            values = ", ".join(_sql_literal(row.get(name)) for name in columns)
            lines.append(
                f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) VALUES ({values});"
            )
    lines.extend(["COMMIT;", ""])
    path.write_text("\n".join(lines))
    return path


def write_csv_dir(path: str | Path, rows: dict[str, list[dict[str, Any]]]) -> Path:
    path = Path(path).expanduser().absolute()
    path.mkdir(parents=True, exist_ok=True)
    for table in TABLES:
        columns = _column_names(table)
        with (path / f"{table}.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows[table])
    return path


def export_provenance(
    sources: Iterable[str | Path],
    *,
    sqlite_path: str | Path | None = None,
    sql_path: str | Path | None = None,
    csv_dir: str | Path | None = None,
) -> tuple[list[Path], dict[str, int]]:
    if sqlite_path is None and sql_path is None and csv_dir is None:
        raise ValueError("export needs at least one of --sqlite, --sql, or --csv-dir")
    campaigns = discover_campaigns(sources)
    if not campaigns:
        raise ValueError("no yall campaigns found")
    rows = collect_rows(campaigns)
    if sqlite_path is not None:
        write_sqlite(sqlite_path, rows)
    if sql_path is not None:
        write_sql(sql_path, rows)
    if csv_dir is not None:
        write_csv_dir(csv_dir, rows)
    return campaigns, {table: len(values) for table, values in rows.items()}
