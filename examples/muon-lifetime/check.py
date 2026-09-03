#!/usr/bin/env python3
import json
from pathlib import Path
import sys


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: check.py FIT.json CHECKED.json")
    input_path, output_path = map(Path, sys.argv[1:])
    fit = json.loads(input_path.read_text())
    tau = float(fit["tau_us"])
    error = float(fit["tau_error_us"])
    if int(fit["fit_status"]) != 0:
        raise RuntimeError("ROOT fit failed with status {}".format(fit["fit_status"]))
    if not (1.4 < tau < 3.0):
        raise RuntimeError("lifetime outside broad sanity window: {}".format(tau))
    if not (0.0 < error < 1.0):
        raise RuntimeError("invalid lifetime uncertainty: {}".format(error))
    fit["checked"] = True
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(fit, indent=2, sort_keys=True) + "\n")
    print("checked tau={:.6f} +/- {:.6f} us".format(tau, error))


if __name__ == "__main__":
    main()
