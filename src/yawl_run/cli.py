from __future__ import annotations

import argparse
import json
import shlex
import shutil
import sys

from .campaign import campaign_manifest, campaign_status, create_campaign, start_local
from .condor_backend import condor_queue_status, render_condor, submit_rendered
from .model import load_spec
from .pbs_backend import pbs_queue_status, render_pbs, submit_pbs
from .slurm_backend import render_slurm, slurm_queue_status, submit_slurm
from .worker import run_task


def _friendly_sections(parser: argparse.ArgumentParser, positional_title: str = "arguments") -> None:
    # argparse's default "positional arguments" / "optional arguments" headings
    # describe parser mechanics rather than the yawl interface.
    parser._positionals.title = positional_title
    parser._optionals.title = "options"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yawl-run",
        description="Yet Another Workflow Layer. Y'all run!",
    )
    _friendly_sections(parser, "commands")
    visible_commands = "{validate,plan,create,start,status,retry}"
    sub = parser.add_subparsers(
        dest="command",
        required=True,
        metavar=visible_commands,
    )

    validate = sub.add_parser("validate", help="validate a Yawlfile")
    _friendly_sections(validate)
    validate.add_argument("spec", nargs="?", default="Yawlfile")

    plan = sub.add_parser("plan", help="show tasks described by a Yawlfile")
    _friendly_sections(plan)
    plan.add_argument("spec", nargs="?", default="Yawlfile")
    plan_format = plan.add_mutually_exclusive_group()
    plan_format.add_argument(
        "--json",
        action="store_true",
        help="emit the expanded plan as JSON",
    )
    plan_format.add_argument(
        "--dot",
        action="store_true",
        help="emit the expanded dependency graph in Graphviz DOT format",
    )

    create = sub.add_parser("create", help="create a frozen campaign from a Yawlfile")
    _friendly_sections(create)
    create.add_argument("spec", nargs="?", default="Yawlfile")
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

    status = sub.add_parser("status", help="show campaign status")
    _friendly_sections(status)
    status.add_argument("campaign_dir")
    status.add_argument("--json", action="store_true")

    retry = sub.add_parser("retry", help="run another attempt of one task")
    _friendly_sections(retry)
    retry.add_argument("campaign_dir")
    retry.add_argument("task")

    # worker is an internal entry point used by generated scheduler jobs. Keep it
    # parseable but omit it from ordinary command listings and usage text.
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
            },
            "inputs": [
                {"role": ref.role, "path": ref.path}
                for ref in task.inputs
            ],
            "outputs": [
                {"role": ref.role, "path": ref.path}
                for ref in task.outputs
            ],
        })
    return {
        "name": spec.name,
        "backend": spec.backend,
        "source": str(spec.source),
        "tasks": tasks,
    }


def _plan_dot(spec: object) -> str:
    lines = ["digraph yawl {", "  rankdir=LR;"]
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

        if args.command == "status":
            data = campaign_status(args.campaign_dir)
            backend = data["backend"]
            if backend == "condor":
                data["scheduler"] = condor_queue_status(args.campaign_dir)
            elif backend == "slurm":
                data["scheduler"] = slurm_queue_status(args.campaign_dir)
            elif backend == "pbs":
                data["scheduler"] = pbs_queue_status(args.campaign_dir)
            if args.json:
                print(json.dumps(data, indent=2, sort_keys=True))
            else:
                print(f"Campaign {data['id']} ({backend})")
                scheduler = data.get("scheduler") or {}
                active_nodes = scheduler.get("nodes", {})
                for task in data["tasks"]:
                    suffix = ""
                    active = active_nodes.get(task["name"])
                    if active:
                        suffix = (
                            f" {backend}={active['state']} job={active['job_id']}"
                        )
                    print(
                        f"  {task['name']:<20} {task['state']:<10} "
                        f"attempts={task['attempts']}{suffix}"
                    )
                if data.get("scheduler") is not None:
                    counts = scheduler.get("counts", {})
                    node_summary = ", ".join(
                        f"{name}={count}" for name, count in sorted(counts.items())
                    ) or "no active nodes"
                    if backend == "condor":
                        dagman = scheduler.get("dagman") or "not-in-queue"
                        print(
                            f"  scheduler: dagman={dagman} "
                            f"cluster={scheduler['cluster_id']}; nodes: {node_summary}"
                        )
                    else:
                        print(f"  scheduler: {backend}; nodes: {node_summary}")
            return 0

        if args.command in {"retry", "worker"}:
            return run_task(args.campaign_dir, args.task)

    except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"yawl-run: {exc}", file=sys.stderr)
        return 2

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
