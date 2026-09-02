#!/usr/bin/env python3

from __future__ import annotations

import argparse
from fractions import Fraction
from pathlib import Path


def read_fraction(path: Path) -> Fraction:
    fields = path.read_text().split()
    if len(fields) != 2:
        raise ValueError(f"{path}: expected NUMERATOR DENOMINATOR")
    numerator, denominator = (int(value) for value in fields)
    return Fraction(numerator, denominator)


def write_fraction(path: Path, value: Fraction) -> None:
    path.write_text(f"{value.numerator} {value.denominator}\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate exact rational convergents of sqrt(2)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    seed = sub.add_parser("seed")
    seed.add_argument("output", type=Path)

    step = sub.add_parser("next")
    step.add_argument("input", type=Path)
    step.add_argument("output", type=Path)

    args = parser.parse_args()

    if args.command == "seed":
        value = Fraction(1, 1)
    else:
        previous = read_fraction(args.input)
        value = 1 + Fraction(1, previous + 1)

    write_fraction(args.output, value)
    print(f"{value.numerator}/{value.denominator} = {float(value):.16g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
