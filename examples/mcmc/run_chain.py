#!/usr/bin/env python3

from __future__ import annotations

import argparse
from array import array
import json
from pathlib import Path

import ROOT


PARAMETERS = ("mpv", "landau_width", "gauss_sigma")


def parse_config(path: Path) -> tuple[int, int, int, int]:
    try:
        chain_id = int(path.stem.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        raise ValueError(f"{path}: expected a name like chain-00.txt") from None

    fields = path.read_text().split()
    if len(fields) != 2:
        raise ValueError(
            f"{path}: expected ITERATIONS BURN_IN, got {len(fields)} fields"
        )
    iterations, burn_in = map(int, fields)
    seed = 41001 + chain_id
    if chain_id < 0 or iterations < 100:
        raise ValueError(f"{path}: invalid chain configuration")
    if burn_in < 0 or burn_in >= iterations:
        raise ValueError(f"{path}: burn-in must satisfy 0 <= burn-in < iterations")
    return chain_id, seed, iterations, burn_in


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one RooStats MCMC chain.")
    parser.add_argument("model", type=Path)
    parser.add_argument("config", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    ROOT.gROOT.SetBatch(True)
    chain_id, seed, iterations, burn_in = parse_config(args.config)

    source = ROOT.TFile.Open(str(args.model), "READ")
    if not source or source.IsZombie():
        raise OSError(f"could not open {args.model}")
    workspace = source.Get("workspace")
    if not workspace:
        raise ValueError(f"{args.model}: workspace not found")
    model = workspace.obj("ModelConfig")
    data = workspace.data("data")
    if not model or not data:
        raise ValueError(f"{args.model}: ModelConfig or data not found")

    ROOT.RooRandom.randomGenerator().SetSeed(seed)

    # SequentialProposal moves one coordinate at a time. Its constructor
    # parameter is a divisor. The LFHCal-like toy deliberately keeps broad
    # priors, especially on MP, so use 30 here to keep proposal steps near the
    # posterior scale rather than taking 10%-of-range jumps.
    proposal = ROOT.RooStats.SequentialProposal(30.0)
    calculator = ROOT.RooStats.MCMCCalculator(data, model)
    calculator.SetConfidenceLevel(0.68)
    calculator.SetNumIters(iterations)
    calculator.SetNumBurnInSteps(burn_in)
    calculator.SetNumBins(40)
    calculator.SetProposalFunction(proposal)

    interval = calculator.GetInterval()
    if not interval:
        raise RuntimeError("RooStats MCMCCalculator did not return an interval")
    chain = interval.GetChain()
    if not chain or chain.Size() == 0:
        raise RuntimeError("RooStats produced an empty Markov chain")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    output = ROOT.TFile.Open(str(args.output), "RECREATE")
    if not output or output.IsZombie():
        raise OSError(f"could not create {args.output}")

    tree = ROOT.TTree("samples", "compressed Metropolis-Hastings chain states")
    b_chain = array("i", [chain_id])
    b_state = array("i", [0])
    b_iteration = array("i", [0])
    b_weight = array("d", [0.0])
    b_nll = array("d", [0.0])
    b_values = {name: array("d", [0.0]) for name in PARAMETERS}

    tree.Branch("chain", b_chain, "chain/I")
    tree.Branch("state", b_state, "state/I")
    tree.Branch("iteration", b_iteration, "iteration/I")
    tree.Branch("weight", b_weight, "weight/D")
    tree.Branch("nll", b_nll, "nll/D")
    for name, holder in b_values.items():
        tree.Branch(name, holder, f"{name}/D")

    iteration = 0
    total_weight = 0.0
    for index in range(chain.Size()):
        point = chain.Get(index)
        weight = float(chain.Weight(index))
        b_state[0] = index
        b_iteration[0] = iteration
        b_weight[0] = weight
        b_nll[0] = float(chain.NLL(index))
        for name, holder in b_values.items():
            variable = point.find(name)
            if not variable:
                raise ValueError(f"chain does not contain parameter {name}")
            holder[0] = float(variable.getVal())
        tree.Fill()
        total_weight += weight
        iteration += int(round(weight))

    metadata = {
        "schema": 1,
        "chain_id": chain_id,
        "seed": seed,
        "iterations": iterations,
        "burn_in": burn_in,
        "accepted_states": int(chain.Size()),
        "acceptance_fraction": float(chain.Size()) / float(iterations),
        "stored_weight": total_weight,
        "parameters": list(PARAMETERS),
        "source_model": str(args.model),
    }
    tree.Write()
    ROOT.TNamed("chain_metadata", json.dumps(metadata, sort_keys=True)).Write()
    output.Close()
    source.Close()

    print(
        f"chain={chain_id:02d} seed={seed} iterations={iterations} "
        f"accepted_states={chain.Size()} "
        f"acceptance={metadata['acceptance_fraction']:.3f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
