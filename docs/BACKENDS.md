# Backends

yawl-run sits above a scheduler rather than trying to become one. The campaign model stays portable while each backend translates that model into the scheduler's native concepts.

| Backend | Status | Dependency mechanism |
| --- | --- | --- |
| local | supported | yawl local coordinator |
| HTCondor / DAGMan | supported | DAGMan |
| Slurm | experimental | `afterok` dependencies |
| OpenPBS / PBS Professional | experimental | `afterok` dependencies |

The Slurm and PBS adapters are tested in CI with simulated scheduler commands and generated-script checks, but have not yet been validated by this project on production Slurm or PBS clusters.

## Local

The local backend launches dependency-ready tasks directly and keeps task stdout and stderr in each attempt directory.

Local campaigns default to one active yawl task at a time. Set the concurrency limit when creating the campaign:

```bash
yawl-run create --backend local -j 4 | yawl-run start
```

`-j N` controls the maximum number of dependency-ready yawl tasks that may run concurrently. It is not a CPU reservation for the coordinator or for an individual task.

If `N` exceeds the CPUs available to the local process, yawl-run warns and allows the operating system to time-slice runnable tasks.

Local `start` prints concise orchestration status while leaving task output in the attempt directories. A run looks roughly like:

```text
[local] host=starsub01 pid=12345 jobs=4 cpus_available=64 load1=3.18
[start] prepare
[done ] prepare attempt=1 elapsed=0.02s real=0.00s user=0.00s sys=0.00s
[start] partial-000
[start] partial-001
...
[local] finished completed=10 failed=0 blocked=0
```

A failed local campaign causes `yawl-run start` to exit nonzero.

## Task resources

Resource requests are task policy in the Yawlfile:

```text
heavy-analysis:
    %cpus 4
    %memory 8GB
    ./Analyze input.root
```

Portable resource concepts are translated where the backend has a natural mapping.

- Condor maps `%cpus 4` to `request_cpus = 4`.
- Slurm maps `%cpus 4` to `--cpus-per-task=4`.
- PBS maps `%cpus 4` to `select=1:ncpus=4`.
- Local yawl records `%cpus` as task metadata but does not currently use it as a local scheduling weight.

`%disk` maps directly for Condor. The experimental Slurm and PBS backends record disk policy but deliberately do not invent a site-specific scratch or disk request.

For the full policy syntax, see [YAWLFILE.md](YAWLFILE.md).

## HTCondor / DAGMan

Creating a Condor campaign renders the durable campaign, DAG, per-node submit files, bundled worker, scheduler log paths, and initial task state. It does not submit the DAG:

```bash
yawl-run create --campaigns-dir ./campaigns
```

Start that exact campaign with:

```bash
yawl-run start ./campaigns/<campaign-id>
```

or:

```bash
yawl-run create --campaigns-dir ./campaigns | yawl-run start
```

`start` streams `condor_submit_dag` output to the terminal. A failed submission does not mark the campaign as successfully started.

For an active Condor campaign, `status` reports the DAGMan controller separately from its DAG nodes and maps active `DAGNodeName` values back to yawl task names.

## Slurm

Slurm support is experimental.

A Slurm campaign renders one `sbatch` script per yawl task. `start` submits tasks in a held state, wires parent job IDs with `afterok` dependencies, records scheduler IDs, marks the yawl campaign started, and releases the jobs.

If graph submission fails partway through, yawl-run makes a best-effort attempt to cancel already-submitted held jobs and leaves the campaign unstarted.

Slurm status uses `squeue`.

## PBS

OpenPBS / PBS Professional support is experimental.

PBS uses the same general held-submission strategy with `qsub -h`, `-W depend=afterok:...`, and `qrls`. `%retry` is implemented inside the batch script so the scheduler sees one node whose final exit status reflects all allowed attempts.

PBS status uses `qstat -f`.

## Shared filesystem assumption

The queued backends currently assume that the campaign directory and declared paths are accessible from execution nodes. Site-specific staging systems are not part of yawl-run's core model.

## Execution wrappers

A site or container wrapper can be configured without teaching yawl-run anything application-specific:

```text
backend condor
%cpus 1
%memory 4GB
%disk 2GB
%wrapper /path/to/run-in-container.sh
```

When the campaign is created, yawl-run copies the wrapper into `environment/`, records its source path, size, and SHA-256, and makes the queued task invoke the bundled yawl worker through that archived wrapper.

The experimental Slurm and PBS backends use the same wrapper mechanism. `%getenv` maps directly to Condor and PBS behavior; Slurm currently relies on its normal exported environment.

## Design rule

Scheduler-specific behavior should remain in backend adapters. Portable workflow concepts such as tasks, dependencies, attempts, resources, provenance, and execution environments belong in yawl-run's core model. Site or application conventions should stay outside the generic workflow layer.
