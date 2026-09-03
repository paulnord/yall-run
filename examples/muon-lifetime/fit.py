#!/usr/bin/env python3
import json
import math
from pathlib import Path
import sys

import ROOT

ROOT.gROOT.SetBatch(True)


def main():
    if len(sys.argv) != 6:
        raise SystemExit("usage: fit.py INPUT.root FIT.json FIT.root FIT.png FIT.pdf")
    input_path, json_path, fit_root_path, plot_path, pdf_path = map(Path, sys.argv[1:])
    for path in (json_path, fit_root_path, plot_path, pdf_path):
        path.parent.mkdir(parents=True, exist_ok=True)

    source = ROOT.TFile.Open(str(input_path))
    if not source or source.IsZombie():
        raise RuntimeError("cannot open {}".format(input_path))
    tree = source.Get("decays")
    if not tree:
        raise RuntimeError("missing decays tree")

    hist = ROOT.TH1D("decay_time", "Muon decay time;t [#mus];events / bin", 120, 0.0, 12.0)
    for entry in tree:
        hist.Fill(float(entry.t_us))

    model = ROOT.TF1("decay_model", "expo(0)+pol0(2)", 0.25, 10.0)
    model.SetParameters(math.log(max(hist.GetMaximum(), 1.0)), -1.0 / 2.2, 1.0)
    model.SetParLimits(2, 0.0, max(hist.GetMaximum(), 1.0))
    fit_result = hist.Fit(model, "QRS")
    status = int(fit_result)

    slope = float(model.GetParameter(1))
    slope_error = float(model.GetParError(1))
    if slope >= 0.0:
        raise RuntimeError("unphysical non-negative exponential slope")
    tau = -1.0 / slope
    tau_error = abs(slope_error / (slope * slope))

    payload = {
        "input": str(input_path),
        "fit_status": status,
        "tau_us": tau,
        "tau_error_us": tau_error,
        "background_per_bin": float(model.GetParameter(2)),
        "entries": int(hist.GetEntries()),
    }
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    fit_file = ROOT.TFile(str(fit_root_path), "RECREATE")
    hist.Write()
    model.Write()
    ROOT.TNamed("fit_json", json.dumps(payload, sort_keys=True)).Write()
    fit_file.Close()

    canvas = ROOT.TCanvas("c_fit", "Muon lifetime fit", 900, 650)
    ROOT.gStyle.SetOptStat(0)
    hist.SetMarkerStyle(20)
    hist.SetMarkerSize(0.75)
    hist.Draw("E")
    model.SetLineColor(ROOT.kBlue + 1)
    model.SetLineWidth(2)
    model.Draw("SAME")
    legend = ROOT.TLegend(0.56, 0.72, 0.88, 0.88)
    legend.AddEntry(hist, "synthetic decay times", "pe")
    legend.AddEntry(model, "exponential + background", "l")
    legend.AddEntry(0, "#tau = {:.4f} #pm {:.4f} #mus".format(tau, tau_error), "")
    legend.Draw()
    canvas.SaveAs(str(plot_path))
    canvas.SaveAs(str(pdf_path))

    source.Close()
    print(json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    main()
