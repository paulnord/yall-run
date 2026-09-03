#!/usr/bin/env python3
from array import array
import json
import math
from pathlib import Path
import sys

import ROOT

MASS_KS = 0.497611
MASS_PI = 0.13957039


def read_config(path):
    fields = Path(path).read_text().split()
    if len(fields) != 4:
        raise ValueError("run config must contain: EVENTS SEED MOMENTUM_RESOLUTION BACKGROUND_FRACTION")
    return int(fields[0]), int(fields[1]), float(fields[2]), float(fields[3])


def random_pion(rng):
    pt = rng.Exp(0.45)
    eta = rng.Uniform(-2.0, 2.0)
    phi = rng.Uniform(0.0, 2.0 * math.pi)
    px = pt * math.cos(phi)
    py = pt * math.sin(phi)
    pz = pt * math.sinh(eta)
    energy = math.sqrt(px * px + py * py + pz * pz + MASS_PI * MASS_PI)
    return ROOT.TLorentzVector(px, py, pz, energy)


def smear_pion(vector, rng, resolution):
    scale = max(rng.Gaus(1.0, resolution), 0.05)
    px = vector.Px() * scale
    py = vector.Py() * scale
    pz = vector.Pz() * scale
    energy = math.sqrt(px * px + py * py + pz * pz + MASS_PI * MASS_PI)
    return ROOT.TLorentzVector(px, py, pz, energy)


def signal_pair(rng, resolution):
    pt = rng.Exp(0.6)
    phi = rng.Uniform(0.0, 2.0 * math.pi)
    pz = rng.Gaus(1.5, 0.8)
    px = pt * math.cos(phi)
    py = pt * math.sin(phi)
    energy = math.sqrt(px * px + py * py + pz * pz + MASS_KS * MASS_KS)
    parent = ROOT.TLorentzVector(px, py, pz, energy)
    pstar = math.sqrt(0.25 * MASS_KS * MASS_KS - MASS_PI * MASS_PI)
    costheta = rng.Uniform(-1.0, 1.0)
    sintheta = math.sqrt(max(0.0, 1.0 - costheta * costheta))
    decay_phi = rng.Uniform(0.0, 2.0 * math.pi)
    dx = pstar * sintheta * math.cos(decay_phi)
    dy = pstar * sintheta * math.sin(decay_phi)
    dz = pstar * costheta
    daughter_energy = math.sqrt(pstar * pstar + MASS_PI * MASS_PI)
    first = ROOT.TLorentzVector(dx, dy, dz, daughter_energy)
    second = ROOT.TLorentzVector(-dx, -dy, -dz, daughter_energy)
    boost = parent.BoostVector()
    first.Boost(boost)
    second.Boost(boost)
    return smear_pion(first, rng, resolution), smear_pion(second, rng, resolution)


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: generate.py RUN_CONFIG OUTPUT.root")
    config_path, output_path = sys.argv[1:]
    events, seed, resolution, background_fraction = read_config(config_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    rng = ROOT.TRandom3(seed)
    root_file = ROOT.TFile(str(output), "RECREATE")
    tree = ROOT.TTree("pairs", "synthetic pion pairs")
    names = ("px1", "py1", "pz1", "e1", "px2", "py2", "pz2", "e2")
    values = {name: array("d", [0.0]) for name in names}
    for name in names:
        tree.Branch(name, values[name], "{}/D".format(name))
    for _ in range(events):
        if rng.Uniform() < background_fraction:
            first, second = random_pion(rng), random_pion(rng)
        else:
            first, second = signal_pair(rng, resolution)
        components = (first.Px(), first.Py(), first.Pz(), first.E(), second.Px(), second.Py(), second.Pz(), second.E())
        for name, component in zip(names, components):
            values[name][0] = component
        tree.Fill()
    truth = {"mass_ks_gev": MASS_KS, "mass_pi_gev": MASS_PI, "events": events, "seed": seed, "momentum_resolution": resolution, "background_fraction": background_fraction}
    tree.Write()
    ROOT.TNamed("truth_json", json.dumps(truth, sort_keys=True)).Write()
    root_file.Close()
    print(json.dumps(truth, sort_keys=True))


if __name__ == "__main__":
    main()
