# Status and diagnostics

`yall-run status` stays intentionally compact for routine monitoring:

```bash
yall-run status campaigns/<campaign-id>
```

For failure diagnosis, repeat `-v` to reveal progressively deeper execution evidence:

```bash
yall-run status campaigns/<campaign-id> -v
yall-run status campaigns/<campaign-id> -vv
yall-run status campaigns/<campaign-id> -vvv
yall-run status campaigns/<campaign-id> -vvvv
```

The levels are diagnostic rather than merely longer versions of the same listing.

## `status`

The default view shows one line per task plus the scheduler summary for queued backends. It is intended for frequent use and remains unchanged by this feature.

## `status -v`: why did it fail?

The first level adds a diagnostic block only for failed, blocked, interrupted, held, suspended, or unknown tasks. It reports the latest attempt number, return codes, structured failure kind, the last non-empty stderr line, attempt directory, and relevant scheduler state.

For an HTCondor hold, Yall also reports the hold reason and hold reason code/subcode when the pool supplies them.

## `status -vv`: what exactly ran?

The second level adds execution context for those problem tasks:

- command and working directory
- requested resources
- timing from the latest attempt
- declared input and output paths with existence information
- stdout, stderr, and attempt paths
- bounded stderr and stdout tails

Log output is deliberately bounded. `status` is a diagnostic summary, not a substitute for opening the complete log file when a payload emits a very large log.

## `status -vvv`: forensic summary

The third level adds evidence commonly needed for a deeper postmortem:

- every recorded attempt for each problem task, plus recovered tasks with multiple attempts
- return code, failure kind, timing, and last stderr line for each attempt
- actual launch command from `provenance.json`
- wrapper and amendment information when present
- provenance path
- recent resume rounds and operator-supplied resume reasons
- detailed scheduler diagnostics for active nodes
- larger, but still bounded, stdout and stderr tails

For Condor campaigns, `-vvv` also queries `condor_history` using the DAGMan cluster IDs recorded by Yall and identifies relevant submit, DAG, Rescue DAG, and submission records. Historical scheduler evidence remains diagnostic only and never changes task state.

## `status -vvvv`: execution trace

The fourth level retains everything from `-vvv` and adds a structured HTCondor execution timeline. It is designed for cases where "attempt 3 failed" is not enough because the scheduler may have started the same job several times.

The trace groups evidence by:

1. original submission or resume generation;
2. DAG retry number when recorded;
3. Condor job ID;
4. individual scheduler execution starts;
5. the Yall attempt associated with that execution, when the evidence supports an exact link.

It parses the Condor user event logs for repeated execute events, evictions, holds/releases, disconnect/reconnect events, scheduler failures, exit codes, signals, and execution hosts. Exit code `100` from Yall's Condor launcher is identified as a startup failure before the payload marker; exit code `101` is identified as a Yall-worker failure after startup classification.

A Yall attempt is attached to one execution as `association=direct` only when its recorded Condor job ID and `NumJobStarts` agree with a complete-from-submit event sequence. For an incomplete or rotated log, Yall says `observed start N` and leaves ambiguous attempt-to-start relationships as `execution=unknown` rather than guessing.

Event-log reads are bounded. Missing, malformed, or oversized logs produce partial diagnostics instead of causing `status` to fail. The execution trace is read-only evidence and never participates in recovery decisions.

Detailed scheduler execution reconstruction is currently Condor-specific. Local, Slurm, and PBS campaigns still show all lower verbosity diagnostics and report that the detailed execution trace is Condor-only.

See [Execution trace status](EXECUTION_TRACE.md) for the correlation and uncertainty rules.

## JSON output

`--json` remains the stable machine-readable status view. Verbosity flags are for human-readable diagnostics, so `--json` and `-v` are intentionally not combined. The `-vvvv` work does not change the JSON schema.
