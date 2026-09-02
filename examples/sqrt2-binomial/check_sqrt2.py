#!/usr/bin/env python3

from __future__ import annotations

import argparse
import math
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the binomial sqrt(2) estimate.")
    parser.add_argument("result", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    values = dict(
        line.split("=", 1)
        for line in args.result.read_text().splitlines()
        if "=" in line
    )
    estimate = float(values["sqrt2"])
    error = estimate - math.sqrt(2.0)
    tolerance = 5.0e-8
    args.output.write_text(
        f"sqrt2={estimate:.17g}\n"
        f"reference={math.sqrt(2.0):.17g}\n"
        f"error={error:.17g}\n"
        f"tolerance={tolerance:.17g}\n"
    )
    if abs(error) > tolerance:
        raise SystemExit(
            f"sqrt2 estimate outside tolerance: error={error:.3e}, tolerance={tolerance:.3e}"
        )
    print(f"sqrt2 error = {error:.3e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
