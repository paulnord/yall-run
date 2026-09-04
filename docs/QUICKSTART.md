# Quick start

This guide gets a small yall-run workflow from a `Yallfile` to a completed campaign. For the full language reference, see [YALLFILE.md](YALLFILE.md).

## Install

yall-run requires Python 3.9 or newer.

From a checkout of the repository:

```bash
python3 -m pip install -e .
```

## Write a Yallfile

Create a file named exactly `Yallfile`:

```text
campaign hello-yall
backend local

left:
    echo left says hello

right:
    echo right says hello

finish: left right
    echo both parents finished
```

A Yallfile is a reusable workflow recipe. The example has three tasks. `finish` depends on both `left` and `right`.

## Validate and inspect

```bash
yall-run validate
yall-run plan
```

`validate` checks the workflow definition. `plan` shows the expanded tasks without creating a campaign.

## Create a campaign

```bash
yall-run create
```

`create` freezes the expanded workflow into a new campaign directory under `./campaigns` and prints that directory. It does not run anything.

To place new campaigns somewhere else:

```bash
yall-run create --campaigns-dir /path/to/campaigns
```

## Start it

Start the exact campaign printed by `create`:

```bash
yall-run start ./campaigns/<campaign-id>
```

Or pipe the campaign path directly from `create` into `start`:

```bash
yall-run create | yall-run start
```

A campaign can be started only once. To run the recipe again, create another campaign.

## Run local tasks in parallel

Local campaigns default to one active yall task at a time. Freeze a different concurrency limit when creating the campaign:

```bash
yall-run create --backend local -j 4 | yall-run start
```

`-j` controls how many dependency-ready yall tasks may run concurrently. It is a local-backend option, not a CPU request for an individual task.

## Run the same workflow on HTCondor

If HTCondor and DAGMan are available, create a Condor campaign from the same Yallfile by overriding the backend:

```bash
yall-run create --backend condor | yall-run start
```

`create` freezes the campaign and renders its DAG and submit files without submitting anything. `start` then submits that exact campaign through DAGMan, preserving the same task dependencies used by the local backend.

Local `-j` does not apply to queued backends. Requests for an individual queued task belong in the Yallfile instead:

```text
analysis:
    %cpus 4
    %memory 8GB
    ./Analyze input.root
```

For scheduler details, site wrappers, and the experimental Slurm and PBS adapters, see [BACKENDS.md](BACKENDS.md).

## Check status

```bash
yall-run status ./campaigns/<campaign-id>
```

Task stdout and stderr are kept with each task attempt rather than mixed into the coordinator output.

## Add real inputs, outputs, and resources

Named data and task policy live directly in the Yallfile:

```text
convert:
    @input raw raw/run137.h2g
    @output root converted/raw_137.root

    %retry 1
    %cpus 2
    %memory 4GB

    ./Convert -i @input.raw -o @output.root
```

`@` declarations describe data and named values. `%` directives describe execution policy.

## Where next?

- [Yallfile language reference](YALLFILE.md)
- [Campaign lifecycle and campaign directories](CAMPAIGNS.md)
- [Local, Condor, Slurm, and PBS backends](BACKENDS.md)
- [Campaign and attempt provenance](PROVENANCE.md)
- [Relational provenance export](EXPORT.md)
- [Worked examples](../examples/README.md)
