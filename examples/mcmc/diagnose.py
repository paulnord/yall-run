#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import fmean

import ROOT


def sample_variance(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = fmean(values)
    return sum((value - mean) ** 2 for value in values) / (len(values) - 1)


def quantile(sorted_values: list[float], probability: float) -> float:
    if not sorted_values:
        raise ValueError("cannot calculate a quantile of an empty sample")
    if probability <= 0:
        return sorted_values[0]
    if probability >= 1:
        return sorted_values[-1]
    position = probability * (len(sorted_values) - 1)
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return sorted_values[low]
    fraction = position - low
    return sorted_values[low] * (1.0 - fraction) + sorted_values[high] * fraction


def split_rhat(chains: list[list[float]]) -> float:
    if len(chains) < 2:
        return float("nan")
    half = min(len(chain) // 2 for chain in chains)
    if half < 2:
        return float("nan")
    split = []
    for chain in chains:
        split.append(chain[:half])
        split.append(chain[-half:])
    n = half
    means = [fmean(chain) for chain in split]
    variances = [sample_variance(chain) for chain in split]
    within = fmean(variances)
    if within <= 0:
        return 1.0
    between = n * sample_variance(means)
    variance = ((n - 1.0) / n) * within + between / n
    return math.sqrt(variance / within)


def correlation(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or len(left) < 2:
        return float("nan")
    left_mean = fmean(left)
    right_mean = fmean(right)
    numerator = sum(
        (x - left_mean) * (y - right_mean) for x, y in zip(left, right)
    )
    left_ss = sum((x - left_mean) ** 2 for x in left)
    right_ss = sum((y - right_mean) ** 2 for y in right)
    denominator = math.sqrt(left_ss * right_ss)
    return numerator / denominator if denominator else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose combined RooStats MCMC chains.")
    parser.add_argument("posterior", type=Path)
    parser.add_argument("diagnostics", type=Path)
    parser.add_argument("summary", type=Path)
    args = parser.parse_args()

    ROOT.gROOT.SetBatch(True)
    source = ROOT.TFile.Open(str(args.posterior), "READ")
    if not source or source.IsZombie():
        raise OSError(f"could not open {args.posterior}")
    tree = source.Get("samples")
    named = source.Get("combine_metadata")
    if not tree or not named:
        raise ValueError(f"{args.posterior}: samples or combine_metadata not found")
    metadata = json.loads(named.GetTitle())

    parameters = [str(name) for name in metadata["parameters"]]
    chain_count = int(metadata["chains"])
    iterations = int(metadata["iterations"])
    burn_in = int(metadata["burn_in"])

    by_chain = {
        chain: {name: [] for name in parameters}
        for chain in range(chain_count)
    }

    retained_per_chain = [0 for _ in range(chain_count)]
    for row in tree:
        chain_id = int(row.chain)
        start = int(row.iteration)
        weight = int(round(float(row.weight)))
        end = start + weight
        keep = max(0, end - max(start, burn_in))
        if keep == 0:
            continue
        retained_per_chain[chain_id] += keep
        for name in parameters:
            by_chain[chain_id][name].extend([float(getattr(row, name))] * keep)

    expected = iterations - burn_in
    if any(count != expected for count in retained_per_chain):
        raise ValueError(
            f"post-burn-in lengths {retained_per_chain} do not all equal {expected}"
        )

    pooled = {
        name: [
            value
            for chain in range(chain_count)
            for value in by_chain[chain][name]
        ]
        for name in parameters
    }

    parameter_results: dict[str, dict[str, float]] = {}
    for name in parameters:
        values = pooled[name]
        ordered = sorted(values)
        mean = fmean(values)
        sd = math.sqrt(sample_variance(values))
        parameter_results[name] = {
            "mean": mean,
            "sd": sd,
            "q16": quantile(ordered, 0.16),
            "median": quantile(ordered, 0.50),
            "q84": quantile(ordered, 0.84),
            "split_rhat": split_rhat(
                [by_chain[chain][name] for chain in range(chain_count)]
            ),
        }

    correlations = {
        left: {
            right: correlation(pooled[left], pooled[right])
            for right in parameters
        }
        for left in parameters
    }

    chain_results = sorted(
        metadata["chain_metadata"], key=lambda item: int(item["chain_id"])
    )
    diagnostics = {
        "schema": 1,
        "chains": chain_count,
        "iterations_per_chain": iterations,
        "burn_in": burn_in,
        "retained_per_chain": expected,
        "retained_total": expected * chain_count,
        "parameters": parameter_results,
        "correlations": correlations,
        "chains_detail": chain_results,
    }
    args.diagnostics.write_text(json.dumps(diagnostics, indent=2, sort_keys=True) + "\n")

    lines = [
        f"chains={chain_count}",
        f"iterations_per_chain={iterations}",
        f"burn_in={burn_in}",
        f"retained_total={diagnostics['retained_total']}",
    ]
    for name in parameters:
        result = parameter_results[name]
        lines.append(
            f"{name}: mean={result['mean']:.6g} sd={result['sd']:.6g} "
            f"68%=[{result['q16']:.6g}, {result['q84']:.6g}] "
            f"split_rhat={result['split_rhat']:.5f}"
        )
    for item in chain_results:
        lines.append(
            f"chain-{int(item['chain_id']):02d}: seed={item['seed']} "
            f"acceptance={float(item['acceptance_fraction']):.4f} "
            f"accepted_states={item['accepted_states']}"
        )
    args.summary.write_text("\n".join(lines) + "\n")
    source.Close()

    print(
        " ".join(
            f"{name}:Rhat={parameter_results[name]['split_rhat']:.4f}"
            for name in parameters
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
