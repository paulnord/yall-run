# Execution trace status

**Draft status:** this branch currently implements the scheduler-provenance foundation only. The CLI does not yet accept `-vvvv`; event-log parsing and the trace renderer remain to be implemented.

`status -vvvv` is intended to reconstruct how a task moved through Yall and the scheduler, not merely print more fields.

The trace model has four nested identities:

1. campaign generation: original submission or a numbered resume round;
2. DAG node try: the initial DAGMan node run plus `%retry` reruns;
3. scheduler execution start: repeated starts of one Condor job after startup retry, eviction, hold/release or other scheduler events;
4. Yall attempt: one worker invocation that reached Yall attempt creation.

These are deliberately not collapsed into one counter. A scheduler execution may fail before a Yall attempt exists, and one Condor job ID may execute more than once.

## Provenance foundation

New Condor campaigns stamp each DAG node job with `YallDAGRetry=$(RETRY)` through a DAGMan `VARS` ClassAd. The worker also reads the `_CONDOR_JOB_AD` snapshot, when available, and records scheduler identity in attempt `provenance.json`: cluster/proc and global job ID, DAGMan job and node, DAG retry number, number of job starts, execution host and start timestamps. The job-ad snapshot path and SHA-256 are recorded as well.

The worker accepts only validated, case-insensitive, allowlisted literal attributes. Missing, undefined, malformed, duplicate, or unevaluated fields are left unknown, with diagnostic errors recorded. It does not evaluate ClassAd expressions or copy arbitrary job-ad fields. Reads are limited to 1 MiB and regular files; missing or invalid telemetry must not fail the payload. A local campaign nested inside a Condor job does not inherit that outer job identity.

The snapshot path is temporary scheduler storage, not an archived full ClassAd. The selected values and digest are durable in Yall provenance. A complete cluster/proc pair creates an exact **job-level** bridge from Yall attempts to Condor job/DAG identity for future campaigns. Matching a particular execution start additionally requires suitable start/event evidence; do not equate `NumJobStarts` with the ordinal of events in a possibly incomplete log. Older campaigns will need best-effort correlation from durable Yall timestamps plus Condor event/history records, and inferred associations must be labeled as inferred.

## Planned `-vvvv` renderer

The execution trace will group evidence by generation, DAG retry, scheduler job and individual execution start, then attach Yall attempts beneath the execution that launched them. Condor event logs provide repeated execute/evict/terminate/hold/release events for one job ID; `condor_history` supplies retained job-level evidence; Yall provenance supplies payload-level facts.

Every rendered relationship should be either directly evidenced or explicitly marked `inferred`.

## Review constraints for the next slice

- Never infer a job ID from an expression, `undefined`, or a missing attribute.
- Keep repeated starts, node retry counts, Yall attempts, and resume rounds separate.
- Do not assume a rescued DAG resets its retry counter to zero; display the recorded value.
- A Yall input/output guard can fail before the payload starts. The launcher's non-startup failure code does not by itself prove scientific code ran.
- A missing log, missing execute event, or ambiguous correlation must remain explicitly unknown.
- Event-log and history observations must never mutate campaign records or drive recovery decisions.
- Existing frozen campaigns retain their bundled worker. These fields become available only in newly created campaigns.
