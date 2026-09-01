from __future__ import annotations

import argparse
import json
import shutil
import sys

from .backend import condor_queue_status, submit_condor
from .campaign import campaign_status, run_task, start_local
from .model import load_spec


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yawl-run",
        description="Yet Another Workflow Layer. Y'all run!",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="validate a campaign specification")
    validate.add_argument("spec")

    plan = sub.add_parser("plan", help="show tasks without running them")
    plan.add_argument("spec")

    start = sub.add_parser("start", help="create and run or submit a campaign")
    start.add_argument("spec")
    start.add_argument("--root", default="./campaigns")
    start.add_argument("--backend", choices=("local", "condor"))
    start.add_argument(
        "--dry-run",
        action="store_true",
        help="for Condor, render campaign/DAG files without submitting",
    )

    status = sub.add_parser("status", help="show campaign status")
    status.add_argument("campaign_dir")
    status.add_argument("--json", action="store_true")

    retry = sub.add_parser("retry", help="run another local attempt of one task")
    retry.add_argument("campaign_dir")
    retry.add_argument("task")

    worker = sub.add_parser("worker", help=argparse.SUPPRESS)
    worker.add_argument("campaign_dir")
    worker.add_argument("task")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            spec = load_spec(args.spec)
            print(f"valid: {spec.name} ({len(spec.tasks)} tasks, backend={spec.backend})")
            return 0

        if args.command == "plan":
            spec = load_spec(args.spec)
            print(f"Campaign: {spec.name} (backend={spec.backend})")
            for task in spec.tasks:
                extras = []
                if task.parents:
                    extras.append("after=" + ",".join(task.parents))
                if task.retries:
                    extras.append(f"retries={task.retries}")
                suffix = f" [{' '.join(extras)}]" if extras else ""
                print(f"  {task.name}: {task.command}{suffix}")
            return 0

        if args.command == "start":
            spec = load_spec(args.spec)
            backend = args.backend or spec.backend
            if backend == "local":
                if args.dry_run:
                    raise ValueError("--dry-run currently applies only to the Condor backend")
                cdir = start_local(spec, args.root)
            else:
                if not args.dry_run and shutil.which("condor_submit_dag") is None:
                    raise ValueError("condor_submit_dag not found in PATH")
                cdir = submit_condor(spec, args.root, submit=not args.dry_run)
            print(cdir)
            return 0

        if args.command == "status":
            data = campaign_status(args.campaign_dir)
            if data["backend"] == "condor":
                data["scheduler"] = condor_queue_status(args.campaign_dir)
            if args.json:
                print(json.dumps(data, indent=2, sort_keys=True))
            else:
                print(f"Campaign {data['id']} ({data['backend']})")
                for task in data["tasks"]:
                    print(f"  {task['name']:<20} {task['state']:<10} attempts={task['attempts']}")
                if data.get("scheduler") is not None:
                    scheduler = ", ".join(
                        f"{name}={count}"
                        for name, count in sorted(data["scheduler"].items())
                    ) or "no active DAGMan jobs"
                    print(f"  scheduler: {scheduler}")
            return 0

        if args.command in {"retry", "worker"}:
            return run_task(args.campaign_dir, args.task)

    except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"yawl-run: {exc}", file=sys.stderr)
        return 2

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
