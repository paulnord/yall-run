# ROOT invariant-mass reconstruction

This example mimics a tiny collider-analysis production. Six independent synthetic runs contain pion-pair four-vectors. Each run is generated and then reconstructed independently; the final task merges the per-run mass histograms and fits the combined peak.

```text
generate-00 -> reconstruct-00 --+
generate-01 -> reconstruct-01 --+
...                              +--> merge
generate-05 -> reconstruct-05 --+
```

Signal events are two-body `K_S^0 -> pi+ pi-` decays with `m(K_S^0) = 497.611 MeV`. The daughters are boosted to the lab, smeared with a small momentum resolution, and mixed with combinatorial background pairs. `reconstruct.py` uses `TLorentzVector` to rebuild the invariant mass and fits a Gaussian plus linear background.

This is deliberately closer to ordinary HEP event processing than the numerical examples: many small ROOT files are processed independently and then merged.

The ROOT environment is exactly the MCMC launcher, `../mcmc/run-pyroot.sh`.

```bash
cd examples/invariant-mass
yawl-run validate
yawl-run plan
yawl-run create -j 6 | yawl-run start
cat mass-work/summary.txt
```
