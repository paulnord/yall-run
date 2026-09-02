#!/usr/bin/env python3

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import time

REFRESH_NODES = 1_000_000


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def atomic_write_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def update_incumbent(path: Path, ruler: list[int], shard_id: int) -> dict:
    """Install ruler if it improves the shared incumbent, returning current state."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        current = read_json(path)
        length = ruler[-1]
        if length < int(current["best_length"]):
            current["best_length"] = length
            current["ruler"] = ruler
            current["revision"] = int(current.get("revision", 0)) + 1
            current["found_by"] = shard_id
            current["updated_at"] = datetime.now(timezone.utc).isoformat()
            atomic_write_json(path, current)
            print(
                f"shard={shard_id:02d} new incumbent length={length} "
                f"ruler={','.join(map(str, ruler))}",
                flush=True,
            )
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return current


def parse_shard(path: Path) -> tuple[int, int]:
    fields = path.read_text().split()
    if len(fields) != 2:
        raise ValueError(f"{path}: expected SHARD_ID SHARD_COUNT")
    shard_id, shard_count = map(int, fields)
    if shard_count < 1 or not 0 <= shard_id < shard_count:
        raise ValueError(f"{path}: invalid shard {shard_id}/{shard_count}")
    return shard_id, shard_count


def search_shard(shard_file: Path, incumbent_path: Path, output: Path) -> dict:
    shard_id, shard_count = parse_shard(shard_file)
    initial = read_json(incumbent_path)
    order = int(initial["order"])
    initial_limit = int(initial["initial_limit"])
    bound = int(initial["best_length"])

    nodes = 0
    duplicate_rejections = 0
    symmetry_rejections = 0
    refreshes = 0
    solutions = 0
    improvements = 0
    started = time.perf_counter()

    def refresh_bound() -> None:
        nonlocal bound, refreshes
        current = read_json(incumbent_path)
        refreshes += 1
        bound = min(bound, int(current["best_length"]))

    def visit(marks: list[int], used: set[int]) -> None:
        nonlocal nodes, duplicate_rejections, symmetry_rejections
        nonlocal solutions, improvements, bound

        nodes += 1
        if nodes % REFRESH_NODES == 0:
            refresh_bound()

        remaining = order - len(marks)
        if remaining == 0:
            # Search only one orientation of each ruler.  Reflection exchanges
            # the first and last gaps, so first_gap <= last_gap is sufficient.
            if marks[1] > marks[-1] - marks[-2]:
                symmetry_rejections += 1
                return
            solutions += 1
            previous = bound
            current = update_incumbent(incumbent_path, marks, shard_id)
            bound = min(bound, int(current["best_length"]))
            if bound < previous:
                improvements += 1
            return

        # We search only for a ruler strictly shorter than the incumbent.
        # Leave at least one unit for every mark still to be placed.
        max_next = bound - remaining
        last = marks[-1]
        if last >= max_next:
            return

        for candidate in range(last + 1, max_next + 1):
            # The bound can decrease while this loop is active.
            if candidate > bound - remaining:
                break
            distances = [candidate - mark for mark in marks]
            if len(set(distances)) != len(distances) or any(
                distance in used for distance in distances
            ):
                duplicate_rejections += 1
                continue
            visit(marks + [candidate], used.union(distances))

    # Partition by the first nonzero mark.  Every possible first mark belongs
    # to exactly one shard, so completion of all shards proves global coverage.
    max_first = initial_limit - (order - 1)
    first_marks = []
    for first in range(1, max_first + 1):
        if (first - 1) % shard_count != shard_id:
            continue
        if first > bound - (order - 1):
            break
        first_marks.append(first)
        visit([0, first], {first})

    # A final read is not required for correctness, but records the strongest
    # bound this shard knows it exhausted.  A larger value is also safe: it
    # means the shard searched an even larger domain before another worker won.
    refresh_bound()
    elapsed = time.perf_counter() - started
    result = {
        "schema": 1,
        "order": order,
        "shard_id": shard_id,
        "shard_count": shard_count,
        "first_marks": first_marks,
        "initial_limit": initial_limit,
        "exhausted_below": bound,
        "nodes": nodes,
        "duplicate_rejections": duplicate_rejections,
        "symmetry_rejections": symmetry_rejections,
        "solutions": solutions,
        "improvements": improvements,
        "incumbent_refreshes": refreshes,
        "elapsed_seconds": elapsed,
        "completed": True,
    }
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(
        f"shard={shard_id:02d}/{shard_count} nodes={nodes} "
        f"solutions={solutions} exhausted_below={bound} elapsed={elapsed:.2f}s"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Search one shard of a Golomb ruler tree.")
    parser.add_argument("shard", type=Path)
    parser.add_argument("incumbent", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    search_shard(args.shard, args.incumbent, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
