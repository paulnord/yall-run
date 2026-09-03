#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from pathlib import Path

import ROOT


PARAMETERS = ("mpv", "landau_width", "gauss_sigma")
LANDAU_MP_SHIFT = -0.22278298


def require_roostats() -> None:
    if not hasattr(ROOT, "RooStats") or not hasattr(ROOT.RooStats, "MCMCCalculator"):
        raise RuntimeError("this example requires ROOT with RooFit and RooStats")


def find_pdf_peak(pdf: object, x: object, x_min: float, x_max: float) -> float:
    observables = ROOT.RooArgSet(x)
    steps = 6400
    best_x = x_min
    best_value = -1.0
    for index in range(steps + 1):
        value_x = x_min + (x_max - x_min) * index / steps
        x.setVal(value_x)
        value_pdf = float(pdf.getVal(observables))
        if value_pdf > best_value:
            best_value = value_pdf
            best_x = value_x
    return best_x


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate synthetic low-statistics LFHCal-like Langau data and a RooStats model."
    )
    parser.add_argument("output", type=Path)
    parser.add_argument("--events", type=int, default=2500)
    parser.add_argument("--seed", type=int, default=20260902)
    args = parser.parse_args()

    require_roostats()
    ROOT.gROOT.SetBatch(True)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    ROOT.RooRandom.randomGenerator().SetSeed(args.seed)

    # The earlier LFHCal toy fit used a nominal fit window extending to
    # 4 * avmip = 320 ADC.  Keep one ADC-wide bins across that range.
    x_min = 0.0
    x_max = 320.0
    x_bins = 320
    x = ROOT.RooRealVar("x", "pulse height [ADC]", x_min, x_max)
    x.setBins(x_bins)
    # RooFFTConvPdf samples the convolution on this named cache binning.
    x.setBins(4096, "cache")

    # Match the earlier LFHCal/HGCROC toy truth.  The classic ROOT langaus
    # implementation corrects the CERNLIB Landau shift so that its MP
    # parameter is the actual maximum of the unconvolved Landau.  RooLandau
    # takes the uncorrected location parameter, so express that location as a
    # function of the sampled MP and width to preserve the old convention.
    mpv = ROOT.RooRealVar("mpv", "Landau MP [ADC]", 80.0, 40.0, 176.0)
    landau_width = ROOT.RooRealVar(
        "landau_width", "Landau width [ADC]", 8.0, 0.5, 30.0
    )
    landau_location = ROOT.RooFormulaVar(
        "landau_location",
        "RooLandau location corresponding to corrected MP",
        f"@0 - ({LANDAU_MP_SHIFT})*@1",
        ROOT.RooArgList(mpv, landau_width),
    )
    gauss_mean = ROOT.RooRealVar("gauss_mean", "Gaussian mean", 0.0)
    gauss_mean.setConstant(True)
    gauss_sigma = ROOT.RooRealVar(
        "gauss_sigma", "Gaussian resolution [ADC]", 4.0, 0.5, 20.0
    )

    landau = ROOT.RooLandau(
        "landau", "Landau", x, landau_location, landau_width
    )
    gaussian = ROOT.RooGaussian(
        "resolution", "Gaussian resolution", x, gauss_mean, gauss_sigma
    )
    langau = ROOT.RooFFTConvPdf(
        "langau", "Landau convolved with Gaussian", x, landau, gaussian
    )
    langau.setBufferFraction(0.20)

    langau_peak = find_pdf_peak(langau, x, x_min, x_max)
    generated = langau.generate(ROOT.RooArgSet(x), args.events)
    data = generated.binnedClone("data", "synthetic low-statistics Langau data")

    parameters = ROOT.RooArgSet(mpv, landau_width, gauss_sigma)
    prior = ROOT.RooUniform("prior", "uniform prior", parameters)

    workspace = ROOT.RooWorkspace("workspace", "PyROOT LFHCal-like Langau MCMC example")
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
        "schema": 2,
        "seed": args.seed,
        "events": args.events,
        "parameters": {
            "mpv": 80.0,
            "landau_width": 8.0,
            "gauss_sigma": 4.0,
        },
        "landau_location": 80.0 - LANDAU_MP_SHIFT * 8.0,
        "langau_peak": langau_peak,
        "observable": {
            "name": "x",
            "min": x_min,
            "max": x_max,
            "bins": x_bins,
        },
        "fft_cache_bins": 4096,
    }

    output = ROOT.TFile.Open(str(args.output), "RECREATE")
    if not output or output.IsZombie():
        raise OSError(f"could not create {args.output}")
    workspace.Write("workspace")
    ROOT.TNamed("truth_json", json.dumps(truth, sort_keys=True)).Write()
    output.Close()

    print(
        f"generated {args.events} events: mpv=80 landau_width=8 "
        f"gauss_sigma=4 langau_peak={langau_peak:.4f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
