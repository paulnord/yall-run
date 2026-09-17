# Execution trace status

`yall-run status CAMPAIGN -vvvv` reconstructs how an interesting task moved through Yall and HTCondor. It is an execution timeline layered on top of the `-vvv` forensic summary, not simply a larger log dump.

The trace keeps four identities separate:

1. campaign generation: the original submission or a numbered resume round;
2. DAG node try: the initial DAGMan node run plus `%retry` reruns;
3. scheduler execution start: repeated starts of one Condor job after startup retry, eviction, hold/release, or other scheduler events;
4. Yall attempt: one worker invocation that reached Yall attempt creation.

A scheduler execution can fail before a Yall attempt exists, and one Condor job ID can execute more than once. Those cases must not be collapsed into one attempt counter.

## Durable scheduler identity

New Condor campaigns stamp each DAG node job with `YallDAGRetry=$(RETRY)` through a DAGMan `VARS` ClassAd. The worker reads the `_CONDOR_JOB_AD` snapshot, when available, and records allowlisted scheduler identity in attempt `provenance.json`: cluster/proc and global job ID, DAGMan job and node, DAG retry number, `NumJobStarts`, execution host, and start timestamps.

The worker accepts only validated, case-insensitive literal values. Missing, malformed, duplicate, undefined, or unevaluated attributes remain unknown. Job-ad reads are bounded and optional telemetry failures never fail the scientific task.

## Event-log reconstruction

At `-vvvv`, Yall reads the Condor user event logs written for the original submission and each resume generation. It groups events by Condor job ID and reconstructs repeated execution starts, including useful evidence such as:

- execute/start host;
- eviction;
- hold and release;
- disconnect/reconnect;
- shadow or remote execution failures;
- termination exit code or signal.

The event-log read is bounded to 64 MiB. If a larger log is encountered, Yall reads a bounded tail and labels it as partial. Missing, malformed, or partial logs remain diagnostic conditions rather than status failures.

A start is labeled `start N` only when the retained event log includes that job's submit event and is therefore complete from submission. Otherwise Yall says `observed start N`; it does not pretend that the observed ordinal is the job's absolute start number.

Exit code `100` from Yall's Condor node launcher is displayed as a startup failure before the payload marker. Exit code `101` is displayed as a Yall-worker failure after startup classification. Yall does not use those launcher codes to claim that a particular scientific command ran.

## Correlating Yall attempts

For campaigns with the scheduler provenance introduced before this feature, Yall can directly attach an attempt to a particular scheduler execution when all of the following agree:

- the Condor job ID recorded in the attempt;
- a complete-from-submit event sequence for that job;
- the attempt's `NumJobStarts` value;
- the corresponding execution start exists in the event log.

That relationship is rendered as `association=direct`. If the job can be identified but the individual execution cannot, the attempt remains explicitly `execution=unknown`. Older campaigns without scheduler identity are still useful: their event logs, Condor history, Yall attempts, resumes, and amendments are shown without fabricating missing links.

## Generations, retries, and recovery

Each initial submission or resume round is shown as its own generation. Resume reasons are displayed when recorded. DAG retry identity comes from the recorded `YallDAGRetry` evidence and is not assumed to reset merely because a Rescue DAG was used.

`condor_q`, `condor_history`, event logs, and Yall provenance are complementary diagnostic sources. Conflicting evidence is marked rather than silently reconciled.

## Safety boundary

Execution-trace evidence is read-only diagnostics. It never changes task state, rewrites attempts, or participates in `resume` recovery decisions. Live scheduler reconciliation continues to use the existing conservative recovery path.

Detailed scheduler execution reconstruction is currently HTCondor-specific. On local, Slurm, and PBS campaigns, `-vvvv` retains the lower-level diagnostics and reports that the detailed scheduler execution trace is Condor-only.
