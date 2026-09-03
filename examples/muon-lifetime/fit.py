#!/usr/bin/env python3
import json
import math
from pathlib import Path
import sys

import ROOT


def main():
    if len(sys.argv) != 4:
        raise SystemExit("usage: fit.py INPUT.root FIT.json FIT.root")
    input_path, json_path, fit_root_path = map(Path, sys.argv[1:])
    json_path.parent.mkdir(parents=True, exist_ok=True)
    fit_root_path.parent.mkdir(parents=True, exist_ok=True)

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
    source.Close()
    print(json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    main()
