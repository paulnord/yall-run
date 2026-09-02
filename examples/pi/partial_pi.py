#!/usr/bin/env python3

from __future__ import annotations

import argparse
import math
from pathlib import Path


def read_range(path: Path) -> tuple[int, int]:
    fields = path.read_text().split()
    if len(fields) != 2:
        raise ValueError(f"{path}: expected two integers: START STOP")
    start, stop = (int(value) for value in fields)
    if start < 0 or stop <= start:
        raise ValueError(f"{path}: invalid range {start} {stop}")
    return start, stop


def leibniz_partial(start: int, stop: int) -> float:
    return math.fsum(
        (1.0 if n % 2 == 0 else -1.0) / (2 * n + 1)
        for n in range(start, stop)
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compute one interval of the Leibniz series for pi."
    )
    parser.add_argument("range_file", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    start, stop = read_range(args.range_file)
    value = leibniz_partial(start, stop)
    args.output.write_text(f"{start} {stop} {value:.17g}\n")
    print(f"terms {start}:{stop} -> {value:.17g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
