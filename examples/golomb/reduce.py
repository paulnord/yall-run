#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Reduce completed Golomb search shards.")
    parser.add_argument("incumbent", type=Path)
    parser.add_argument("results", nargs="+", type=Path)
    parser.add_argument("-o", "--output", required=True, type=Path)
    args = parser.parse_args()

    incumbent = json.loads(args.incumbent.read_text())
    ruler = incumbent.get("ruler")
    if not ruler:
        raise ValueError("search completed without finding a ruler")

    order = int(incumbent["order"])
    best_length = int(incumbent["best_length"])
    initial_limit = int(incumbent["initial_limit"])
    results = [json.loads(path.read_text()) for path in args.results]
    if not results:
        raise ValueError("no search results")

    shard_counts = {int(result["shard_count"]) for result in results}
    if len(shard_counts) != 1:
        raise ValueError("search results disagree about shard count")
    shard_count = shard_counts.pop()
    shard_ids = {int(result["shard_id"]) for result in results}
    if shard_ids != set(range(shard_count)):
        raise ValueError(
            f"incomplete shard set: got {sorted(shard_ids)}, expected 0..{shard_count - 1}"
        )

    for result in results:
        if not result.get("completed"):
            raise ValueError(f"shard {result['shard_id']} did not complete")
        if int(result["order"]) != order:
            raise ValueError("search results disagree about ruler order")
        if int(result["initial_limit"]) != initial_limit:
            raise ValueError("search results disagree about initial limit")
        if int(result["exhausted_below"]) < best_length:
            raise ValueError(
                f"shard {result['shard_id']} only exhausted below "
                f"{result['exhausted_below']}, but best length is {best_length}"
            )

    # To beat a length-L ruler, the first nonzero mark can be at most
    # L - order + 1. Confirm that every such first mark was assigned and visited.
    required_first_marks = set(range(1, best_length - order + 2))
    searched_first_marks = {
        int(first)
        for result in results
        for first in result.get("first_marks", [])
    }
    missing = sorted(required_first_marks - searched_first_marks)
    if missing:
        raise ValueError(f"search did not cover first marks needed for proof: {missing}")

    summary = {
        "schema": 1,
        "order": order,
        "length": best_length,
        "ruler": ruler,
        "initial_limit": initial_limit,
        "shards": shard_count,
        "optimality_established": True,
        "nodes": sum(int(result["nodes"]) for result in results),
        "duplicate_rejections": sum(
            int(result["duplicate_rejections"]) for result in results
        ),
        "solutions": sum(int(result["solutions"]) for result in results),
        "improvements": sum(int(result["improvements"]) for result in results),
        "incumbent_revision": int(incumbent.get("revision", 0)),
        "found_by": incumbent.get("found_by"),
        "shard_results": sorted(results, key=lambda result: int(result["shard_id"])),
    }
    args.output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(
        f"best length={best_length} ruler={','.join(map(str, ruler))} "
        f"nodes={summary['nodes']} shards={shard_count} optimal=yes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
