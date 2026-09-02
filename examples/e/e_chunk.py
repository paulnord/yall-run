#!/usr/bin/env python3

from __future__ import annotations

import argparse
from fractions import Fraction
import math
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute one exact chunk of the series for e.")
    parser.add_argument("start", type=int)
    parser.add_argument("stop", type=int)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    if args.start < 0 or args.stop <= args.start:
        raise ValueError(f"invalid range {args.start}:{args.stop}")

    value = sum(
        (Fraction(1, math.factorial(n)) for n in range(args.start, args.stop)),
        Fraction(0, 1),
    )
    terms = args.stop - args.start
    args.output.write_text(
        f"terms={terms}\n"
        f"numerator={value.numerator}\n"
        f"denominator={value.denominator}\n"
    )
    print(f"terms {args.start}:{args.stop} -> {value.numerator}/{value.denominator}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
