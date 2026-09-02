#!/usr/bin/env python3

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize shared Golomb search state.")
    parser.add_argument("--order", type=int, required=True)
    parser.add_argument("--limit", type=int, required=True)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    if args.order < 2:
        raise ValueError("order must be at least 2")
    if args.limit <= args.order - 1:
        raise ValueError("limit is too small for the requested order")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema": 1,
        "order": args.order,
        "initial_limit": args.limit,
        "best_length": args.limit,
        "ruler": None,
        "revision": 0,
        "found_by": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    args.output.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    args.output.with_suffix(args.output.suffix + ".lock").touch()
    print(f"order={args.order} initial_limit={args.limit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
