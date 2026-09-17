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
```

The levels are diagnostic rather than merely longer versions of the same listing.

## `status`

The default view shows one line per task plus the scheduler summary for queued backends. It is intended for frequent use and remains unchanged by this feature.

## `status -v`: why did it fail?

The first level adds a diagnostic block only for failed, blocked, interrupted, held, suspended, or unknown tasks. It reports the latest attempt number, return codes, structured failure kind, the last non-empty stderr line, attempt directory, and relevant scheduler state.

For an HTCondor hold, Yall also reports the hold reason and hold reason code/subcode when the pool supplies them.

This level is meant to replace the first round of manual commands after seeing `failed` in the ordinary status display.

## `status -vv`: what exactly ran?

The second level adds execution context for those problem tasks:

- command and working directory
- requested resources
- timing from the latest attempt
- declared input and output paths with existence information
- stdout, stderr, and attempt paths
- bounded stderr and stdout tails

Log output is deliberately bounded. `status` is a diagnostic summary, not a substitute for opening the complete log file when a payload emits a very large log.

## `status -vvv`: reconstruct the failure

The third level adds the evidence that is commonly needed for a deeper postmortem:

- every recorded attempt for each problem task
- return code, failure kind, timing, and last stderr line for each attempt
- actual launch command from `provenance.json`
- wrapper and amendment information when present
- provenance path
- recent resume rounds and operator-supplied resume reasons
- detailed scheduler diagnostics for active nodes
- larger, but still bounded, stdout and stderr tails

This level is intended to replace most of the `find`, `cat attempt.json`, `cat provenance.json`, log-tail, and scheduler-inspection commands normally used to diagnose a failed campaign.

## JSON output

`--json` remains a stable machine-readable status view. Verbosity flags are for human-readable output, so `--json` and `-v` are intentionally not combined. Scripts that need additional provenance should read the campaign/attempt records directly or use the relational export.

## Condor forensic fallback

At `-vvv`, Condor campaigns also query `condor_history` using the DAGMan cluster IDs
recorded by Yall. Historical scheduler evidence is diagnostic only and never changes
task state. If history is unavailable, status reports that fact and continues using
Yall's local provenance and live scheduler evidence.

For each interesting Condor task, `-vvv` also identifies the original and recent
recovery submit files, DAG/rescue files, submission record, and critical submit
attributes such as `executable`, `arguments`, `output`, `error`, and `log`. This is
intended to expose path/quoting/submission bugs without requiring manual file hunting.
