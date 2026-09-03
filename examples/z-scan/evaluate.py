#!/usr/bin/env python3
import json
from pathlib import Path
import sys

import ROOT

SIGMA_DETECTOR = 1.5


def read_point(path):
    fields = Path(path).read_text().split()
    if len(fields) != 2:
        raise ValueError("grid point must contain: MASS_GEV WIDTH_GEV")
    return float(fields[0]), float(fields[1])


def main():
    if len(sys.argv) != 4:
        raise SystemExit("usage: evaluate.py DATA.root POINT.txt OUTPUT.json")
    data_path, point_path, output_path = map(Path, sys.argv[1:])
    mass_value, width_value = read_point(point_path)
    source = ROOT.TFile.Open(str(data_path))
    if not source or source.IsZombie():
        raise RuntimeError("cannot open {}".format(data_path))
    hist = source.Get("mass")
    if not hist:
        raise RuntimeError("missing mass histogram")

    x = ROOT.RooRealVar("x", "m_ll [GeV]", 70.0, 110.0)
    x.setBins(4096, "cache")
    mass = ROOT.RooRealVar("mass0", "Z mass", mass_value, 80.0, 100.0)
    width = ROOT.RooRealVar("width", "Z width", width_value, 0.5, 6.0)
    mean = ROOT.RooRealVar("resolution_mean", "resolution mean", 0.0)
    sigma = ROOT.RooRealVar("resolution_sigma", "resolution sigma", SIGMA_DETECTOR)
    for variable in (mass, width, mean, sigma):
        variable.setConstant(True)
    breit_wigner = ROOT.RooBreitWigner("bw", "Breit-Wigner", x, mass, width)
    gaussian = ROOT.RooGaussian("resolution", "detector resolution", x, mean, sigma)
    model = ROOT.RooFFTConvPdf("model", "BW x Gaussian", x, breit_wigner, gaussian)
    model.setBufferFraction(0.25)
    data = ROOT.RooDataHist("data", "binned data", ROOT.RooArgList(x), hist)
    nll = model.createNLL(data)
    nll_value = float(nll.getVal())
    payload = {"point": point_path.stem, "mass_gev": mass_value, "width_gev": width_value, "detector_sigma_gev": SIGMA_DETECTOR, "nll": nll_value, "entries": int(hist.GetEntries())}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    source.Close()
    print(json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    main()
