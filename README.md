# YAWL-run

**Yet Another Workflow Layer**  
**Y'all run!**

YAWL-run is a deliberately small campaign runner for reproducible analysis work. It sits *above* a batch system rather than trying to become one.

The core model is:

```text
campaign
  task
    attempt
```

The same campaign specification can run locally or through HTCondor/DAGMan. YAWL-run owns campaign identity, stable task names, dependencies, retry history, logs, provenance, and backend adapters. Condor still owns scheduling, resource matching, queue policy, holds, and execution hosts.

> Naming note: YAWL-run is not the YAWL (Yet Another Workflow Language) workflow system.

## Install

Requires Python 3.9+.

```bash
python3 -m pip install -e .
```

## Local smoke test

```bash
yawl-run validate examples/hello.toml
yawl-run plan examples/hello.toml
yawl-run start examples/hello.toml --root ./campaigns
yawl-run status ./campaigns/<campaign-id>
```

## Condor / DAGMan

`examples/condor-dag.toml` demonstrates sibling jobs, retries, and a child that waits for both parents.

First render everything without submitting:

```bash
yawl-run validate examples/condor-dag.toml
yawl-run plan examples/condor-dag.toml
yawl-run start examples/condor-dag.toml --root ./campaigns --dry-run
```

The generated campaign contains:

```text
condor/
  campaign.dag
  yawl_0000_left.sub
  yawl_0000_left.sh
  yawl_0001_right.sub
  yawl_0001_right.sh
  yawl_0002_finish.sub
  yawl_0002_finish.sh
  logs/
```

When the DAG looks right, submit for real:

```bash
yawl-run start examples/condor-dag.toml --root ./campaigns
```

or override a campaign's configured backend:

```bash
yawl-run start examples/hello.toml --backend condor --root ./campaigns --dry-run
```

DAGMan retries invoke the YAWL worker again, so a Condor retry becomes attempt `002`, `003`, etc. in the durable YAWL campaign record rather than living only in scheduler history.

Check state with:

```bash
yawl-run status ./campaigns/<campaign-id>
```

For Condor campaigns, status also queries the DAGMan cluster when it is still present in `condor_q`.

## Example DAG campaign

```toml
[campaign]
name = "condor-dag-demo"
backend = "condor"

[[task]]
name = "left"
command = "./left-analysis"
retries = 1

[[task]]
name = "right"
command = "./right-analysis"
retries = 1

[[task]]
name = "compare"
command = "./compare-results"
parents = ["left", "right"]
```

That renders the DAGMan relationship:

```text
left  --\
         > compare
right --/
```

## Design rule

If a feature can be described without mentioning LFHCal, HGCROC, a particular run number, ROOT histograms, or detector-specific conventions, it may belong in YAWL-run. Otherwise it belongs in the analysis-specific layer.

## Status

Prototype. Small on purpose, but now with a real HTCondor/DAGMan backend.
