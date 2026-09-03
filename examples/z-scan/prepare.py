#!/usr/bin/env python3
import json
from pathlib import Path
import sys

import ROOT

ROOT.gROOT.SetBatch(True)
MASS_TRUTH = 91.1876
WIDTH_TRUTH = 2.4952
SIGMA_DETECTOR = 1.5
EVENTS = 8000
SEED = 20260903
X_MIN = 70.0
X_MAX = 110.0
BINS = 160


def main():
    if len(sys.argv) != 4:
        raise SystemExit("usage: prepare.py OUTPUT.root RAW.png RAW.pdf")
    output, plot_path, pdf_path = map(Path, sys.argv[1:])
    for path in (output, plot_path, pdf_path):
        path.parent.mkdir(parents=True, exist_ok=True)

    rng = ROOT.TRandom3(SEED)
    hist = ROOT.TH1D("mass", "Synthetic Z -> l^{+}l^{-};m_{ll} [GeV];events / bin", BINS, X_MIN, X_MAX)
    while int(hist.GetEntries()) < EVENTS:
        value = rng.BreitWigner(MASS_TRUTH, WIDTH_TRUTH) + rng.Gaus(0.0, SIGMA_DETECTOR)
        if X_MIN <= value < X_MAX:
            hist.Fill(value)

    truth = {
        "mass_gev": MASS_TRUTH,
        "width_gev": WIDTH_TRUTH,
        "detector_sigma_gev": SIGMA_DETECTOR,
        "events": EVENTS,
        "seed": SEED,
        "range_gev": [X_MIN, X_MAX],
        "bins": BINS,
    }
    root_file = ROOT.TFile(str(output), "RECREATE")
    hist.Write()
    ROOT.TNamed("truth_json", json.dumps(truth, sort_keys=True)).Write()
    root_file.Close()

    canvas = ROOT.TCanvas("c_raw", "Synthetic Z spectrum", 900, 650)
    ROOT.gStyle.SetOptStat(0)
    hist.SetMarkerStyle(20)
    hist.SetMarkerSize(0.65)
    hist.Draw("E")
    truth_line = ROOT.TLine(MASS_TRUTH, 0.0, MASS_TRUTH, 1.05 * hist.GetMaximum())
    truth_line.SetLineColor(ROOT.kRed + 1)
    truth_line.SetLineStyle(2)
    truth_line.Draw("SAME")
    legend = ROOT.TLegend(0.61, 0.76, 0.88, 0.88)
    legend.AddEntry(hist, "synthetic data", "pe")
    legend.AddEntry(truth_line, "generation M_{Z}", "l")
    legend.Draw()
    canvas.SaveAs(str(plot_path))
    canvas.SaveAs(str(pdf_path))

    print(json.dumps(truth, sort_keys=True))


if __name__ == "__main__":
    main()
