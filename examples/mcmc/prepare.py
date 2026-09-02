#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from pathlib import Path

import ROOT


PARAMETERS = ("mpv", "landau_width", "gauss_sigma")


def require_roostats() -> None:
    if not hasattr(ROOT, "RooStats") or not hasattr(ROOT.RooStats, "MCMCCalculator"):
        raise RuntimeError("this example requires ROOT with RooFit and RooStats")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate synthetic binned Langau data and a RooStats model."
    )
    parser.add_argument("output", type=Path)
    parser.add_argument("--events", type=int, default=12000)
    parser.add_argument("--seed", type=int, default=20260902)
    args = parser.parse_args()

    require_roostats()
    ROOT.gROOT.SetBatch(True)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    ROOT.RooRandom.randomGenerator().SetSeed(args.seed)

    x = ROOT.RooRealVar("x", "pulse height", 0.0, 100.0)
    x.setBins(100)
    # RooFFTConvPdf samples the convolution on this named cache binning.
    x.setBins(2048, "cache")

    mpv = ROOT.RooRealVar("mpv", "Landau location", 35.0, 20.0, 50.0)
    landau_width = ROOT.RooRealVar(
        "landau_width", "Landau width", 4.5, 1.0, 10.0
    )
    gauss_mean = ROOT.RooRealVar("gauss_mean", "Gaussian mean", 0.0)
    gauss_mean.setConstant(True)
    gauss_sigma = ROOT.RooRealVar(
        "gauss_sigma", "Gaussian resolution", 2.5, 0.5, 7.0
    )

    landau = ROOT.RooLandau("landau", "Landau", x, mpv, landau_width)
    gaussian = ROOT.RooGaussian(
        "resolution", "Gaussian resolution", x, gauss_mean, gauss_sigma
    )
    langau = ROOT.RooFFTConvPdf(
        "langau", "Landau convolved with Gaussian", x, landau, gaussian
    )
    langau.setBufferFraction(0.20)

    generated = langau.generate(ROOT.RooArgSet(x), args.events)
    data = generated.binnedClone("data", "synthetic binned Langau data")

    parameters = ROOT.RooArgSet(mpv, landau_width, gauss_sigma)
    prior = ROOT.RooUniform("prior", "uniform prior", parameters)

    workspace = ROOT.RooWorkspace("workspace", "PyROOT Langau MCMC example")
    # Importing the composite PDFs also imports and reconnects their variables.
    workspace.Import(langau)
    workspace.Import(prior)
    workspace.Import(data)

    model = ROOT.RooStats.ModelConfig("ModelConfig", workspace)
    model.SetPdf(workspace.pdf("langau"))
    model.SetPriorPdf(workspace.pdf("prior"))
    model.SetParametersOfInterest(ROOT.RooArgSet(workspace.var("mpv")))
    model.SetNuisanceParameters(
        ROOT.RooArgSet(
            workspace.var("landau_width"),
            workspace.var("gauss_sigma"),
        )
    )
    model.SetObservables(ROOT.RooArgSet(workspace.var("x")))
    workspace.Import(model)

    truth = {
        "schema": 1,
        "seed": args.seed,
        "events": args.events,
        "parameters": {
            "mpv": 35.0,
            "landau_width": 4.5,
            "gauss_sigma": 2.5,
        },
        "observable": {"name": "x", "min": 0.0, "max": 100.0, "bins": 100},
        "fft_cache_bins": 2048,
    }

    output = ROOT.TFile.Open(str(args.output), "RECREATE")
    if not output or output.IsZombie():
        raise OSError(f"could not create {args.output}")
    workspace.Write("workspace")
    ROOT.TNamed("truth_json", json.dumps(truth, sort_keys=True)).Write()
    output.Close()

    print(
        f"generated {args.events} events: "
        "mpv=35 landau_width=4.5 gauss_sigma=2.5"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
