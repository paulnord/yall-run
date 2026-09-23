from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import shlex
import shutil
import sqlite3
import sys

from .amend import amend_campaign
from .campaign import (
    campaign_manifest,
    campaign_status,
    create_campaign,
    retry_task,
    run_postflight,
    start_local,
)
from .condor_backend import render_condor, submit_rendered
from .export import export_provenance
from .model import load_spec
from .pbs_backend import render_pbs, submit_pbs
from .slurm_backend import render_slurm, submit_slurm
from .recovery import (
    QUEUED_BACKENDS,
    condor_history_snapshot,
    reconcile_status,
    resume_campaign,
)
from .status_view import render_status
from .worker import run_task
from .walltime import effective_walltime, format_walltime


class _VersionAction(argparse.Action):
    def __init__(self, option_strings, dest, **kwargs):
        super().__init__(option_strings=option_strings, dest=dest, nargs=0, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None):
        from .version import display_version

        parser._print_message(display_version() + "\n", sys.stdout)
        parser.exit()


def _friendly_sections(parser: argparse.ArgumentParser, positional_title: str = "arguments") -> None:
    parser._positionals.title = positional_title
    parser._optionals.title = "options"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yall-run",
        description="Yet Another Launch Layer. Y'all run!",
    )
    _friendly_sections(parser, "commands")
    parser.add_argument(
        "-V", "--version", action=_VersionAction,
        help="show package version and checkout commit when available",
    )
    visible_commands = "{validate,plan,create,start,resume,amend,status,retry,export}"
    sub = parser.add_subparsers(
        dest="command",
        required=True,
        metavar=visible_commands,
    )

    validate = sub.add_parser("validate", help="validate a Yallfile")
    _friendly_sections(validate)
    validate.add_argument("spec", nargs="?", default="Yallfile")

    plan = sub.add_parser("plan", help="show tasks described by a Yallfile")
    _friendly_sections(plan)
    plan.add_argument("spec", nargs="?", default="Yallfile")
    plan_format = plan.add_mutually_exclusive_group()
    plan_format.add_argument("--json", action="store_true", help="emit the expanded plan as JSON")
    plan_format.add_argument("--dot", action="store_true", help="emit the expanded dependency graph in Graphviz DOT format")

    create = sub.add_parser("create", help="create a frozen campaign from a Yallfile")
    _friendly_sections(create)
    create.add_argument("spec", nargs="?", default="Yallfile")
    create.add_argument(
        "--campaigns-dir",
        default="./campaigns",
        help="directory that will contain newly created campaign directories",
    )
    create.add_argument("--backend", choices=("local", "condor", "slurm", "pbs"))
    create.add_argument(
        "-j",
        "--jobs",
        type=int,
        help="local backend only: maximum concurrent tasks, frozen into the campaign",
    )

    start = sub.add_parser("start", help="start one existing campaign")
    _friendly_sections(start)
    start.add_argument(
        "campaign_dir",
        nargs="?",
        metavar="CAMPAIGN_DIR",
        help="campaign directory; if omitted, read one path from stdin",
    )
    start.add_argument(
        "--overwrite",
        action="store_true",
        help="permit all tasks in this start to use pre-existing declared outputs",
    )

    resume = sub.add_parser("resume", help="continue one started campaign")
    _friendly_sections(resume)
    resume.add_argument("campaign_dir")
    resume.add_argument(
        "--dry-run", action="store_true",
        help="queued backends: inspect recovery without submitting or cancelling jobs",
    )
    resume.add_argument(
        "--cancel-pending", action="store_true",
        help="Slurm/PBS: cancel surviving pending/held jobs before rebuilding dependencies",
    )
    resume.add_argument(
        "--overwrite", action="store_true",
        help="delete existing outputs of unfinished tasks before retrying",
    )
    resume.add_argument(
        "-y", "--yes", action="store_true",
        help="confirm --overwrite without an interactive prompt",
    )
    resume.add_argument(
        "--reason",
        help="optional human explanation stored with the resume record",
    )

    amend = sub.add_parser(
        "amend",
        help="compare an edited Yallfile and record safe changes to unfinished tasks",
    )
    _friendly_sections(amend)
    amend.add_argument("campaign_dir")
    amend.add_argument(
        "--from",
        dest="spec_path",
        metavar="PATH",
        help="edited Yallfile; defaults to the campaign's recorded source path",
    )
    amend_mode = amend.add_mutually_exclusive_group()
    amend_mode.add_argument(
        "--dry-run",
        action="store_true",
        help="show the proposed amendment without writing it",
    )
    amend_mode.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="record the amendment without interactive confirmation",
    )
    amend.add_argument(
        "--reason",
        help="optional human explanation stored with the amendment",
    )

    status = sub.add_parser("status", help="show campaign status")
    _friendly_sections(status)
    status.add_argument("campaign_dir")
    status.add_argument("--json", action="store_true")
    status.add_argument(
        "-v", "--verbose", action="count", default=0,
        help="add failure diagnostics; repeat up to -vvvv for execution trace",
    )

    retry = sub.add_parser("retry", help="run one more attempt of a failed local task")
    _friendly_sections(retry)
    retry.add_argument("campaign_dir")
    retry.add_argument("task")

    postflight = sub.add_parser(
        "postflight",
        help=argparse.SUPPRESS,
    )
    _friendly_sections(postflight)
    postflight.add_argument("campaign_dir")

    export = sub.add_parser("export", help="export campaign provenance to relational files")
    _friendly_sections(export)
    export.add_argument(
        "sources",
        nargs="+",
        metavar="CAMPAIGN_OR_DIR",
        help="campaign directory or directory tree containing campaigns",
    )
    export.add_argument("--sqlite", metavar="FILE", help="write or update a SQLite database")
    export.add_argument("--sql", metavar="FILE", help="write SQLite-compatible SQL")
    export.add_argument("--csv-dir", metavar="DIR", help="write one CSV file per table")

    worker = sub.add_parser("worker", help=argparse.SUPPRESS)
    _friendly_sections(worker)
    worker.add_argument("campaign_dir")
    worker.add_argument("task")
    sub._choices_actions[:] = [
        action for action in sub._choices_actions if action.dest != "worker"
    ]

    return parser


def _display_command(command: object) -> str:
    if isinstance(command, str):
        return command
    return shlex.join(str(item) for item in command)


def _plan_json(spec: object) -> dict[str, object]:
    tasks = []
    for task in spec.tasks:
        tasks.append({
            "name": task.name,
            "parents": list(task.parents),
            "command": task.command if isinstance(task.command, str) else list(task.command),
            "cwd": task.cwd,
            "retries": task.retries,
            "overwrite": task.overwrite,
            "resources": {
                "cpus": task.resources.cpus,
                "memory": task.resources.memory,
                "disk": task.resources.disk,
                "walltime_seconds": effective_walltime(
                    task.resources.walltime_seconds, spec.condor.request_walltime_seconds
                ),
            },
            "inputs": [{"role": ref.role, "path": ref.path} for ref in task.inputs],
            "outputs": [{"role": ref.role, "path": ref.path} for ref in task.outputs],
        })
    return {
        "name": spec.name,
        "backend": spec.backend,
        "source": str(spec.source),
        "execution": asdict(spec.execution),
        "preflight": [
            {"command": command if isinstance(command, str) else list(command),
             "cwd": str(spec.source.parent)}
            for command in spec.preflight
        ],
        "postflight": [
            {"command": command if isinstance(command, str) else list(command),
             "cwd": "campaign_dir"}
            for command in spec.postflight
        ],
        "tasks": tasks,
    }


def _plan_dot(spec: object) -> str:
    lines = ["digraph yall {", "  rankdir=LR;"]
    for task in spec.tasks:
        lines.append(f"  {json.dumps(task.name)};")
    for task in spec.tasks:
        for parent in task.parents:
            lines.append(f"  {json.dumps(parent)} -> {json.dumps(task.name)};")
    lines.append("}")
    return "\n".join(lines)


def _require_commands(*names: str) -> None:
    missing = [name for name in names if shutil.which(name) is None]
    if missing:
        raise ValueError(f"required command not found in PATH: {', '.join(missing)}")


def _campaign_dir_argument(value: str | None) -> str:
    if value:
        return value
    if getattr(sys.stdin, "isatty", lambda: False)():
        raise ValueError("CAMPAIGN_DIR is required, or pipe one campaign path to start")
    lines = [line.strip() for line in sys.stdin if line.strip()]
    if not lines:
        raise ValueError("no campaign path received on stdin")
    if len(lines) != 1:
        raise ValueError("expected exactly one campaign path on stdin")
    return lines[0]


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            spec = load_spec(args.spec)
            print(f"valid: {spec.name} ({len(spec.tasks)} tasks, backend={spec.backend})")
            return 0

        if args.command == "plan":
            spec = load_spec(args.spec)
            if args.json:
                print(json.dumps(_plan_json(spec), indent=2, sort_keys=True))
                return 0
            if args.dot:
                print(_plan_dot(spec))
                return 0
            print(f"Campaign: {spec.name} (backend={spec.backend})")
            if spec.preflight:
                print(f"Host preflight (during create, cwd={spec.source.parent}):")
                for index, command in enumerate(spec.preflight, 1):
                    prefix = "! " if isinstance(command, str) else ""
                    print(f"  {index}: {prefix}{_display_command(command)}")
            if spec.postflight:
                print("Host postflight (after campaign completion):")
                for index, command in enumerate(spec.postflight, 1):
                    prefix = "! " if isinstance(command, str) else ""
                    print(f"  {index}: {prefix}{_display_command(command)}")
            if spec.execution.wrapper:
                wrapper = shlex.join([spec.execution.wrapper, *spec.execution.wrapper_args])
                print(f"Payload wrapper: {wrapper}")
            for task in spec.tasks:
                extras = []
                if task.parents:
                    extras.append("after=" + ",".join(task.parents))
                if task.retries:
                    extras.append(f"retries={task.retries}")
                if task.resources.cpus is not None:
                    extras.append(f"cpus={task.resources.cpus}")
                if task.resources.memory is not None:
                    extras.append(f"memory={task.resources.memory}")
                if task.resources.disk is not None:
                    extras.append(f"disk={task.resources.disk}")
                walltime = effective_walltime(
                    task.resources.walltime_seconds, spec.condor.request_walltime_seconds
                )
                if walltime is not None:
                    extras.append(f"time={format_walltime(walltime)}")
                if task.overwrite:
                    extras.append("overwrite")
                suffix = f" [{' '.join(extras)}]" if extras else ""
                print(f"  {task.name}: {_display_command(task.command)}{suffix}")
            return 0

        if args.command == "create":
            if args.jobs is not None and args.jobs < 1:
                raise ValueError("-j/--jobs must be positive")
            spec = load_spec(args.spec)
            backend = args.backend or spec.backend
            if backend != "local" and args.jobs is not None:
                raise ValueError("-j/--jobs is only valid for the local backend")
            if backend == "condor":
                cdir = render_condor(spec, args.campaigns_dir)
            elif backend == "slurm":
                cdir = render_slurm(spec, args.campaigns_dir)
            elif backend == "pbs":
                cdir = render_pbs(spec, args.campaigns_dir)
            elif backend == "local":
                cdir = create_campaign(
                    spec,
                    args.campaigns_dir,
                    backend="local",
                    local_jobs=args.jobs,
                )
            else:
                raise ValueError(f"unknown campaign backend: {backend}")
            print(cdir)
            return 0

        if args.command == "start":
            campaign_dir = _campaign_dir_argument(args.campaign_dir)
            cdir, manifest = campaign_manifest(campaign_dir)
            backend = manifest.get("backend", "local")
            if backend == "local":
                start_local(cdir, overwrite=args.overwrite)
            elif backend == "condor":
                _require_commands("condor_submit_dag")
                submit_rendered(cdir, overwrite=args.overwrite)
            elif backend == "slurm":
                _require_commands("sbatch", "scontrol")
                submit_slurm(cdir, overwrite=args.overwrite)
            elif backend == "pbs":
                _require_commands("qsub", "qrls")
                submit_pbs(cdir, overwrite=args.overwrite)
            else:
                raise ValueError(f"unknown campaign backend: {backend}")
            return 0

        if args.command == "resume":
            return resume_campaign(
                args.campaign_dir, dry_run=args.dry_run,
                cancel_pending=args.cancel_pending, reason=args.reason,
                overwrite=args.overwrite, yes=args.yes,
            )

        if args.command == "amend":
            proposal = amend_campaign(
                args.campaign_dir,
                spec_path=args.spec_path,
                dry_run=True,
                reason=args.reason,
            )
            if not proposal["changes"]:
                print("[amend] no semantic changes to the frozen workflow")
                return 0
            number = int(proposal["number"])
            print(f"[amend] proposed amendment {number:04d}")
            for change in proposal["changes"]:
                print(
                    f"  {change['task']}: {change['state']} "
                    f"attempts={change['attempts']} command changed"
                )
                print(f"    - {_display_command(change['before'])}")
                print(f"    + {_display_command(change['after'])}")
            if args.dry_run:
                print("[amend] dry run: no files written")
                return 0
            if not args.yes:
                if not getattr(sys.stdin, "isatty", lambda: False)():
                    raise ValueError(
                        "amend needs confirmation on an interactive terminal; "
                        "use --yes to record or --dry-run to preview"
                    )
                answer = input(f"[amend] record amendment {number:04d}? [y/N] ")
                if answer.strip().lower() not in {"y", "yes"}:
                    print("[amend] no amendment written")
                    return 0
            result = amend_campaign(
                args.campaign_dir,
                spec_path=args.spec_path,
                dry_run=False,
                reason=args.reason,
            )
            print(f"[amend] wrote {result['path']}")
            return 0

        if args.command == "status":
            if args.verbose > 4:
                raise ValueError("status verbosity supports at most -vvvv")
            if args.json and args.verbose:
                raise ValueError("status verbosity is text-only; use --json without -v")
            data = campaign_status(args.campaign_dir)
            backend = data["backend"]
            if backend in QUEUED_BACKENDS:
                data = reconcile_status(args.campaign_dir, data)
            if args.verbose >= 3 and backend == "condor":
                data["scheduler_history"] = condor_history_snapshot(args.campaign_dir)
            if args.json:
                print(json.dumps(data, indent=2, sort_keys=True))
            else:
                print(render_status(args.campaign_dir, data, verbosity=args.verbose))
            return 0

        if args.command == "retry":
            return retry_task(args.campaign_dir, args.task)

        if args.command == "postflight":
            run_postflight(args.campaign_dir)
            print(f"[postflight] completed {args.campaign_dir}")
            return 0

        if args.command == "export":
            campaigns, counts = export_provenance(
                args.sources,
                sqlite_path=args.sqlite,
                sql_path=args.sql,
                csv_dir=args.csv_dir,
            )
            print(f"exported {len(campaigns)} campaign(s)")
            nonzero = [f"{name}={count}" for name, count in counts.items() if count]
            if nonzero:
                print("rows: " + " ".join(nonzero))
            return 0

        if args.command == "worker":
            return run_task(args.campaign_dir, args.task)

    except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError, sqlite3.Error) as exc:
        print(f"yall-run: {exc}", file=sys.stderr)
        return 2

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
