#!/usr/bin/env python3
from array import array
import json
from pathlib import Path
import sys

import ROOT

TAU_US = 2.1969811
WINDOW_US = 12.0


def read_config(path):
    fields = Path(path).read_text().split()
    if len(fields) != 3:
        raise ValueError("run config must contain: EVENTS SEED BACKGROUND_FRACTION")
    events = int(fields[0])
    seed = int(fields[1])
    background_fraction = float(fields[2])
    if events <= 0 or not (0.0 <= background_fraction < 1.0):
        raise ValueError("invalid run configuration")
    return events, seed, background_fraction


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: simulate.py RUN_CONFIG OUTPUT.root")
    config_path, output_path = sys.argv[1:]
    events, seed, background_fraction = read_config(config_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    rng = ROOT.TRandom3(seed)
    root_file = ROOT.TFile(str(output), "RECREATE")
    tree = ROOT.TTree("decays", "synthetic stopped-muon decay times")
    t_us = array("d", [0.0])
    tree.Branch("t_us", t_us, "t_us/D")

    for _ in range(events):
        if rng.Uniform() < background_fraction:
            value = rng.Uniform(0.0, WINDOW_US)
        else:
            value = rng.Exp(TAU_US)
            while value >= WINDOW_US:
                value = rng.Exp(TAU_US)
        t_us[0] = value
        tree.Fill()

    metadata = {
        "tau_us": TAU_US,
        "window_us": WINDOW_US,
        "events": events,
        "seed": seed,
        "background_fraction": background_fraction,
    }
    tree.Write()
    ROOT.TNamed("truth_json", json.dumps(metadata, sort_keys=True)).Write()
    root_file.Close()
    print(json.dumps(metadata, sort_keys=True))


if __name__ == "__main__":
    main()
