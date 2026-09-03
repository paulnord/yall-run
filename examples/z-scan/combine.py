#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

import ROOT

MASS_TRUTH = 91.1876
WIDTH_TRUTH = 2.4952


def spacing(values):
    values = sorted(set(values))
    if len(values) < 2:
        return 1.0
    return min(b - a for a, b in zip(values[:-1], values[1:]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("results", nargs="+")
    parser.add_argument("--root", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--plot", required=True)
    parser.add_argument("--pdf", required=True)
    args = parser.parse_args()
    rows = [json.loads(Path(path).read_text()) for path in args.results]
    rows.sort(key=lambda row: (row["mass_gev"], row["width_gev"]))
    best = min(rows, key=lambda row: row["nll"])
    min_nll = float(best["nll"])
    masses = sorted(set(float(row["mass_gev"]) for row in rows))
    widths = sorted(set(float(row["width_gev"]) for row in rows))
    dm = spacing(masses)
    dw = spacing(widths)
    scan = ROOT.TH2D("delta_nll", "Z likelihood scan;M_{Z} [GeV];#Gamma_{Z} [GeV]", len(masses), masses[0] - 0.5 * dm, masses[-1] + 0.5 * dm, len(widths), widths[0] - 0.5 * dw, widths[-1] + 0.5 * dw)
    for row in rows:
        scan.Fill(float(row["mass_gev"]), float(row["width_gev"]), 2.0 * (float(row["nll"]) - min_nll))
    for path in (args.root, args.summary, args.plot, args.pdf):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    root_file = ROOT.TFile(args.root, "RECREATE")
    scan.Write()
    ROOT.TNamed("best_fit_json", json.dumps(best, sort_keys=True)).Write()
    root_file.Close()
    canvas = ROOT.TCanvas("c", "Z likelihood scan", 900, 720)
    ROOT.gStyle.SetOptStat(0)
    scan.Draw("COLZ TEXT")
    truth = ROOT.TMarker(MASS_TRUTH, WIDTH_TRUTH, 29)
    truth.SetMarkerColor(ROOT.kRed + 1)
    truth.SetMarkerSize(2.0)
    truth.Draw("SAME")
    best_marker = ROOT.TMarker(float(best["mass_gev"]), float(best["width_gev"]), 34)
    best_marker.SetMarkerColor(ROOT.kBlack)
    best_marker.SetMarkerSize(1.8)
    best_marker.Draw("SAME")
    canvas.SaveAs(args.plot)
    canvas.SaveAs(args.pdf)
    lines = ["grid_points={}".format(len(rows)), "best_mass_gev={:.6f}".format(float(best["mass_gev"])), "best_width_gev={:.6f}".format(float(best["width_gev"])), "generation_mass_gev={:.6f}".format(MASS_TRUTH), "generation_width_gev={:.6f}".format(WIDTH_TRUTH), "min_nll={:.9f}".format(min_nll)]
    Path(args.summary).write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
