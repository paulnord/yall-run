#!/usr/bin/env python3

from __future__ import annotations

import argparse
from fractions import Fraction
import math
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the reduced series approximation to e.")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    values = dict(
        line.split("=", 1)
        for line in args.input.read_text().splitlines()
        if line.strip()
    )
    terms = int(values["terms"])
    exact = Fraction(int(values["numerator"]), int(values["denominator"]))
    approximation = float(exact)
    error = approximation - math.e

    args.output.write_text(
        f"terms={terms}\n"
        f"e={approximation:.17g}\n"
        f"error={error:.17g}\n"
    )
    print(f"e = {approximation:.17g} from {terms} terms; error = {error:.3e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
