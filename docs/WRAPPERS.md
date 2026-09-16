# Payload execution wrappers

`%wrapper PATH [ARG ...]` applies to local, HTCondor/DAGMan, Slurm and PBS tasks.
It is generic execution policy (`ExecutionSpec`), not a setting in `CondorSpec`.
`CondorSpec` still contains resource defaults and batch environment settings;
renaming that remaining model is outside this change.

## The boundary

The execution host runs Yall's worker with a supported Python (3.9+) and its
standard library. Queued backends bundle the worker; installing the yall-run
package on every worker node or inside the container is unnecessary.

The worker, outside the wrapper, owns input/output guards, attempt numbering,
state, logs and launch provenance. It invokes the wrapper around the scientific
payload and records the outcome after the wrapper exits. A C++ payload does not
need Python in its image merely to support Yall.

```text
%wrapper ./run-in-container.sh --image analysis.sif --
```

For an argv task, the worker executes this argv vector without an extra host
shell:

```text
<archived-wrapper> --image analysis.sif -- <program> <arguments...>
```

For a `!` shell task, it instead executes:

```text
<archived-wrapper> --image analysis.sif -- /bin/sh -c <entire-command-string>
```

Thus shell expansion, pipes and redirection happen in the payload environment.
The wrapper must not join argv naively or evaluate it a second time. An `exec
"$@"` launcher is sufficient; adapters such as the EIC stdin adapter must provide
equivalent argument and exit-status behavior. There is no implicit `--`.

## Wrapper responsibilities

A wrapper must run the supplied command in the foreground, wait for it, and
return its exit status. Do not daemonize the command or return success before
it finishes. Scheduler cancellation and signals must reach the payload using
the launcher's normal process/signal handling. Yall does not introduce a
separate timeout, process-tree supervisor, or a guarantee of final state writes
following SIGKILL or host loss.

The worker starts the wrapper with the task's frozen working directory and an
inherited environment augmented with `YALL_*` variables. The wrapper must retain
that directory or deliberately map it into its environment. `YALL_TASK_CWD`
exposes the expected path. Wrappers that reset cwd without restoring it can
break relative commands; no automatic path remapping is attempted.

The host worker must be able to inspect declared inputs and outputs. Those paths
must also be usable by the scientific payload, with appropriate mounts and
permissions. The campaign directory only needs to be accessible inside the
container if the application reads `YALL_PROVENANCE` or otherwise uses it.
Wrappers must pass through needed `YALL_*` variables when filtering environment.
A host input check is not proof of readability from inside the container, nor a
check that a download is complete. Finish/stage input transfers before launch.

Commands available only inside the payload environment are allowed. Do not use
host `which` results as proof of payload executable availability. Creation-time
executable fingerprints are labeled `creation_host`, not container identity.

## Freezing, logging and failures

The wrapper executable is copied once to `environment/payload-wrapper<suffix>`
when creating the campaign. `execution.wrapper` records its source, archived
path, SHA-256, size and literal arguments. Local and queued runs use that same
record. External launchers, images and files referenced in wrapper arguments
are not copied or pinned automatically.

Wrapper startup stdout/stderr and payload stdout/stderr go to the task attempt's
`stdout.log` and `stderr.log`. A nonzero wrapper/payload exit produces a failed
attempt with that exit status. A host launch failure (for example, a missing
archived wrapper, unwrapped program or cwd) records `failure.kind=launch_failed`,
the errno/message, a finished timestamp, and return code 2. No child PID or
command exit code is invented when the process never started. Final attempt records
call the immediate host child PID `launch_pid`; Yall does not claim that this is the
PID of a process created later inside a container or launcher.

Missing inputs and protected existing outputs fail **before** wrapper invocation.
Missing declared outputs after an otherwise successful invocation also fail the
task. Host-side guards cannot inspect files visible only inside an isolated
container namespace.

Launch provenance labels the worker environment as `host` and records its
interpreter, wrapper identity and planned launch argv separately from the
logical scientific command. It does not pretend to know the payload environment.
See [Provenance](PROVENANCE.md) for field details.

## Pre-beta change

This replaces the old `wrapper -> worker -> payload` behavior outright. No
legacy mode, migration, or dual execution paths are added. The campaign schema
is now 8; create new campaigns to use payload-only wrapping. `resume` reuses
frozen workers/scripts and does not rewrite an old campaign's execution model.

Yallfile syntax and forwarding adapters such as LFHCal's `run-in-eic-shell.sh`
do not need a syntax change for the current LFHCal argv tasks. The bundled EIC
example also fixes literal-backslash transport through eic-shell's `read` loop;
the LFHCal adapter snapshot predates that fix. Copy the updated example adapter
before relying on shell payloads or arguments containing backslashes. This PR
does not update the LFHCal repository. Wrappers that specifically inspect or expect the
old Python worker argv must change. Local `%wrapper` now takes effect as well;
run wrapped local examples from the host to avoid nested container launchers.

Testing includes an exact LFHCal adapter snapshot with a simulated `eic-shell`,
not a live CERN container or scheduler. Run the EIC smoke test at your site
before a production campaign.
