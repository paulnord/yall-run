#!/usr/bin/env python3

from __future__ import annotations

import argparse
import math
from pathlib import Path


def read_partial(path: Path) -> tuple[int, int, float]:
    fields = path.read_text().split()
    if len(fields) != 3:
        raise ValueError(f"{path}: expected START STOP VALUE")
    return int(fields[0]), int(fields[1]), float(fields[2])


def main() -> int:
    parser = argparse.ArgumentParser(description="Combine binomial-series chunks.")
    parser.add_argument("partials", nargs="+", type=Path)
    parser.add_argument("-o", "--output", required=True, type=Path)
    args = parser.parse_args()

    records = sorted(read_partial(path) for path in args.partials)
    expected = 0
    for start, stop, _ in records:
        if start != expected:
            raise ValueError(f"non-contiguous term ranges: expected {expected}, got {start}")
        expected = stop

    value = math.fsum(record[2] for record in records)
    args.output.write_text(
        f"terms={expected}\n"
        f"sqrt2={value:.17g}\n"
        f"error={value - math.sqrt(2.0):.17g}\n"
    )
    print(f"sqrt2 = {value:.17g} from {expected} terms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
