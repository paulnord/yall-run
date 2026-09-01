from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .campaign import campaign_status, run_task, start_campaign
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

    start = sub.add_parser("start", help="create and run a campaign")
    start.add_argument("spec")
    start.add_argument("--root", default="./campaigns")

    status = sub.add_parser("status", help="show campaign status")
    status.add_argument("campaign_dir")
    status.add_argument("--json", action="store_true")

    retry = sub.add_parser("retry", help="run another attempt of one task")
    retry.add_argument("campaign_dir")
    retry.add_argument("task")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate":
            spec = load_spec(args.spec)
            print(f"valid: {spec.name} ({len(spec.tasks)} tasks)")
            return 0

        if args.command == "plan":
            spec = load_spec(args.spec)
            print(f"Campaign: {spec.name}")
            for task in spec.tasks:
                print(f"  {task.name}: {task.command}")
            return 0

        if args.command == "start":
            spec = load_spec(args.spec)
            cdir = start_campaign(spec, args.root)
            print(cdir)
            return 0

        if args.command == "status":
            data = campaign_status(args.campaign_dir)
            if args.json:
                print(json.dumps(data, indent=2, sort_keys=True))
            else:
                print(f"Campaign {data['id']}")
                for task in data["tasks"]:
                    print(f"  {task['name']:<20} {task['state']:<10} attempts={task['attempts']}")
            return 0

        if args.command == "retry":
            rc = run_task(args.campaign_dir, args.task)
            return rc

    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"yawl-run: {exc}", file=sys.stderr)
        return 2

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
