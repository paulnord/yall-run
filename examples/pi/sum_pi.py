#!/usr/bin/env python3

from __future__ import annotations

import argparse
import math
from pathlib import Path


def read_partial(path: Path) -> tuple[int, int, float]:
    fields = path.read_text().split()
    if len(fields) != 3:
        raise ValueError(f"{path}: expected START STOP PARTIAL_SUM")
    return int(fields[0]), int(fields[1]), float(fields[2])


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Combine Leibniz partial sums into a pi estimate."
    )
    parser.add_argument("partials", nargs="+", type=Path)
    parser.add_argument("-o", "--output", required=True, type=Path)
    args = parser.parse_args()

    pieces = sorted((read_partial(path), path) for path in args.partials)
    expected_start = 0
    values: list[float] = []
    for (start, stop, value), path in pieces:
        if start != expected_start:
            raise ValueError(
                f"{path}: expected interval starting at {expected_start}, got {start}"
            )
        if stop <= start:
            raise ValueError(f"{path}: invalid interval {start}:{stop}")
        expected_start = stop
        values.append(value)

    estimate = 4.0 * math.fsum(values)
    error = estimate - math.pi
    args.output.write_text(
        f"terms={expected_start}\n"
        f"pi={estimate:.16f}\n"
        f"error={error:.16e}\n"
    )
    print(f"pi = {estimate:.16f}")
    print(f"error = {error:.3e} from {expected_start} terms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
