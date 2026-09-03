#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

import ROOT

ROOT.gROOT.SetBatch(True)
MASS_TRUTH = 91.1876
WIDTH_TRUTH = 2.4952
SIGMA_DETECTOR = 1.5


def spacing(values):
    values = sorted(set(values))
    if len(values) < 2:
        return 1.0
    return min(b - a for a, b in zip(values[:-1], values[1:]))


def make_model(prefix, x, mass_value, width_value):
    mass = ROOT.RooRealVar(prefix + "_mass", "Z mass", mass_value)
    width = ROOT.RooRealVar(prefix + "_width", "Z width", width_value)
    mean = ROOT.RooRealVar(prefix + "_mean", "resolution mean", 0.0)
    sigma = ROOT.RooRealVar(prefix + "_sigma", "resolution sigma", SIGMA_DETECTOR)
    for variable in (mass, width, mean, sigma):
        variable.setConstant(True)
    breit_wigner = ROOT.RooBreitWigner(prefix + "_bw", "Breit-Wigner", x, mass, width)
    gaussian = ROOT.RooGaussian(prefix + "_resolution", "detector resolution", x, mean, sigma)
    model = ROOT.RooFFTConvPdf(prefix + "_model", "BW x Gaussian", x, breit_wigner, gaussian)
    model.setBufferFraction(0.25)
    return model, (mass, width, mean, sigma, breit_wigner, gaussian)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("results", nargs="+")
    parser.add_argument("--data", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--plot", required=True)
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--fit-plot", required=True)
    parser.add_argument("--fit-pdf", required=True)
    args = parser.parse_args()

    rows = [json.loads(Path(path).read_text()) for path in args.results]
    rows.sort(key=lambda row: (row["mass_gev"], row["width_gev"]))
    best = min(rows, key=lambda row: row["nll"])
    min_nll = float(best["nll"])
    masses = sorted(set(float(row["mass_gev"]) for row in rows))
    widths = sorted(set(float(row["width_gev"]) for row in rows))
    dm = spacing(masses)
    dw = spacing(widths)

    scan = ROOT.TH2D(
        "delta_nll",
        "Z likelihood scan;M_{Z} [GeV];#Gamma_{Z} [GeV]",
        len(masses),
        masses[0] - 0.5 * dm,
        masses[-1] + 0.5 * dm,
        len(widths),
        widths[0] - 0.5 * dw,
        widths[-1] + 0.5 * dw,
    )
    for row in rows:
        scan.Fill(
            float(row["mass_gev"]),
            float(row["width_gev"]),
            2.0 * (float(row["nll"]) - min_nll),
        )

    for path in (args.root, args.summary, args.plot, args.pdf, args.fit_plot, args.fit_pdf):
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    root_file = ROOT.TFile(args.root, "RECREATE")
    scan.Write()
    ROOT.TNamed("best_fit_json", json.dumps(best, sort_keys=True)).Write()
    root_file.Close()

    ROOT.gStyle.SetOptStat(0)
    canvas = ROOT.TCanvas("c_scan", "Z likelihood scan", 900, 720)
    scan.SetContour(50)
    scan.Draw("COLZ")
    truth_marker = ROOT.TMarker(MASS_TRUTH, WIDTH_TRUTH, 29)
    truth_marker.SetMarkerColor(ROOT.kRed + 1)
    truth_marker.SetMarkerSize(2.0)
    truth_marker.Draw("SAME")
    best_marker = ROOT.TMarker(float(best["mass_gev"]), float(best["width_gev"]), 34)
    best_marker.SetMarkerColor(ROOT.kBlack)
    best_marker.SetMarkerSize(1.8)
    best_marker.Draw("SAME")
    legend = ROOT.TLegend(0.61, 0.75, 0.88, 0.88)
    legend.AddEntry(best_marker, "best grid point", "p")
    legend.AddEntry(truth_marker, "generation truth", "p")
    legend.Draw()
    canvas.SaveAs(args.plot)
    canvas.SaveAs(args.pdf)

    source = ROOT.TFile.Open(args.data)
    if not source or source.IsZombie():
        raise RuntimeError("cannot open {}".format(args.data))
    hist = source.Get("mass")
    if not hist:
        raise RuntimeError("missing mass histogram")

    x = ROOT.RooRealVar("x_overlay", "m_{ll} [GeV]", 70.0, 110.0)
    x.setBins(4096, "cache")
    data = ROOT.RooDataHist("overlay_data", "binned data", ROOT.RooArgList(x), hist)
    best_model, best_parts = make_model("best", x, float(best["mass_gev"]), float(best["width_gev"]))
    truth_model, truth_parts = make_model("truth", x, MASS_TRUTH, WIDTH_TRUTH)

    frame = x.frame(ROOT.RooFit.Title("Synthetic Z spectrum with best grid fit"))
    data.plotOn(frame, ROOT.RooFit.Name("data"))
    truth_model.plotOn(
        frame,
        ROOT.RooFit.Name("truth_curve"),
        ROOT.RooFit.LineColor(ROOT.kRed + 1),
        ROOT.RooFit.LineStyle(2),
    )
    best_model.plotOn(
        frame,
        ROOT.RooFit.Name("best_curve"),
        ROOT.RooFit.LineColor(ROOT.kBlue + 1),
        ROOT.RooFit.LineWidth(2),
    )
    frame.GetYaxis().SetTitle("events / bin")

    fit_canvas = ROOT.TCanvas("c_fit", "Z best-fit overlay", 900, 650)
    frame.Draw()
    fit_legend = ROOT.TLegend(0.57, 0.70, 0.88, 0.88)
    fit_legend.AddEntry(frame.findObject("data"), "synthetic data", "pe")
    fit_legend.AddEntry(frame.findObject("best_curve"), "best grid point", "l")
    fit_legend.AddEntry(frame.findObject("truth_curve"), "generation truth", "l")
    fit_legend.Draw()
    fit_canvas.SaveAs(args.fit_plot)
    fit_canvas.SaveAs(args.fit_pdf)
    source.Close()

    # Keep RooFit components alive until after both plots have been written.
    _keep_alive = best_parts + truth_parts + (best_model, truth_model, data, frame)
    if not _keep_alive:
        raise AssertionError("unreachable")

    lines = [
        "grid_points={}".format(len(rows)),
        "best_mass_gev={:.6f}".format(float(best["mass_gev"])),
        "best_width_gev={:.6f}".format(float(best["width_gev"])),
        "generation_mass_gev={:.6f}".format(MASS_TRUTH),
        "generation_width_gev={:.6f}".format(WIDTH_TRUTH),
        "min_nll={:.9f}".format(min_nll),
    ]
    Path(args.summary).write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
