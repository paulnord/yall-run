# yawl-run

<p align="center">
  <img src="docs/images/yawl-run-logo.png" alt="yawl-run logo" width="400">
</p>

**Yet Another Workflow Layer**  
**Y'all run!**

yawl-run is a deliberately small campaign runner for reproducible analysis work. It sits above a batch system rather than trying to become one.

The core model is:

```text
campaign
  task
    attempt
```

The same campaign can run locally or through HTCondor/DAGMan. yawl-run owns campaign identity, stable task names, dependencies, retry history, logs, lightweight file provenance, and backend adapters. Condor still owns scheduling, resource matching, queue policy, holds, and execution hosts.

> Naming note: yawl-run is not the YAWL (Yet Another Workflow Language) workflow system.

## Install

Requires Python 3.9+.

```bash
python3 -m pip install -e .
```

## A small Yawlfile

The campaign format is intentionally Make-like:

```text
campaign hello-yawl
backend local

left:
    echo left says hello

right:
    echo right says hello

finish: left right
    echo both parents finished
```

Save that as `Yawlfile`, then:

```bash
yawl-run validate
yawl-run plan
yawl-run start --root ./campaigns
```

There is one campaign language: Yawlfile syntax. Old TOML campaign files are not supported.

## Data is `@`, execution policy is `%`

Named inputs and outputs can be reused directly in the command:

```text
convert:
    @input raw raw/run137.h2g
    @output root converted/raw_137.root

    %retry 1
    %cpus 2
    %memory 4GB

    ./Convert -i @input.raw -o @output.root
```

`@input.raw` and `@output.root` expand into argv elements. A role can contain several paths, in which case the reference expands to several argv elements.

`@` is for data and named values. `%` is for execution policy. Resource requests are generic yawl-run concepts; the Condor backend translates them into scheduler directives.

Commands are argv arrays by default. If a task deliberately needs shell syntax, prefix the command with `!`:

```text
report:
    @output text report.txt
    ! echo complete > @output.text
```

## Pattern tasks

For a family of files where each input should produce its own output, `@each` expands one rule into one task per matching file:

```text
@set dataset beam2026

pedestal-{run}:
    @each raw converted/{dataset}_raw_{run}.root
    @output pedestal pedestal/{dataset}_pedestal_{run}.root
    ./make-pedestal @input.raw -o @output.pedestal
```

Files such as:

```text
converted/beam2026_raw_137.root
converted/beam2026_raw_138.root
converted/beam2026_raw_142.root
```

produce:

```text
pedestal-137
pedestal-138
pedestal-142
```

A patterned child naturally follows the same family. A plain task depending on a patterned parent fans in from the whole family.

See [docs/YAWLFILE.md](docs/YAWLFILE.md) for the format details.

## Map, then reduce: the pi example

`examples/pi` is a complete Condor-ready example of reusable code and fan-out/fan-in dependencies. It evaluates the Leibniz series

```text
pi = 4 * (1 - 1/3 + 1/5 - 1/7 + ...)
```

in eight independent chunks. Every worker runs the same `partial_pi.py` program. The final `sum` task waits for the whole `partial-{chunk}` family and receives all partial result files through one named input.

```bash
cd examples/pi
yawl-run validate
yawl-run plan
yawl-run start --dry-run
```

The last command renders the Condor DAG without submitting it. Submit the exact rendered campaign with `yawl-run submit CAMPAIGN_DIR`.

For a local end-to-end smoke test:

```bash
yawl-run start --backend local
cat pi-work/pi.txt
```

## Task commands and provenance

For every attempt, yawl-run records the resolved input paths and their existence, size, type, and modification time at launch. Outputs are inspected after the command finishes.

Task state is kept compactly in `tasks/<name>.json`. Human-facing attempt directories sit directly under the campaign directory:

```text
campaign.json
provenance.json
tasks/
  partial-000.json
  partial-001.json
  sum.json
partial-000_attempt_001/
  attempt.json
  stdout.log
  stderr.log
partial-001_attempt_001/
  attempt.json
  stdout.log
  stderr.log
sum_attempt_001/
  attempt.json
  stdout.log
  stderr.log
```

Retries become `sum_attempt_002`, `sum_attempt_003`, and so on. This keeps the detailed provenance while making the directories people actually inspect easy to reach.

## Condor / DAGMan

First render everything without submitting:

```bash
yawl-run start --backend condor --root ./campaigns --dry-run
```

The generated campaign contains the DAG, per-node submit files, a bundled worker, scheduler logs, and the durable yawl-run task records.

Inspect that rendered campaign, then submit that exact artifact:

```bash
yawl-run submit ./campaigns/<campaign-id>
```

A rendered campaign can be submitted only once. This keeps the reviewed DAG and the submitted DAG identical.

DAGMan retries invoke the yawl worker again, so a Condor retry becomes attempt `002`, `003`, etc. in the durable yawl campaign record rather than living only in scheduler history.

Check state with:

```bash
yawl-run status ./campaigns/<campaign-id>
```

For active Condor campaigns, status reports the DAGMan controller separately from its DAG nodes and maps active `DAGNodeName` values back to yawl task names.

## Condor execution wrapper

A site or container wrapper can be configured without teaching yawl-run anything detector-specific:

```text
backend condor
%cpus 1
%memory 4GB
%disk 2GB
%wrapper /path/to/run-in-container.sh
```

When the campaign is rendered, yawl-run copies the wrapper into `environment/`, records its source path, size, and SHA-256, and makes each Condor node invoke the bundled yawl worker through that archived wrapper. Environment variables needed by the wrapper can still be inherited with `%getenv true`.

## Design rule

If a feature can be described without mentioning LFHCal, HGCROC, a particular run number, ROOT histograms, or detector-specific conventions, it may belong in yawl-run. Otherwise it belongs in the analysis-specific layer.

## Status

0.5: one human-readable campaign format, local and Condor/DAGMan backends, pattern-task fan-out/fan-in, named data references, resource policy, and durable attempt provenance.
