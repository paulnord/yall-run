# yall-run

<p align="center">
  <img src="docs/images/yall-run-logo.png" alt="yall-run logo" width="400">
</p>

**Yet Another Launch Layer**  
**Y'all run!**

yall-run is a deliberately small campaign runner for reproducible analysis work. It sits above a batch system rather than trying to become one.

A `Yallfile` is a reusable workflow recipe, much like a Makefile. A campaign is one frozen instance of that recipe:

```text
Yallfile
  -> campaign
       -> task
            -> attempt
```

yall-run owns campaign identity, stable task names, dependencies, retry history, logs, provenance, and backend adapters. The queue system still owns resource allocation, queue policy, execution hosts, holds, and machine scheduling.

## Backends

| Backend | Status |
| --- | --- |
| local | supported |
| HTCondor / DAGMan | supported |
| Slurm | experimental |
| OpenPBS / PBS Professional | experimental |

Slurm and PBS are tested in CI with simulated scheduler commands but have not yet been validated by this project on production clusters.

## Install

Requires Python 3.9+.

```bash
python3 -m pip install -e .
```

## Five-minute example

Create a file named `Yallfile`:

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

Then:

```bash
yall-run validate
yall-run plan
yall-run create | yall-run start
```

`create` freezes a new campaign and prints its directory. `start` runs that exact campaign. Creating another run means creating another campaign.

For a local workflow with up to four dependency-ready tasks running at once:

```bash
yall-run create --backend local -j 4 | yall-run start
```

That is enough to get started. The details live in the focused documentation below.

## Documentation

- [Quick start](docs/QUICKSTART.md) - first campaign from install through status
- [Yallfile reference](docs/YALLFILE.md) - tasks, data, `@each`, resources, wrappers, and workflow syntax
- [Campaigns](docs/CAMPAIGNS.md) - create/start lifecycle, frozen campaigns, state, and attempt directories
- [Backends](docs/BACKENDS.md) - local execution, Condor/DAGMan, Slurm, PBS, resources, and wrappers
- [Provenance](docs/PROVENANCE.md) - campaign and attempt records and provenance exposed to programs
- [Export](docs/EXPORT.md) - SQLite, SQL, and CSV export for provenance queries
- [Examples](examples/README.md) - worked scientific and numerical workflows

## Design rule

A feature belongs in yall-run when it expresses a portable workflow concept: tasks, dependencies, attempts, resources, provenance, execution environment, or scheduler adaptation. Details that only make sense for one application, data format, experiment, analysis package, or site's scientific conventions belong in the application-specific layer instead.

> Naming note: yall-run is not the YAWL (Yet Another Workflow Language) workflow system.
