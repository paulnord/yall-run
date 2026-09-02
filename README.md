# yawl-run

<p align="center">
  <img src="docs/images/yawl-run-logo.png" alt="yawl-run logo" width="400">
</p>

**Yet Another Workflow Layer**  
**Y'all run!**

yawl-run is a deliberately small campaign runner for reproducible analysis work. It sits above a batch system rather than trying to become one.

The core model is:

```text
Yawlfile
  -> campaign
       -> task
            -> attempt
```

A `Yawlfile` is a reusable recipe, much like a Makefile. A campaign is one frozen instance of that recipe. The same Yawlfile can be instantiated for local execution or HTCondor/DAGMan. yawl-run owns campaign identity, stable task names, dependencies, retry history, logs, lightweight file provenance, and backend adapters. Condor still owns scheduling, resource matching, queue policy, holds, and execution hosts.

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
yawl-run create --root ./campaigns
```

`create` prints the new campaign directory but runs nothing. Start that exact campaign with:

```bash
yawl-run start ./campaigns/<campaign-id>
```

There is one launch operation: `start CAMPAIGN_DIR`. Creating another run means creating another campaign.

There is also one campaign language: Yawlfile syntax. Old TOML campaign files are not supported.

## Parallelism: `-j`

`-j N` limits the number of yawl tasks active at once:

```bash
yawl-run start ./campaigns/<campaign-id> -j 4
```

For the local backend, yawl runs up to `N` dependency-ready tasks concurrently. Local execution defaults to `-j 1` when no limit is given.

For Condor, yawl passes the limit to DAGMan when the frozen campaign is started. Without `-j`, Condor/DAGMan uses its normal scheduler limits.

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
yawl-run create
```

The Yawlfile declares `backend condor`, so `create` freezes the campaign and renders its DAG without submitting anything. Start the printed campaign directory with:

```bash
yawl-run start campaigns/<campaign-id> -j 4
```

For a local smoke test, create a separate local campaign from the same Yawlfile:

```bash
yawl-run create --backend local
# use the printed campaign directory
yawl-run start campaigns/<local-campaign-id> -j 4
cat pi-work/pi.txt
```

## Attempt directories and provenance

Task state is kept compactly under `tasks/`, while attempt directories are immediately visible at campaign level:

```text
campaign.json
provenance.json
start.json
tasks/
    partial-000.json
    sum.json
partial-000_attempt_001/
    provenance.json
    attempt.json
    stdout.log
    stderr.log
sum_attempt_001/
    provenance.json
    attempt.json
    stdout.log
    stderr.log
```

`provenance.json` inside each attempt is written **before** the task begins and is not rewritten afterward. It contains portable launch provenance: campaign identity, task and attempt identity, command, cwd, resolved inputs, declared outputs, requested resources, execution host, Python version, and start time.

The launched program also receives:

```text
YAWL_CAMPAIGN_ID
YAWL_CAMPAIGN_NAME
YAWL_CAMPAIGN_DIR
YAWL_BACKEND
YAWL_TASK
YAWL_ATTEMPT
YAWL_PROVENANCE
```

`YAWL_PROVENANCE` points at the attempt's launch-provenance JSON. Domain-specific programs can embed that record in their own output format. For example, LFHCal can copy it into a ROOT file without teaching generic yawl-run anything about ROOT.

After the command finishes, `attempt.json` records the return code, finish time, stdout/stderr locations, and observed output metadata. Output data may live on another persistent filesystem; the campaign directory remains the provenance anchor.

## Condor / DAGMan

For a Condor Yawlfile:

```bash
yawl-run create --root ./campaigns
```

creates the durable campaign, DAG, per-node submit files, bundled worker, scheduler log paths, and task state, but does not submit the DAG.

Inspect that campaign if desired, then launch that exact artifact:

```bash
yawl-run start ./campaigns/<campaign-id>
```

A campaign can be started only once. To run the workflow again, create a new campaign from the Yawlfile.

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

When the campaign is created, yawl-run copies the wrapper into `environment/`, records its source path, size, and SHA-256, and makes each Condor node invoke the bundled yawl worker through that archived wrapper. Environment variables needed by the wrapper can still be inherited with `%getenv true`.

## Design rule

If a feature can be described without mentioning LFHCal, HGCROC, a particular run number, ROOT histograms, or detector-specific conventions, it may belong in yawl-run. Otherwise it belongs in the analysis-specific layer.

## Status

0.6: explicit Yawlfile -> campaign -> start lifecycle, one launch path, local/Condor `-j` throttling, flattened attempt directories, pattern-task fan-out/fan-in, and portable per-attempt launch provenance.
