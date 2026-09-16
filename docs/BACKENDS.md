# Backends

yall-run sits above a scheduler rather than trying to become one. The campaign model stays portable while each backend translates that model into the scheduler's native concepts.

| Backend | Status | Dependency mechanism |
| --- | --- | --- |
| local | supported | yall local coordinator |
| HTCondor / DAGMan | supported | DAGMan |
| Slurm | experimental | `afterok` dependencies |
| OpenPBS / PBS Professional | experimental | `afterok` dependencies |

The Slurm and PBS adapters are tested in CI with simulated scheduler commands and generated-script checks, but have not yet been validated by this project on production Slurm or PBS clusters.

## Local

The local backend launches dependency-ready tasks directly and keeps task stdout and stderr in each attempt directory.

Local campaigns default to one active yall task at a time. Set the concurrency limit when creating the campaign:

```bash
yall-run create --backend local -j 4 | yall-run start
```

`-j N` controls the maximum number of dependency-ready yall tasks that may run concurrently. It is not a CPU reservation for the coordinator or for an individual task.

If `N` exceeds the CPUs available to the local process, yall-run warns and allows the operating system to time-slice runnable tasks.

Local `start` prints concise orchestration status while leaving task output in the attempt directories. A run looks roughly like:

```text
[local] host=starsub01 pid=12345 jobs=4 cpus_available=64 load1=3.18
[start] prepare
[done ] prepare attempt=1 elapsed=0.02s real=0.00s user=0.00s sys=0.00s
[start] partial-000
[done ] partial-000 attempt=1 elapsed=0.02s real=0.00s user=0.00s sys=0.00s
...
[local] finished completed=10 failed=0 blocked=0
```

A failed local campaign causes `yall-run start` to exit nonzero.

## Task resources

Resource requests are task policy in the Yallfile:

```text
heavy-analysis:
    %cpus 4
    %memory 8GB
    %time 2h
    ./Analyze input.root
```

Portable resource concepts are translated where the backend has a natural mapping.

- Condor maps `%cpus 4` to `request_cpus = 4`.
- Slurm maps `%cpus 4` to `--cpus-per-task=4`.
- PBS maps `%cpus 4` to `select=1:ncpus=4`.
- Local yall records `%cpus` as task metadata but does not currently use it as a local scheduling weight.

`%disk` maps directly for Condor. The experimental Slurm and PBS backends record disk policy but deliberately do not invent a site-specific scratch or disk request.

`%time 2h` becomes Condor `+MaxRuntime = 7200`, Slurm `--time=02:00:00`, or PBS `walltime=02:00:00`. Slurm rounds second-level requests up to whole minutes. Local execution records wall time but does not enforce it. An omitted request leaves scheduler defaults unchanged.

**Condor's `MaxRuntime` mapping requires site support.** CERN and FZU support this custom job ClassAd attribute; it is not CERN-only or a universal HTCondor timeout. A pool with no policy using it simply stores the attribute without changing its runtime limits. Supporting sites may use it for both scheduling and enforcement. Other pools may require a different attribute, such as DESY NAF's `RequestRuntime`; Yall does not detect or translate that alternative automatically.

**Yall's `%time` does not set `allowed_execute_duration`.** That is HTCondor's separate built-in execution-duration limit, which puts a job on hold when exceeded. Setting it cannot override a shorter site-enforced limit, and setting `MaxRuntime` does not implicitly enable it. See [site runtime policy versus execution timeout](RESOURCES.md#htcondor-site-runtime-policy-versus-execution-timeout) for their independent behavior, timing details, and primary documentation.

For the full policy syntax, see [YALLFILE.md](YALLFILE.md) and [Resources](RESOURCES.md).

## HTCondor / DAGMan

Creating a Condor campaign renders the durable campaign, DAG, per-node submit files, bundled worker, scheduler log paths, and initial task state. It does not submit the DAG:

```bash
yall-run create --campaigns-dir ./campaigns
```

Start that exact campaign with:

```bash
yall-run start ./campaigns/<campaign-id>
```

or:

```bash
yall-run create --campaigns-dir ./campaigns | yall-run start
```

`start` streams `condor_submit_dag` output to the terminal. A failed submission does not mark the campaign as successfully started.

For an active Condor campaign, `status` reports the DAGMan controller separately from its DAG nodes and maps active `DAGNodeName` values back to yall task names.

## Slurm

Slurm support is experimental.

A Slurm campaign renders one `sbatch` script per yall task. `start` submits tasks in a held state, wires parent job IDs with `afterok` dependencies, records scheduler IDs, marks the yall campaign started, and releases the jobs.

If graph submission fails partway through, yall-run makes a best-effort attempt to cancel already-submitted held jobs and leaves the campaign unstarted.

Slurm status uses `squeue`.

## PBS

OpenPBS / PBS Professional support is experimental.

PBS uses the same general held-submission strategy with `qsub -h`, `-W depend=afterok:...`, and `qrls`. `%retry` is implemented inside the batch script so the scheduler sees one node whose final exit status reflects all allowed attempts.

PBS status uses `qstat -f`.

## Shared filesystem assumption

The queued backends currently assume that the campaign directory and declared paths are accessible from execution nodes. Site-specific staging systems are not part of yall-run's core model.

## Execution wrappers

`%wrapper PATH [ARG ...]` is a backend-independent **payload** execution policy.
The local coordinator or a queued node first runs Yall's Python worker on the
execution host. That worker handles state, guards and logs, then invokes the
archived wrapper around only the scientific command.

```text
host Python worker -> archived wrapper -> scientific payload
```

Each batch execution host needs Python 3.9+ and its standard library, but does
not need an installed yall-run package: queued scripts use the bundled worker.
The container only needs the application's dependencies. Do not infer host
Python availability from the submit node or the container's Python version.

The wrapper and its arguments are archived once during campaign creation under
`execution.wrapper`, independently of the selected scheduler. Slurm/PBS remain
experimental. `%getenv` still maps to Condor and PBS; Slurm uses its usual
exported environment. Local execution inherits the invoking environment.

### eic-shell

Use the same launcher and adapter as before, from the **host**, outside the
container:

```text
@env EIC_SHELL
%wrapper ./run-in-eic-shell.sh {EIC_SHELL}
```

The adapter accepts an arbitrary command and sends shell-quoted payload argv to
`eic-shell` over stdin. It no longer transports `yall_worker.py` into the EIC
environment. ROOT/Python versions printed by the example tasks describe the
container; the worker's own provenance describes the host.

The host must access campaign state, declared inputs/outputs, and the archived
wrapper. The payload must access its inputs, outputs, executable and working
directory inside the wrapped environment. Wrappers are responsible for binds,
working-directory preservation, environment forwarding and exit-status
propagation. Only payloads that read `YALL_PROVENANCE` need its path available
inside the container. No automatic mount or path remapping is performed.

See [Execution wrappers](WRAPPERS.md), the [Yallfile reference](YALLFILE.md#execution-wrappers)
and [`examples/eic-shell`](../examples/eic-shell/README.md). This is a deliberate
pre-beta behavior change with no migration layer: create new campaigns.

## Design rule

Scheduler-specific behavior should remain in backend adapters. Portable workflow concepts such as tasks, dependencies, attempts, resources, provenance, and execution environments belong in yall-run's core model. Site or application conventions should stay outside the generic workflow layer.
