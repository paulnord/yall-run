# YAWL-run

<p align="center">
  <img src="docs/images/yawl-run-logo.png" alt="YAWL-run logo" width="400">
</p>

**Yet Another Workflow Layer**  
**Y'all run!**
**Yet Another Workflow Layer**  
**Y'all run!**

YAWL-run is a deliberately small campaign runner for reproducible analysis work. It sits *above* a batch system rather than trying to become one.

The core model is:

```text
campaign
  task
    attempt
```

The same campaign specification can run locally or through HTCondor/DAGMan. YAWL-run owns campaign identity, stable task names, dependencies, retry history, logs, lightweight file provenance, and backend adapters. Condor still owns scheduling, resource matching, queue policy, holds, and execution hosts.

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

## Task commands and provenance

A task command may be a shell string for convenience:

```toml
[[task]]
name = "quick-look"
command = "echo hello > hello.txt"
```

For analysis jobs, an argv array avoids shell parsing and preserves the executable arguments exactly:

```toml
[[task]]
name = "convert"
command = ["./Convert", "-i", "Run300.h2g", "-o", "rawHGCROC_300.root"]
inputs = [
  {role = "raw_h2g", path = "Run300.h2g"},
]
outputs = [
  {role = "raw_root", path = "rawHGCROC_300.root"},
]
```

For every attempt, YAWL records the resolved input paths and their existence, size, type, and modification time at launch. Outputs are inspected after the command finishes. These records live beside stdout/stderr in `tasks/<name>/attempts/NNN/attempt.json`.

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
  yawl_worker.py
  yawl_0000_left.sub
  yawl_0000_left.sh
  yawl_0001_right.sub
  yawl_0001_right.sh
  yawl_0002_finish.sub
  yawl_0002_finish.sh
  logs/
```

Inspect that rendered campaign, then submit that exact artifact:

```bash
yawl-run submit ./campaigns/<campaign-id>
```

A rendered campaign can be submitted only once. This keeps the reviewed DAG and the submitted DAG identical.

You can also submit directly without a review step:

```bash
yawl-run start examples/condor-dag.toml --root ./campaigns
```

DAGMan retries invoke the YAWL worker again, so a Condor retry becomes attempt `002`, `003`, etc. in the durable YAWL campaign record rather than living only in scheduler history.

Check state with:

```bash
yawl-run status ./campaigns/<campaign-id>
```

For active Condor campaigns, status reports the DAGMan controller separately from its DAG nodes and maps active `DAGNodeName` values back to YAWL task names.

## Condor execution wrapper

A site or container wrapper can be configured without teaching YAWL anything detector-specific:

```toml
[condor]
request_cpus = 1
request_memory = "4GB"
request_disk = "2GB"
wrapper = "/path/to/run-in-container.sh"
```

When the campaign is rendered, YAWL copies the wrapper into `environment/`, records its source path, size, and SHA-256, and makes each Condor node invoke the bundled YAWL worker through that archived wrapper. Environment variables needed by the wrapper can still be inherited with `getenv = true`.

## Example DAG campaign

```toml
[campaign]
name = "condor-dag-demo"
backend = "condor"

[[task]]
name = "left"
command = ["./left-analysis"]
retries = 1

[[task]]
name = "right"
command = ["./right-analysis"]
retries = 1

[[task]]
name = "compare"
command = ["./compare-results"]
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

Prototype. Small on purpose, but with a real HTCondor/DAGMan backend and a durable campaign record.
