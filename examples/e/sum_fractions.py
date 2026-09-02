#!/usr/bin/env python3

from __future__ import annotations

import argparse
from fractions import Fraction
from pathlib import Path


def read_record(path: Path) -> tuple[int, Fraction]:
    values = dict(
        line.split("=", 1)
        for line in path.read_text().splitlines()
        if line.strip()
    )
    terms = int(values["terms"])
    value = Fraction(int(values["numerator"]), int(values["denominator"]))
    return terms, value


def main() -> int:
    parser = argparse.ArgumentParser(description="Add exact rational partial sums.")
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("-o", "--output", required=True, type=Path)
    args = parser.parse_args()

    total_terms = 0
    total = Fraction(0, 1)
    for path in args.inputs:
        terms, value = read_record(path)
        total_terms += terms
        total += value

    args.output.write_text(
        f"terms={total_terms}\n"
        f"numerator={total.numerator}\n"
        f"denominator={total.denominator}\n"
    )
    print(
        f"combined {len(args.inputs)} inputs, {total_terms} terms -> "
        f"{total.numerator}/{total.denominator}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
