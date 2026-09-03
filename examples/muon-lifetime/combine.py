#!/usr/bin/env python3
import argparse
import json
import math
from pathlib import Path

import ROOT

ROOT.gROOT.SetBatch(True)
TRUTH_TAU_US = 2.1969811


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("results", nargs="+")
    parser.add_argument("--root", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--plot", required=True)
    parser.add_argument("--pdf", required=True)
    args = parser.parse_args()

    rows = [json.loads(Path(path).read_text()) for path in args.results]
    rows.sort(key=lambda row: row["input"])
    weights = [1.0 / float(row["tau_error_us"]) ** 2 for row in rows]
    weight_sum = sum(weights)
    mean = sum(w * float(row["tau_us"]) for w, row in zip(weights, rows)) / weight_sum
    mean_error = math.sqrt(1.0 / weight_sum)
    chi2 = sum(((float(row["tau_us"]) - mean) / float(row["tau_error_us"])) ** 2 for row in rows)

    for path in (args.root, args.summary, args.plot, args.pdf):
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    graph = ROOT.TGraphErrors(len(rows))
    graph.SetName("run_lifetimes")
    graph.SetTitle("Muon lifetime by run;run index;#tau_{#mu} [#mus]")
    for index, row in enumerate(rows):
        graph.SetPoint(index, float(index), float(row["tau_us"]))
        graph.SetPointError(index, 0.0, float(row["tau_error_us"]))

    truth = ROOT.TF1("generation_truth", str(TRUTH_TAU_US), -0.5, len(rows) - 0.5)
    truth.SetLineStyle(2)
    truth.SetLineColor(ROOT.kRed + 1)
    combined = ROOT.TF1("weighted_mean", str(mean), -0.5, len(rows) - 0.5)
    combined.SetLineColor(ROOT.kBlue + 1)

    root_file = ROOT.TFile(args.root, "RECREATE")
    graph.Write()
    truth.Write()
    combined.Write()
    ROOT.TNamed("combined_json", json.dumps({"weighted_mean_us": mean, "weighted_error_us": mean_error, "chi2": chi2, "ndf": max(len(rows) - 1, 0), "truth_us": TRUTH_TAU_US}, sort_keys=True)).Write()
    root_file.Close()

    canvas = ROOT.TCanvas("c", "Muon lifetime", 900, 650)
    graph.SetMarkerStyle(20)
    graph.Draw("AP")
    truth.Draw("SAME")
    combined.Draw("SAME")
    legend = ROOT.TLegend(0.56, 0.69, 0.88, 0.88)
    legend.AddEntry(graph, "per-run fit", "pe")
    legend.AddEntry(combined, "weighted mean", "l")
    legend.AddEntry(truth, "generation truth", "l")
    legend.AddEntry(0, "#tau = {:.5f} #pm {:.5f} #mus".format(mean, mean_error), "")
    legend.Draw()
    canvas.SaveAs(args.plot)
    canvas.SaveAs(args.pdf)

    lines = [
        "runs={}".format(len(rows)),
        "weighted_mean_us={:.9f}".format(mean),
        "weighted_error_us={:.9f}".format(mean_error),
        "generation_truth_us={:.9f}".format(TRUTH_TAU_US),
        "chi2={:.6f}".format(chi2),
        "ndf={}".format(max(len(rows) - 1, 0)),
    ]
    Path(args.summary).write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
