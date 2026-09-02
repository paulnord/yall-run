#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from pathlib import Path


def verify_ruler(marks: list[int]) -> list[int]:
    if not marks or marks[0] != 0:
        raise ValueError("ruler must start at zero")
    if any(right <= left for left, right in zip(marks, marks[1:])):
        raise ValueError("ruler marks must be strictly increasing")

    distances = [
        marks[j] - marks[i]
        for i in range(len(marks))
        for j in range(i + 1, len(marks))
    ]
    if len(set(distances)) != len(distances):
        raise ValueError("ruler contains repeated pairwise distances")
    return distances


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a reduced Golomb ruler result.")
    parser.add_argument("best", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    result = json.loads(args.best.read_text())
    marks = [int(mark) for mark in result["ruler"]]
    order = int(result["order"])
    length = int(result["length"])

    if len(marks) != order:
        raise ValueError(f"expected {order} marks, found {len(marks)}")
    if marks[-1] != length:
        raise ValueError("reported length does not match final mark")
    if not result.get("optimality_established"):
        raise ValueError("search reducer did not establish optimality")

    distances = verify_ruler(marks)
    expected = order * (order - 1) // 2
    if len(distances) != expected:
        raise ValueError("unexpected pairwise-distance count")

    report = (
        f"order={order}\n"
        f"length={length}\n"
        f"ruler={' '.join(map(str, marks))}\n"
        f"distinct_distances={len(distances)}\n"
        f"nodes={result['nodes']}\n"
        f"shards={result['shards']}\n"
        "optimality_established=yes\n"
    )
    args.output.write_text(report)
    print(report, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
