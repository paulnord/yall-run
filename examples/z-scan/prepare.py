#!/usr/bin/env python3
import json
from pathlib import Path
import sys

import ROOT

MASS_TRUTH = 91.1876
WIDTH_TRUTH = 2.4952
SIGMA_DETECTOR = 1.5
EVENTS = 8000
SEED = 20260903
X_MIN = 70.0
X_MAX = 110.0
BINS = 160


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: prepare.py OUTPUT.root")
    output = Path(sys.argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    rng = ROOT.TRandom3(SEED)
    hist = ROOT.TH1D("mass", "Synthetic Z -> l^{+}l^{-};m_{ll} [GeV];events / bin", BINS, X_MIN, X_MAX)
    while int(hist.GetEntries()) < EVENTS:
        value = rng.BreitWigner(MASS_TRUTH, WIDTH_TRUTH) + rng.Gaus(0.0, SIGMA_DETECTOR)
        if X_MIN <= value < X_MAX:
            hist.Fill(value)
    truth = {"mass_gev": MASS_TRUTH, "width_gev": WIDTH_TRUTH, "detector_sigma_gev": SIGMA_DETECTOR, "events": EVENTS, "seed": SEED, "range_gev": [X_MIN, X_MAX], "bins": BINS}
    root_file = ROOT.TFile(str(output), "RECREATE")
    hist.Write()
    ROOT.TNamed("truth_json", json.dumps(truth, sort_keys=True)).Write()
    root_file.Close()
    print(json.dumps(truth, sort_keys=True))


if __name__ == "__main__":
    main()
