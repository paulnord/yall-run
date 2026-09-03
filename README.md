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

A `Yawlfile` is a reusable recipe, much like a Makefile. A campaign is one frozen instance of that recipe. The same Yawlfile can be instantiated for local execution or a supported queue backend. yawl-run owns campaign identity, stable task names, dependencies, retry history, logs, lightweight file provenance, and backend adapters. The queue system still owns resource allocation, queue policy, execution hosts, holds, and machine scheduling.

> Naming note: yawl-run is not the YAWL (Yet Another Workflow Language) workflow system.

## Backends

| Backend | Status | Dependency mechanism |
| --- | --- | --- |
| local | supported | yawl local coordinator |
| HTCondor / DAGMan | supported | DAGMan |
| Slurm | experimental | `afterok` dependencies |
| OpenPBS / PBS Professional | experimental | `afterok` dependencies |

The Slurm and PBS adapters are tested in CI with simulated scheduler commands and generated-script checks, but have not yet been validated by this project on production Slurm or PBS clusters. Reports, fixes, site tests, and contributed backend work are welcome.

Other scheduler backends should remain adapters rather than growing scheduler-specific concepts into yawl's core model.

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
yawl-run create --campaigns-dir ./campaigns
```

`Yawlfile` is the canonical default filename, with that capitalization. On case-sensitive filesystems, `yawlfile` and `YAWLFILE` are different names. A differently named workflow file can always be supplied explicitly.

`create` prints the new campaign directory but runs nothing. Start that exact campaign with:

```bash
yawl-run start ./campaigns/<campaign-id>
```

Because `create` writes the new campaign path to standard output, it can also feed `start` directly:

```bash
yawl-run create | yawl-run start
```

Options naturally stay on the `create` side of the pipe:

```bash
yawl-run create --backend local -j 4 | yawl-run start
```

When `CAMPAIGN_DIR` is supplied explicitly, `start` uses that argument. When it is omitted, `start` reads exactly one nonblank campaign path from standard input.

`--campaigns-dir` names the directory that will contain newly created campaign directories. It defaults to `./campaigns`. The old prototype name `--root` is intentionally retired because it was ambiguous, especially in workflows that use CERN ROOT files.

There is one launch operation: `start`. Creating another run means creating another campaign.

There is also one campaign language: Yawlfile syntax. Old TOML campaign files are not supported.

## Local parallelism: `-j`

`-j N` is a **local-backend creation option**. It freezes the maximum number of dependency-ready yawl tasks that may run concurrently:

```bash
yawl-run create --backend local -j 4
# use the printed campaign directory
yawl-run start ./campaigns/<campaign-id>
```

Local execution defaults to one active task when `-j` is omitted. `-j` is not a CPU reservation for the yawl coordinator. The coordinator remains a lightweight process while it launches and reconciles up to `N` task processes.

If `N` exceeds the CPUs available to the local process, yawl prints a warning and the operating system time-slices runnable tasks. This is allowed because `-j` controls task concurrency, not processor allocation.

`-j` is intentionally invalid for Condor, Slurm, and PBS campaigns. Processor requests for queued tasks are a different concept and belong to task resource policy in the Yawlfile:

```text
heavy-analysis:
    %cpus 4
    %memory 8GB
    ./Analyze input.root
```

Condor maps `%cpus 4` to `request_cpus = 4`. Slurm maps it to `--cpus-per-task=4`. PBS maps it to `select=1:ncpus=4`. Local yawl records `%cpus` as task metadata but does not currently use it as a local scheduling weight.

## Local progress output

Local `start` emits concise orchestration status to standard output while task stdout/stderr remain in their attempt directories. A run looks roughly like:

```text
[local] host=starsub01 pid=12345 jobs=4 cpus_available=64 load1=3.18
[start] prepare
[done ] prepare attempt=1 elapsed=0.02s real=0.00s user=0.00s sys=0.00s
[start] partial-000
[start] partial-001
[done ] partial-000 attempt=1 elapsed=1.31s real=1.27s user=1.20s sys=0.02s
...
[local] finished completed=10 failed=0 blocked=0
```

`elapsed` measures the coordinator's start-to-reconciliation wall time. `real`, `user`, and `sys` are measured around the task command itself and are also stored in `attempt.json`.

Failures include the exit status and the corresponding stderr log path. A failed local campaign causes `yawl-run start` to exit nonzero, which makes ordinary shell use natural:

```bash
yawl-run start ./campaigns/<campaign-id> > yawl.log 2>&1 &
```

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

`@` is for data and named values. `%` is for execution policy. Resource requests are generic yawl-run concepts where the backend has a portable translation. `%disk` maps directly for Condor; the experimental Slurm and PBS backends currently record it but deliberately do not invent a site-specific disk/scratch request.

Commands are argv arrays by default. If a task deliberately needs shell syntax, prefix the command with `!`:

```text
report:
    @output text report.txt
    ! echo complete > @output.text
```

## Pattern tasks

For a family of files where each input should produce its own output, `@each` expands one rule into one task per matching file:

```text
pedestal-{run}:
    @each raw converted/data{run}.root
    @output pedestal pedestal/run{run}.root
    ./make-pedestal @input.raw -o @output.pedestal
```

Files such as:

```text
converted/data123.root
converted/data138.root
converted/data142.root
```

produce three tasks:

```text
pedestal-123
pedestal-138
pedestal-142
```

with corresponding outputs:

```text
pedestal/run123.root
pedestal/run138.root
pedestal/run142.root
```

A plain placeholder such as `{run}` currently captures arbitrary non-path text. For example, `data123a.root` also matches `data{run}.root` with `run=123a`; numeric-only typed captures are not yet part of the syntax.

A patterned child naturally follows the same family. A plain task depending on a patterned parent fans in from the whole family.

See [docs/YAWLFILE.md](docs/YAWLFILE.md) for the format details.

## Map, then reduce: the pi example

`examples/pi` is a complete Condor-ready example of reusable code and fan-out/fan-in dependencies. It evaluates the Leibniz series

```text
pi = 4 * (1 - 1/3 + 1/5 - 1/7 + ...)
```

in eight independent chunks. Every worker runs the same `partial_pi.py` program. The final `sum` task waits for the whole `partial-{chunk}` family and receives all partial result files through one named input.

For Condor:

```bash
cd examples/pi
yawl-run validate
yawl-run plan
yawl-run create
# use the printed campaign directory
yawl-run start campaigns/<campaign-id>
```

The Yawlfile declares `backend condor`, so `create` freezes the campaign and renders its DAG without submitting anything. `start` submits that exact frozen campaign.

For a local smoke test, create a separate local campaign from the same Yawlfile and freeze the desired local concurrency at creation time:

```bash
yawl-run create --backend local -j 4 | yawl-run start
cat pi-work/pi.txt
```

The same recipe may be experimentally rendered for another scheduler without editing the file:

```bash
yawl-run create --backend slurm
yawl-run create --backend pbs
```

## Campaign records and attempt provenance

A campaign keeps one frozen definition of the workflow and only small mutable files for current task state:

```text
campaign.json
start.json
state/
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

`campaign.json` is the durable definition of the campaign. It contains campaign identity, creation environment, execution policy, task order, and each frozen task definition including dependencies, command, cwd, resources, inputs, and outputs. Creation provenance lives there rather than in a separate top-level provenance file.

Files under `state/` are deliberately small and mutable. A task state file normally contains only its current state, attempt count, and most recent return code. This lets independent workers update their own state atomically without duplicating the full task definition in every file.

Each attempt still has two distinct records. `provenance.json` is written **before** the task command begins and is never rewritten afterward. It contains portable launch provenance: campaign identity, task and attempt identity, command, cwd, resolved inputs, declared outputs, requested resources, execution host, Python version, and start time. `attempt.json` is completed after execution and records the return code, finish time, timing, stdout/stderr locations, and observed output metadata.

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

`YAWL_PROVENANCE` points at the attempt's launch-provenance JSON. Application-specific programs may copy or embed that record into their own native output formats without teaching generic yawl-run anything about those formats.

Output data may live on another persistent filesystem; the campaign directory remains the provenance anchor.

## Relational provenance export

Campaign JSON remains the canonical record, but one or more campaign trees can be scraped into SQLite, a SQLite-compatible SQL dump, or normalized CSV tables for querying and reporting:

```bash
yawl-run export campaigns \
    --sqlite yawl.sqlite \
    --sql yawl.sql \
    --csv-dir yawl-csv
```

The export uses yawl's natural campaign, task, and attempt identities as relational primary keys and does not modify the campaign directories. Older campaigns can be included even when newer provenance fields are absent; unavailable values are left null.

See [docs/EXPORT.md](docs/EXPORT.md) for the table layout, primary keys, and example queries.

## Condor / DAGMan

For a Condor Yawlfile:

```bash
yawl-run create --campaigns-dir ./campaigns
```

creates the durable campaign, DAG, per-node submit files, bundled worker, scheduler log paths, and task state, but does not submit the DAG.

Inspect that campaign if desired, then launch that exact artifact:

```bash
yawl-run start ./campaigns/<campaign-id>
```

or create and submit it in one shell pipeline:

```bash
yawl-run create --campaigns-dir ./campaigns | yawl-run start
```

`start` streams `condor_submit_dag` output to the terminal instead of hiding it. A failed Condor submission does not mark the campaign as successfully started.

A campaign can be started only once. To run the workflow again, create a new campaign from the Yawlfile.

Check state with:

```bash
yawl-run status ./campaigns/<campaign-id>
```

For active Condor campaigns, status reports the DAGMan controller separately from its DAG nodes and maps active `DAGNodeName` values back to yawl task names.

## Experimental Slurm and PBS

Slurm campaigns render one `sbatch` script per yawl task. `start` submits every task in a held state, wires parent job IDs with `afterok` dependencies, records the scheduler IDs, marks the yawl campaign started, and releases the jobs. If graph submission fails partway through, yawl best-effort cancels the already-submitted held jobs and leaves the campaign unstarted.

PBS uses the same strategy with `qsub -h`, `-W depend=afterok:...`, and `qrls`. `%retry` is implemented inside each Slurm/PBS batch script so the scheduler sees one node whose final exit status reflects all allowed attempts.

Both experimental adapters assume the campaign directory and declared paths are accessible from execution nodes. Slurm status uses `squeue`; PBS status uses `qstat -f`. These adapters are CI-tested against simulated command behavior, not yet certified against a production installation.

## Execution wrapper

A site or container wrapper can be configured without teaching yawl-run anything application-specific:

```text
backend condor
%cpus 1
%memory 4GB
%disk 2GB
%wrapper /path/to/run-in-container.sh
```

When the campaign is created, yawl-run copies the wrapper into `environment/`, records its source path, size, and SHA-256, and makes the queued task invoke the bundled yawl worker through that archived wrapper. The experimental Slurm and PBS backends use the same wrapper mechanism. `%getenv` maps directly to Condor and PBS behavior; Slurm currently relies on its normal exported environment.

## Design rule

A feature belongs in yawl-run when it expresses a portable workflow concept: tasks, dependencies, attempts, resources, provenance, execution environment, or scheduler adaptation. Details that only make sense for one application, data format, experiment, analysis package, or site's scientific conventions belong in the application-specific layer instead.

## Status

0.8.1: `start` accepts a campaign path either as its argument or as one line on standard input, so `yawl-run create | yawl-run start` works directly; schema 7 stores frozen task definitions and campaign creation provenance once in `campaign.json`, with compact mutable task state under `state/`; supported local and HTCondor/DAGMan execution; experimental Slurm and PBS adapters with native dependency submission; explicit Yawlfile -> campaign -> start lifecycle; `--campaigns-dir` for campaign placement; local-only `-j` frozen at campaign creation; local progress/error/timing reporting; flattened attempt directories; pattern-task fan-out/fan-in; portable per-attempt launch provenance.
