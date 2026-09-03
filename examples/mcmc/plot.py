#!/usr/bin/env python3

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path

import ROOT


def quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("empty sample")
    position = probability * (len(ordered) - 1)
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return ordered[low]
    fraction = position - low
    return ordered[low] * (1.0 - fraction) + ordered[high] * fraction


def iter_tree(tree: object):
    for index in range(int(tree.GetEntries())):
        tree.GetEntry(index)
        yield tree


def post_burn_weight(row: object, burn_in: int) -> int:
    start = int(row.iteration)
    weight = int(round(float(row.weight)))
    return max(0, start + weight - max(start, burn_in))


def save_canvas(canvas: object, output_dir: Path, stem: str) -> None:
    canvas.SaveAs(str(output_dir / f"{stem}.pdf"))
    canvas.SaveAs(str(output_dir / f"{stem}.png"))


def load_truth_parameters(model_path: Path) -> dict[str, float]:
    source = ROOT.TFile.Open(str(model_path), "READ")
    if not source or source.IsZombie():
        raise OSError(f"could not open {model_path}")
    truth_named = source.Get("truth_json")
    if not truth_named:
        source.Close()
        raise ValueError(f"{model_path}: truth metadata missing")
    truth = json.loads(truth_named.GetTitle())
    values = {
        str(name): float(value)
        for name, value in truth["parameters"].items()
    }
    source.Close()
    return values


def make_corner(
    tree: object,
    diagnostics: dict[str, object],
    parameters: list[str],
    truth_values: dict[str, float],
    burn_in: int,
    output_dir: Path,
) -> None:
    ROOT.gStyle.SetOptStat(0)
    n = len(parameters)
    canvas = ROOT.TCanvas("corner_canvas", "posterior corner plot", 950, 950)
    canvas.Divide(n, n, 0.002, 0.002)
    keepalive = []

    ranges = {}
    for name in parameters:
        result = diagnostics["parameters"][name]
        low = float(result["mean"]) - 4.0 * float(result["sd"])
        high = float(result["mean"]) + 4.0 * float(result["sd"])
        truth = truth_values.get(name)
        if truth is not None:
            low = min(low, truth)
            high = max(high, truth)
        if not high > low:
            high = low + 1.0
        padding = 0.04 * (high - low)
        ranges[name] = (low - padding, high + padding)

    for row_index, y_name in enumerate(parameters):
        for col_index, x_name in enumerate(parameters):
            pad = canvas.cd(row_index * n + col_index + 1)
            pad.SetLeftMargin(0.15)
            pad.SetBottomMargin(0.15)
            pad.SetRightMargin(0.06)
            pad.SetTopMargin(0.06)

            x_low, x_high = ranges[x_name]
            if row_index == col_index:
                hist = ROOT.TH1D(
                    f"corner_diag_{x_name}",
                    "",
                    50,
                    x_low,
                    x_high,
                )
                hist.SetDirectory(0)
                for row in iter_tree(tree):
                    weight = post_burn_weight(row, burn_in)
                    if weight:
                        hist.Fill(float(getattr(row, x_name)), weight)
                if hist.Integral() > 0:
                    hist.Scale(1.0 / hist.Integral())
                hist.GetXaxis().SetTitle(x_name)
                hist.GetYaxis().SetTitle("posterior")
                hist.Draw("HIST")
                keepalive.append(hist)

                truth = truth_values.get(x_name)
                if truth is not None:
                    truth_line = ROOT.TLine(
                        truth, 0.0, truth, max(hist.GetMaximum(), 1.0e-12)
                    )
                    truth_line.SetLineColor(ROOT.kRed + 1)
                    truth_line.SetLineStyle(2)
                    truth_line.SetLineWidth(2)
                    truth_line.Draw("SAME")
                    keepalive.append(truth_line)
            elif row_index > col_index:
                y_low, y_high = ranges[y_name]
                hist = ROOT.TH2D(
                    f"corner_2d_{y_name}_{x_name}",
                    "",
                    40,
                    x_low,
                    x_high,
                    40,
                    y_low,
                    y_high,
                )
                hist.SetDirectory(0)
                for row in iter_tree(tree):
                    weight = post_burn_weight(row, burn_in)
                    if weight:
                        hist.Fill(
                            float(getattr(row, x_name)),
                            float(getattr(row, y_name)),
                            weight,
                        )
                hist.GetXaxis().SetTitle(x_name)
                hist.GetYaxis().SetTitle(y_name)
                hist.Draw("COL")
                keepalive.append(hist)

                truth_x = truth_values.get(x_name)
                truth_y = truth_values.get(y_name)
                if truth_x is not None and truth_y is not None:
                    truth_marker = ROOT.TMarker(truth_x, truth_y, 29)
                    truth_marker.SetMarkerColor(ROOT.kRed + 1)
                    truth_marker.SetMarkerSize(1.8)
                    truth_marker.Draw("SAME")
                    keepalive.append(truth_marker)
            else:
                pad.SetFrameFillColor(0)
                latex = ROOT.TLatex()
                latex.SetNDC(True)
                latex.SetTextAlign(22)
                latex.SetTextSize(0.13)
                corr = float(diagnostics["correlations"][y_name][x_name])
                latex.DrawLatex(0.5, 0.55, f"#rho = {corr:+.3f}")
                latex.SetTextSize(0.07)
                latex.DrawLatex(0.5, 0.38, f"{y_name} vs {x_name}")
                keepalive.append(latex)

    canvas.cd()
    save_canvas(canvas, output_dir, "corner")


def make_traces(
    tree: object,
    parameters: list[str],
    truth_values: dict[str, float],
    burn_in: int,
    chain_count: int,
    output_dir: Path,
) -> None:
    canvas = ROOT.TCanvas("trace_canvas", "MCMC traces", 1100, 850)
    canvas.Divide(1, len(parameters), 0.0, 0.002)
    colors = [
        ROOT.kBlue + 1,
        ROOT.kRed + 1,
        ROOT.kGreen + 2,
        ROOT.kMagenta + 1,
        ROOT.kOrange + 7,
        ROOT.kCyan + 2,
        ROOT.kViolet + 1,
        ROOT.kGray + 2,
    ]

    values = {parameter: defaultdict(list) for parameter in parameters}
    for row in iter_tree(tree):
        if post_burn_weight(row, burn_in) == 0:
            continue
        chain_id = int(row.chain)
        iteration = int(row.iteration)
        for parameter in parameters:
            values[parameter][chain_id].append(
                (iteration, float(getattr(row, parameter)))
            )

    keepalive = []
    for pad_index, parameter in enumerate(parameters, start=1):
        pad = canvas.cd(pad_index)
        pad.SetLeftMargin(0.10)
        pad.SetRightMargin(0.04)
        pad.SetBottomMargin(0.15 if pad_index == len(parameters) else 0.08)
        multigraph = ROOT.TMultiGraph()
        multigraph.SetTitle(f"{parameter};iteration;{parameter}")
        legend = ROOT.TLegend(0.82, 0.12, 0.96, 0.88)
        legend.SetBorderSize(0)
        legend.SetFillStyle(0)

        for chain_id in range(chain_count):
            points = values[parameter][chain_id]
            stride = max(1, len(points) // 2500)
            selected = points[::stride]
            graph = ROOT.TGraph(len(selected))
            for index, (iteration, value) in enumerate(selected):
                graph.SetPoint(index, iteration, value)
            graph.SetLineColor(colors[chain_id % len(colors)])
            graph.SetLineWidth(1)
            multigraph.Add(graph, "L")
            if pad_index == 1:
                legend.AddEntry(graph, f"chain {chain_id:02d}", "l")
            keepalive.append(graph)

        multigraph.Draw("A")
        pad.Update()

        truth = truth_values.get(parameter)
        if truth is not None:
            x_axis = multigraph.GetXaxis()
            truth_line = ROOT.TLine(
                x_axis.GetXmin(), truth, x_axis.GetXmax(), truth
            )
            truth_line.SetLineColor(ROOT.kRed + 1)
            truth_line.SetLineStyle(2)
            truth_line.SetLineWidth(2)
            truth_line.Draw("SAME")
            if pad_index == 1:
                legend.AddEntry(truth_line, "generation truth", "l")
            keepalive.append(truth_line)

        if pad_index == 1:
            legend.Draw()
            keepalive.append(legend)
        keepalive.append(multigraph)

    canvas.cd()
    save_canvas(canvas, output_dir, "traces")


def weighted_posterior_states(
    tree: object,
    parameters: list[str],
    burn_in: int,
    count: int,
) -> list[dict[str, float]]:
    states = []
    total = 0
    for row in iter_tree(tree):
        weight = post_burn_weight(row, burn_in)
        if weight:
            states.append(
                (
                    total,
                    total + weight,
                    {name: float(getattr(row, name)) for name in parameters},
                )
            )
            total += weight
    if total == 0:
        raise ValueError("posterior contains no post-burn-in samples")

    targets = [int((index + 0.5) * total / count) for index in range(count)]
    selected = []
    state_index = 0
    for target in targets:
        while state_index + 1 < len(states) and target >= states[state_index][1]:
            state_index += 1
        selected.append(states[state_index][2])
    return selected


def make_predictive(
    model_path: Path,
    tree: object,
    diagnostics: dict[str, object],
    parameters: list[str],
    burn_in: int,
    output_dir: Path,
) -> None:
    source = ROOT.TFile.Open(str(model_path), "READ")
    if not source or source.IsZombie():
        raise OSError(f"could not open {model_path}")
    workspace = source.Get("workspace")
    truth_named = source.Get("truth_json")
    if not workspace or not truth_named:
        raise ValueError(f"{model_path}: workspace or truth metadata missing")
    truth = json.loads(truth_named.GetTitle())

    x = workspace.var("x")
    pdf = workspace.pdf("langau")
    data = workspace.data("data")
    if not x or not pdf or not data:
        raise ValueError("model workspace is incomplete")

    nbins = int(truth["observable"]["bins"])
    x_min = float(truth["observable"]["min"])
    x_max = float(truth["observable"]["max"])
    width = (x_max - x_min) / nbins
    event_count = float(data.sumEntries())

    data_hist = ROOT.TH1D(
        "posterior_expected_data",
        "Langau posterior expected spectrum;x;events / bin",
        nbins,
        x_min,
        x_max,
    )
    data_hist.SetDirectory(0)
    # RooDataHist is already binned. Filling TH1 once with data.weight() would
    # treat each bin count as a single weighted event and give an error equal
    # to the bin content. Set the observed count and its Poisson sqrt(N) error
    # explicitly instead.
    for index in range(data.numEntries()):
        point = data.get(index)
        center = float(point.find("x").getVal())
        count = float(data.weight())
        bin_number = data_hist.FindBin(center)
        data_hist.SetBinContent(bin_number, count)
        data_hist.SetBinError(bin_number, math.sqrt(count) if count > 0.0 else 0.0)

    draws = weighted_posterior_states(tree, parameters, burn_in, 80)
    observables = ROOT.RooArgSet(x)
    predictions = [[] for _ in range(nbins)]

    for draw in draws:
        for name, value in draw.items():
            workspace.var(name).setVal(value)
        for bin_index in range(nbins):
            center = x_min + (bin_index + 0.5) * width
            x.setVal(center)
            expected = float(pdf.getVal(observables)) * width * event_count
            predictions[bin_index].append(expected)

    band = ROOT.TGraphAsymmErrors(nbins)
    median = ROOT.TGraph(nbins)
    truth_curve = ROOT.TGraph(nbins)
    residual_band = ROOT.TGraphAsymmErrors(nbins)
    residual_truth = ROOT.TGraph(nbins)
    residual_hist = ROOT.TH1D(
        "posterior_expected_residuals",
        "",
        nbins,
        x_min,
        x_max,
    )
    residual_hist.SetDirectory(0)

    truth_values = truth["parameters"]
    for name, value in truth_values.items():
        workspace.var(name).setVal(float(value))

    residual_extent = 0.0
    for bin_index in range(nbins):
        center = x_min + (bin_index + 0.5) * width
        values = predictions[bin_index]
        low = quantile(values, 0.16)
        mid = quantile(values, 0.50)
        high = quantile(values, 0.84)

        band.SetPoint(bin_index, center, mid)
        band.SetPointError(
            bin_index, width / 2.0, width / 2.0, mid - low, high - mid
        )
        median.SetPoint(bin_index, center, mid)

        # Put the same posterior-expected interval into residual coordinates.
        # The residual is defined relative to the posterior median, so the
        # expected-spectrum band is centered on zero.
        residual_band.SetPoint(bin_index, center, 0.0)
        residual_band.SetPointError(
            bin_index, width / 2.0, width / 2.0, mid - low, high - mid
        )

        x.setVal(center)
        expected_truth = float(pdf.getVal(observables)) * width * event_count
        truth_curve.SetPoint(bin_index, center, expected_truth)
        residual_truth.SetPoint(bin_index, center, expected_truth - mid)

        bin_number = bin_index + 1
        count = float(data_hist.GetBinContent(bin_number))
        count_error = float(data_hist.GetBinError(bin_number))
        residual = count - mid
        residual_hist.SetBinContent(bin_number, residual)
        residual_hist.SetBinError(bin_number, count_error)

        residual_extent = max(
            residual_extent,
            abs(residual) + count_error,
            mid - low,
            high - mid,
            abs(expected_truth - mid),
        )

    canvas = ROOT.TCanvas(
        "predictive_canvas", "Langau posterior expected spectrum", 1000, 850
    )
    top_pad = ROOT.TPad("predictive_top", "spectrum", 0.0, 0.30, 1.0, 1.0)
    residual_pad = ROOT.TPad(
        "predictive_residual", "residuals", 0.0, 0.0, 1.0, 0.30
    )
    top_pad.SetLeftMargin(0.11)
    top_pad.SetRightMargin(0.04)
    top_pad.SetTopMargin(0.08)
    top_pad.SetBottomMargin(0.02)
    residual_pad.SetLeftMargin(0.11)
    residual_pad.SetRightMargin(0.04)
    residual_pad.SetTopMargin(0.02)
    residual_pad.SetBottomMargin(0.30)
    top_pad.Draw()
    residual_pad.Draw()

    top_pad.cd()
    data_hist.SetMinimum(0.0)
    data_hist.SetMarkerStyle(20)
    data_hist.SetMarkerSize(0.8)
    data_hist.SetMarkerColor(ROOT.kBlack)
    data_hist.SetLineColor(ROOT.kBlack)
    data_hist.GetXaxis().SetTitle("")
    data_hist.GetXaxis().SetLabelSize(0.0)
    data_hist.GetYaxis().SetTitle("events / bin")
    data_hist.Draw("E1")

    band.SetFillColorAlpha(ROOT.kAzure - 9, 0.65)
    band.SetLineColor(ROOT.kAzure + 2)
    band.SetLineWidth(1)
    band.Draw("3 SAME")
    median.SetLineColor(ROOT.kBlue + 2)
    median.SetLineWidth(2)
    median.Draw("L SAME")
    truth_curve.SetLineColor(ROOT.kRed + 1)
    truth_curve.SetLineStyle(2)
    truth_curve.SetLineWidth(2)
    truth_curve.Draw("L SAME")
    data_hist.Draw("E1 SAME")

    legend = ROOT.TLegend(0.59, 0.66, 0.94, 0.90)
    legend.SetBorderSize(0)
    legend.SetFillStyle(0)
    legend.AddEntry(data_hist, "synthetic data (Poisson errors)", "lep")
    legend.AddEntry(band, "posterior expected spectrum 68%", "f")
    legend.AddEntry(median, "posterior median expected spectrum", "l")
    legend.AddEntry(truth_curve, "generation truth", "l")
    legend.Draw()

    residual_pad.cd()
    residual_limit = max(1.0, residual_extent) * 1.15
    residual_hist.SetMinimum(-residual_limit)
    residual_hist.SetMaximum(residual_limit)
    residual_hist.SetMarkerStyle(20)
    residual_hist.SetMarkerSize(0.7)
    residual_hist.SetMarkerColor(ROOT.kBlack)
    residual_hist.SetLineColor(ROOT.kBlack)
    residual_hist.GetXaxis().SetTitle("x [ADC]")
    residual_hist.GetYaxis().SetTitle("data - posterior median [events / bin]")
    residual_hist.GetXaxis().SetTitleSize(0.11)
    residual_hist.GetXaxis().SetLabelSize(0.09)
    residual_hist.GetXaxis().SetTitleOffset(1.05)
    residual_hist.GetYaxis().SetTitleSize(0.09)
    residual_hist.GetYaxis().SetLabelSize(0.08)
    residual_hist.GetYaxis().SetTitleOffset(0.55)
    residual_hist.GetYaxis().SetNdivisions(505)
    residual_hist.Draw("E1")

    residual_band.SetFillColorAlpha(ROOT.kAzure - 9, 0.65)
    residual_band.SetLineColor(ROOT.kAzure + 2)
    residual_band.SetLineWidth(1)
    residual_band.Draw("3 SAME")
    residual_truth.SetLineColor(ROOT.kRed + 1)
    residual_truth.SetLineStyle(2)
    residual_truth.SetLineWidth(2)
    residual_truth.Draw("L SAME")
    zero_line = ROOT.TLine(x_min, 0.0, x_max, 0.0)
    zero_line.SetLineColor(ROOT.kGray + 2)
    zero_line.SetLineStyle(2)
    zero_line.Draw("SAME")
    residual_hist.Draw("E1 SAME")

    canvas.cd()
    save_canvas(canvas, output_dir, "posterior_predictive")
    source.Close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Make ROOT plots for MCMC example.")
    parser.add_argument("model", type=Path)
    parser.add_argument("posterior", type=Path)
    parser.add_argument("diagnostics", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()

    ROOT.gROOT.SetBatch(True)
    ROOT.gStyle.SetOptStat(0)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    diagnostics = json.loads(args.diagnostics.read_text())
    parameters = list(diagnostics["parameters"])
    burn_in = int(diagnostics["burn_in"])
    chain_count = int(diagnostics["chains"])
    truth_values = load_truth_parameters(args.model)

    posterior = ROOT.TFile.Open(str(args.posterior), "READ")
    if not posterior or posterior.IsZombie():
        raise OSError(f"could not open {args.posterior}")
    tree = posterior.Get("samples")
    if not tree:
        raise ValueError(f"{args.posterior}: samples tree not found")

    make_corner(
        tree,
        diagnostics,
        parameters,
        truth_values,
        burn_in,
        args.output_dir,
    )
    make_traces(
        tree,
        parameters,
        truth_values,
        burn_in,
        chain_count,
        args.output_dir,
    )
    make_predictive(
        args.model, tree, diagnostics, parameters, burn_in, args.output_dir
    )
    posterior.Close()
    print("wrote corner, trace, and posterior expected-spectrum plots")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())