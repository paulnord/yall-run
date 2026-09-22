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

The default view shows one line per task plus the scheduler summary for queued
backends. Completed and failed attempts now retain useful execution information
even after their jobs leave the queue. For example (illustrative values):

```text
  refine1-e1           completed  attempts=1 job=64836.0 wall=00:22:03 cpu=00:21:48 exit=0 queue=00:08:15
  refine2-e1           running    attempts=1 condor=running job=64837.0 elapsed=00:03:12 queue=00:01:05
  refine3-e1           pending    attempts=0
```

- `wall`: recorded elapsed execution time, including wrapper/container startup,
  application work and I/O, excluding queue wait.
- `cpu`: recorded user plus system CPU time; it can exceed wall time for a
  parallel job. Shown only when both CPU measurements are available.
- `exit`: the Yall attempt return code (including output validation).
- `elapsed`: time since the currently running worker recorded its start. This
  live wall-clock estimate may differ slightly from the final measured `wall`.
- `job`: live scheduler job ID, or the saved Condor job ID for the latest
  attempt after the live job disappears.
- `queue`: Condor submission-to-first-start interval, shown only for a job
  whose saved metadata supports a single start and valid `QDate`/`JobStartDate`. It can include
  time held before starting; it excludes time waiting for DAG dependencies
  before the node was submitted. Restarts and incomplete records omit this
  field instead of guessing cumulative queue time. See the
  [HTCondor timestamp definitions](https://htcondor.readthedocs.io/en/24.0/classad-attributes/job-classad-attributes.html#JobStartDate).

Queue timing accepts `NumJobStarts=1` unless the current start differs from
the first. Worker-side ads can still record `NumJobStarts=0` at first launch
(observed in BNL records); zero is accepted only when both start timestamps
are present and equal. Higher counts and conflicting timestamps omit `queue`.

Durations use `HH:MM:SS`, rounded to the nearest second; hours can exceed 24.
The timing and exit fields describe the latest attempt, not a sum across
retries. A newly queued retry does not inherit the previous job's measurements.
Local campaigns show the same saved wall/CPU/exit fields. Older or incomplete
campaigns omit unavailable fields. These details use existing attempt and
provenance files, require no new worker or campaign, and do not add scheduler
queries. The JSON status schema is unchanged.

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
- the first recorded effective command, plus later command transitions and applied amendment numbers
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
