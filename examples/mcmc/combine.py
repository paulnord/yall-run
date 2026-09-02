#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from pathlib import Path

import ROOT


def read_metadata(path: Path) -> dict[str, object]:
    handle = ROOT.TFile.Open(str(path), "READ")
    if not handle or handle.IsZombie():
        raise OSError(f"could not open {path}")
    record = handle.Get("chain_metadata")
    tree = handle.Get("samples")
    if not record or not tree:
        raise ValueError(f"{path}: samples or chain_metadata not found")
    metadata = json.loads(record.GetTitle())
    metadata["entries"] = int(tree.GetEntries())
    handle.Close()
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description="Combine RooStats MCMC chain files.")
    parser.add_argument("chains", nargs="+", type=Path)
    parser.add_argument("-o", "--output", required=True, type=Path)
    args = parser.parse_args()

    ROOT.gROOT.SetBatch(True)
    metadata = [read_metadata(path) for path in args.chains]
    ids = [int(item["chain_id"]) for item in metadata]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate chain ids")
    if set(ids) != set(range(len(ids))):
        raise ValueError(f"expected chain ids 0..{len(ids) - 1}, got {sorted(ids)}")

    settings = {
        (int(item["iterations"]), int(item["burn_in"]))
        for item in metadata
    }
    if len(settings) != 1:
        raise ValueError("chains disagree about iteration or burn-in settings")
    iterations, burn_in = settings.pop()

    combined = ROOT.TChain("samples")
    for path in args.chains:
        if combined.Add(str(path)) == 0:
            raise ValueError(f"could not add samples from {path}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    output = ROOT.TFile.Open(str(args.output), "RECREATE")
    if not output or output.IsZombie():
        raise OSError(f"could not create {args.output}")
    output.cd()
    cloned = combined.CloneTree(-1, "fast")
    cloned.SetName("samples")
    cloned.SetTitle("combined compressed RooStats Markov chains")
    cloned.Write()

    record = {
        "schema": 1,
        "chains": len(metadata),
        "iterations": iterations,
        "burn_in": burn_in,
        "parameters": list(metadata[0]["parameters"]),
        "input_files": [str(path) for path in args.chains],
        "chain_metadata": sorted(metadata, key=lambda item: int(item["chain_id"])),
        "entries": int(cloned.GetEntries()),
    }
    ROOT.TNamed("combine_metadata", json.dumps(record, sort_keys=True)).Write()
    output.Close()

    print(
        f"combined chains={len(metadata)} entries={record['entries']} "
        f"iterations_per_chain={iterations} burn_in={burn_in}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
