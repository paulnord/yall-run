#!/usr/bin/env python3
import json
from pathlib import Path
import sys

import ROOT

ROOT.gROOT.SetBatch(True)
MASS_KS = 0.497611


def main():
    if len(sys.argv) != 6:
        raise SystemExit("usage: reconstruct.py EVENTS.root RESULT.json HIST.root FIT.png FIT.pdf")
    input_path, result_path, hist_path, plot_path, pdf_path = map(Path, sys.argv[1:])
    for path in (result_path, hist_path, plot_path, pdf_path):
        path.parent.mkdir(parents=True, exist_ok=True)

    source = ROOT.TFile.Open(str(input_path))
    if not source or source.IsZombie():
        raise RuntimeError("cannot open {}".format(input_path))
    tree = source.Get("pairs")
    if not tree:
        raise RuntimeError("missing pairs tree")

    hist = ROOT.TH1D("mass", "K_{S}^{0} -> #pi^{+}#pi^{-};m_{#pi#pi} [GeV];pairs / bin", 120, 0.35, 0.65)
    first = ROOT.TLorentzVector()
    second = ROOT.TLorentzVector()
    for entry in tree:
        first.SetPxPyPzE(entry.px1, entry.py1, entry.pz1, entry.e1)
        second.SetPxPyPzE(entry.px2, entry.py2, entry.pz2, entry.e2)
        hist.Fill((first + second).M())

    model = ROOT.TF1("mass_model", "gaus(0)+pol1(3)", 0.44, 0.56)
    model.SetParameters(hist.GetMaximum(), MASS_KS, 0.008, 1.0, 0.0)
    model.SetParLimits(1, 0.48, 0.515)
    model.SetParLimits(2, 0.002, 0.03)
    status = int(hist.Fit(model, "QRS"))
    payload = {
        "input": str(input_path),
        "fit_status": status,
        "mass_gev": float(model.GetParameter(1)),
        "mass_error_gev": float(model.GetParError(1)),
        "sigma_gev": abs(float(model.GetParameter(2))),
        "entries": int(hist.GetEntries()),
    }
    result_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    output = ROOT.TFile(str(hist_path), "RECREATE")
    hist.Write()
    model.Write()
    ROOT.TNamed("fit_json", json.dumps(payload, sort_keys=True)).Write()
    output.Close()

    canvas = ROOT.TCanvas("c_fit", "Invariant mass fit", 900, 650)
    ROOT.gStyle.SetOptStat(0)
    hist.SetMarkerStyle(20)
    hist.SetMarkerSize(0.75)
    hist.Draw("E")
    model.SetLineColor(ROOT.kBlue + 1)
    model.SetLineWidth(2)
    model.Draw("SAME")
    truth = ROOT.TLine(MASS_KS, 0.0, MASS_KS, 1.05 * hist.GetMaximum())
    truth.SetLineColor(ROOT.kRed + 1)
    truth.SetLineStyle(2)
    truth.Draw("SAME")
    legend = ROOT.TLegend(0.55, 0.69, 0.88, 0.88)
    legend.AddEntry(hist, "reconstructed pairs", "pe")
    legend.AddEntry(model, "Gaussian + background", "l")
    legend.AddEntry(truth, "generation mass", "l")
    legend.AddEntry(0, "m = {:.6f} #pm {:.6f} GeV".format(payload["mass_gev"], payload["mass_error_gev"]), "")
    legend.Draw()
    canvas.SaveAs(str(plot_path))
    canvas.SaveAs(str(pdf_path))

    source.Close()
    print(json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    main()
