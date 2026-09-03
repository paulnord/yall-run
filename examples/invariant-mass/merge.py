#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

import ROOT

ROOT.gROOT.SetBatch(True)
MASS_KS = 0.497611


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", nargs="+", required=True)
    parser.add_argument("--roots", nargs="+", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--plot", required=True)
    parser.add_argument("--pdf", required=True)
    args = parser.parse_args()
    rows = [json.loads(Path(path).read_text()) for path in args.results]
    rows.sort(key=lambda row: row["input"])
    if any(int(row["fit_status"]) != 0 for row in rows):
        raise RuntimeError("at least one per-run mass fit failed")

    merged = None
    sources = []
    for path in args.roots:
        source = ROOT.TFile.Open(path)
        if not source or source.IsZombie():
            raise RuntimeError("cannot open {}".format(path))
        sources.append(source)
        hist = source.Get("mass")
        if not hist:
            raise RuntimeError("missing mass histogram in {}".format(path))
        if merged is None:
            merged = hist.Clone("combined_mass")
            merged.SetDirectory(0)
        else:
            merged.Add(hist)

    model = ROOT.TF1("combined_model", "gaus(0)+pol1(3)", 0.44, 0.56)
    model.SetParameters(merged.GetMaximum(), MASS_KS, 0.008, 1.0, 0.0)
    model.SetParLimits(1, 0.48, 0.515)
    model.SetParLimits(2, 0.002, 0.03)
    status = int(merged.Fit(model, "QRS"))
    if status != 0:
        raise RuntimeError("combined mass fit failed with status {}".format(status))

    combined = {
        "mass_gev": float(model.GetParameter(1)),
        "mass_error_gev": float(model.GetParError(1)),
        "sigma_gev": abs(float(model.GetParameter(2))),
        "truth_mass_gev": MASS_KS,
        "runs": len(rows),
        "entries": int(merged.GetEntries()),
    }
    for path in (args.output, args.summary, args.plot, args.pdf):
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    output = ROOT.TFile(args.output, "RECREATE")
    merged.Write()
    model.Write()
    ROOT.TNamed("combined_json", json.dumps(combined, sort_keys=True)).Write()
    output.Close()

    canvas = ROOT.TCanvas("c", "Invariant mass", 900, 650)
    ROOT.gStyle.SetOptStat(0)
    merged.SetTitle("Combined K_{S}^{0} -> #pi^{+}#pi^{-};m_{#pi#pi} [GeV];pairs / bin")
    merged.SetMarkerStyle(20)
    merged.SetMarkerSize(0.75)
    merged.Draw("E")
    model.SetLineColor(ROOT.kBlue + 1)
    model.SetLineWidth(2)
    model.Draw("SAME")
    truth = ROOT.TLine(MASS_KS, 0.0, MASS_KS, 1.05 * merged.GetMaximum())
    truth.SetLineColor(ROOT.kRed + 1)
    truth.SetLineStyle(2)
    truth.Draw("SAME")
    legend = ROOT.TLegend(0.55, 0.69, 0.88, 0.88)
    legend.AddEntry(merged, "all reconstructed pairs", "pe")
    legend.AddEntry(model, "combined fit", "l")
    legend.AddEntry(truth, "generation mass", "l")
    legend.AddEntry(0, "m = {:.6f} #pm {:.6f} GeV".format(combined["mass_gev"], combined["mass_error_gev"]), "")
    legend.Draw()
    canvas.SaveAs(args.plot)
    canvas.SaveAs(args.pdf)

    lines = [
        "runs={}".format(len(rows)),
        "entries={}".format(int(merged.GetEntries())),
        "combined_mass_gev={:.9f}".format(combined["mass_gev"]),
        "combined_mass_error_gev={:.9f}".format(combined["mass_error_gev"]),
        "combined_sigma_gev={:.9f}".format(combined["sigma_gev"]),
        "generation_mass_gev={:.9f}".format(MASS_KS),
    ]
    for index, row in enumerate(rows):
        lines.append("run_{:02d}_mass_gev={:.9f} sigma_gev={:.9f}".format(index, float(row["mass_gev"]), float(row["sigma_gev"])))
    Path(args.summary).write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    for source in sources:
        source.Close()


if __name__ == "__main__":
    main()
