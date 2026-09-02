# PyROOT RooStats MCMC: a Landau-Gaussian posterior

This example runs eight independent Bayesian Markov chains for a synthetic
Landau convolved with Gaussian ("Langau") spectrum. Unlike the small numerical
examples, the statistical engine here is ROOT itself:

- `RooLandau` and `RooGaussian` define the components.
- `RooFFTConvPdf` performs the numerical convolution.
- `RooStats::MCMCCalculator` runs Metropolis-Hastings chains.
- each chain is written to its own ROOT file;
- the chains are combined, checked with split R-hat, and plotted with ROOT.

The workflow is intended both as a realistic yawl-run example and as a compact
starting point for MCMC studies of detector spectra.

## Model

The likelihood is a Landau distribution convolved with a Gaussian detector
resolution:

```text
f(x | m, sigma_L, sigma_G) = Landau(x; m, sigma_L) * Gaussian(x; 0, sigma_G)
```

with three floating parameters:

- `mpv`: Landau location,
- `landau_width`: Landau width,
- `gauss_sigma`: Gaussian detector resolution.

The synthetic data are generated at

```text
mpv = 35
landau_width = 4.5
gauss_sigma = 2.5
```

`mpv` is the RooStats parameter of interest; the two width parameters are
registered as nuisance parameters, but all three are sampled and included in
the diagnostics and corner plot. Uniform priors are used over the finite
parameter ranges encoded in the `RooRealVar`s. The data are binned before
entering the MCMC likelihood, while the convolution itself uses 2048 cache bins.

This is deliberately a clean Langau problem. Pedestal/background components
can be added later without changing the workflow shape.

## Why eight chains?

A Metropolis-Hastings chain is serial: proposal n+1 depends on the state reached
at proposal n. The useful parallelism is therefore *between* independent
chains, not within one chain.

```text
prepare
   |
   +-- chain-00 --+
   +-- chain-01 --+
   +-- chain-02 --+
   +-- chain-03 --+--> combine --> diagnose --> plots
   +-- chain-04 --+
   +-- chain-05 --+
   +-- chain-06 --+
   +-- chain-07 --+
```

With eight available CPUs, eight chains can accumulate roughly eight times the
posterior exploration in about the wall time of one chain. More importantly,
independent seeds give an actual convergence test: chains that have not mixed
to the same posterior will show up in the trace plots and split R-hat.

Each chain descriptor in `chains/` contains

```text
ITERATIONS BURN_IN
```

The chain number comes from the filename (`chain-00.txt`, etc.), and the
reproducible seed is `41001 + chain_id`. The committed example uses 50,000
iterations with 5,000 burn-in iterations per chain. Edit those tiny descriptor
files if you want a shorter smoke test or a longer statistical run.

## PyROOT and the EIC container

You do not need to install PyROOT into the host Python just to run this example.
Every ROOT-dependent task goes through `run-pyroot.sh`, which chooses an
execution environment in this order:

1. If the host `python3` can import ROOT and expose
   `RooStats.MCMCCalculator`, use it directly.
2. If `YAWL_MCMC_EIC_SHELL` names a generated outer `eic-shell` script, use it.
3. Otherwise use an EIC container image with Apptainer or Singularity.

For the container path, the launcher checks `YAWL_MCMC_EIC_IMAGE`, then the
LFHCal-compatible `LFHCAL_CONTAINER_IMAGE`, then defaults to the standard EIC
CVMFS image used at sites such as BNL and JLab:

```text
/cvmfs/singularity.opensciencegrid.org/eicweb/eic_xl:nightly
```

The launcher executes the payload through `eic-shell` inside that image so the
ROOT/RooFit/RooStats environment is initialized before `python3` starts. It
also binds the common EIC/BNL shared paths that exist on the host.

On a BNL or JLab machine with that CVMFS image visible, the ordinary run command
below should therefore require no separate ROOT installation. To select a
different image in csh/tcsh:

```csh
setenv YAWL_MCMC_EIC_IMAGE /path/to/eic_xl-image
```

If you already have a generated EIC shell instead:

```csh
setenv YAWL_MCMC_EIC_SHELL /path/to/eic-shell
```

Set `YAWL_MCMC_FORCE_EIC=1` if you want to use the container even when the host
Python happens to have PyROOT.

For a reproducibility-sensitive scientific calculation, prefer a pinned EIC
container release or immutable image rather than `nightly`.

## Outputs

The final directory contains:

```text
mcmc-work/
    model.root
    chain-00.root
    ...
    chain-07.root
    posterior.root
    diagnostics.json
    summary.txt
    corner.pdf
    corner.png
    traces.pdf
    traces.png
    posterior_predictive.pdf
    posterior_predictive.png
```

`corner` contains one-dimensional posteriors on the diagonal, pairwise
two-dimensional posterior densities below the diagonal, and correlation
coefficients above it.

`traces` overlays all eight chains for each model parameter.

`posterior_predictive` compares the synthetic data with the posterior median
prediction, a central 68% posterior-predictive band, and the known generation
truth.

The ROOT chain files store the Markov chain in compressed form. Rejected
Metropolis proposals are represented by the `weight` of the retained state
rather than by duplicating rows. `diagnose.py` expands those weights logically
when calculating posterior summaries and split R-hat.

## Run

```bash
cd examples/mcmc
yawl-run validate
yawl-run plan
yawl-run create -j 8 | yawl-run start
cat mcmc-work/summary.txt
```

On a high-latency shared filesystem, put the yawl campaign record on local
scratch if you are measuring orchestration overhead:

```bash
yawl-run create -j 8 --campaigns-dir /tmp/yawl-campaigns | yawl-run start
```

The scientific outputs still go to `mcmc-work/` in the example directory.

## Reproducibility

The generated dataset has a fixed seed. Each MCMC chain has a separate fixed
seed derived from its descriptor filename and recorded in ROOT metadata, so
rerunning the same example with the same ROOT version and execution image should
reproduce the same stochastic calculation closely. ROOT/image details and yawl
task provenance remain separate concerns: yawl records how the tasks ran, while
the ROOT files record the statistical samples they produced.
