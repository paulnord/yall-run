#!/usr/bin/env python3

from __future__ import annotations

import argparse
from fractions import Fraction
import math
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Check a rational approximation to sqrt(2).")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    numerator, denominator = (int(value) for value in args.input.read_text().split())
    value = Fraction(numerator, denominator)
    approximation = float(value)
    error = approximation - math.sqrt(2.0)

    args.output.write_text(
        f"convergent={value.numerator}/{value.denominator}\n"
        f"sqrt2={approximation:.17g}\n"
        f"error={error:.17g}\n"
    )
    print(f"sqrt(2) = {approximation:.17g}; error = {error:.3e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
