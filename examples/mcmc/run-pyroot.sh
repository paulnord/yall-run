#!/usr/bin/env bash
set -euo pipefail

if [[ $# -eq 0 ]]; then
    echo "run-pyroot: command required" >&2
    exit 2
fi

# Prefer an ordinary local PyROOT installation when one is already usable.
# This keeps the example portable outside EIC sites and avoids an unnecessary
# container boundary for developers who already have ROOT.
if [[ "${YAWL_MCMC_FORCE_EIC:-0}" != "1" ]] && \
   python3 -c 'import ROOT; assert hasattr(ROOT, "RooStats"); assert hasattr(ROOT.RooStats, "MCMCCalculator")' \
       >/dev/null 2>&1; then
    exec "$@"
fi

# A generated outer eic-shell script is the most portable container entry
# point for users who installed the standard EIC environment themselves.  The
# outer script owns container startup and requires -- before a one-shot command.
if [[ -n "${YAWL_MCMC_EIC_SHELL:-}" ]]; then
    if [[ ! -f "$YAWL_MCMC_EIC_SHELL" ]]; then
        echo "run-pyroot: YAWL_MCMC_EIC_SHELL does not exist: $YAWL_MCMC_EIC_SHELL" >&2
        exit 2
    fi
    exec "$YAWL_MCMC_EIC_SHELL" -- "$@"
fi

# BNL and JLab normally expose the EIC images directly through CVMFS.  Either
# variable can override the default, with LFHCAL_CONTAINER_IMAGE retained for
# compatibility with the LFHCal analysis wrapper that inspired this launcher.
image=${YAWL_MCMC_EIC_IMAGE:-${LFHCAL_CONTAINER_IMAGE:-/cvmfs/singularity.opensciencegrid.org/eicweb/eic_xl:nightly}}

if [[ ! -e "$image" ]]; then
    cat >&2 <<EOF
run-pyroot: PyROOT/RooStats is not available in the host python3 and the EIC image was not found:
  $image

Install/use the standard EIC environment, or set one of:
  YAWL_MCMC_EIC_SHELL=/path/to/eic-shell
  YAWL_MCMC_EIC_IMAGE=/path/to/eic_xl image
EOF
    exit 2
fi

if command -v apptainer >/dev/null 2>&1; then
    runtime=$(command -v apptainer)
elif command -v singularity >/dev/null 2>&1; then
    runtime=$(command -v singularity)
else
    echo "run-pyroot: neither apptainer nor singularity was found" >&2
    exit 127
fi

# Preserve the shared paths normally used by EIC jobs.  Singularity/Apptainer
# already bind $HOME on most sites; these extra paths cover CVMFS and the BNL
# data/work filesystems used by the example when it is run from STAR.
bind_path=""
for candidate in /cvmfs /media /gpfs /gpfs01 /gpfs02 /direct /star; do
    if [[ -e "$candidate" ]]; then
        if [[ -n "$bind_path" ]]; then
            bind_path+=,
        fi
        bind_path+=$candidate
    fi
done

if [[ -n "$bind_path" ]]; then
    if [[ "$(basename "$runtime")" == "apptainer" ]]; then
        export APPTAINER_BINDPATH="${APPTAINER_BINDPATH:+${APPTAINER_BINDPATH},}${bind_path}"
    else
        export SINGULARITY_BINDPATH="${SINGULARITY_BINDPATH:+${SINGULARITY_BINDPATH},}${bind_path}"
    fi
fi

# The eic_xl image already contains the EIC environment.  When starting the
# image directly, execute the payload directly inside it.  Do not invoke the
# outer eic-shell helper again from inside the container.
exec "$runtime" exec "$image" "$@"
