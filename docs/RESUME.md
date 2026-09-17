# Resume an existing campaign

`start` launches a frozen campaign once. `resume` continues that same campaign
without rerunning successful tasks or discarding its earlier attempts:

```bash
yall-run resume campaigns/<campaign-id> --dry-run
yall-run resume campaigns/<campaign-id>
yall-run status campaigns/<campaign-id>
```

`--dry-run` is available for queued backends. It checks recovery preconditions
without submitting or cancelling jobs. The existing local resume behavior is
unchanged. Resume means restarting unfinished **tasks**, not restoring the
memory or instruction pointer of an interrupted analysis executable.

## Common behavior

Queued recovery reads the frozen `campaign.json`, original rendered scripts,
archived worker and wrapper, previous submission records, task states and
latest attempt records. It does not reload the current Yallfile, re-expand run
lists, or replace the archived environment with the installed yall-run version.
Updating the installed package enables recovery for an older campaign, but does
not silently upgrade that campaign's bundled worker.

A successful scheduler query is required. Running, transferring, suspended,
unknown-state or still-removing jobs block recovery. Query failure is **not**
evidence that the queue is empty. Concurrent yall resume operations are guarded
by a campaign-local lock. Use the same scheduler/server and account context as
the original submission; this is not a cross-cluster migration facility.

Completed tasks are retained only if their declared outputs still exist and
their completed dependency chain is consistent. Output existence alone never
establishes completion. Missing external inputs block recovery; inputs produced
by unfinished tasks need not exist yet. These checks do not verify download
completion or file checksums: finish and verify raw-data transfers first.

Existing outputs of unfinished tasks are not deleted or silently overwritten.
Unless the original task/campaign already permits overwrites, inspect and move
partial products aside before resuming. Missing outputs of a completed task
require repair or a new campaign. The command does not silently invalidate
completed descendants or change an old campaign's overwrite policy.

Fix the original cause before resuming, for example rebuilding an executable
at the path recorded in the task. Referenced binaries, data and container images
remain external resources; resuming does not make a moving image immutable.

If you made an out-of-band repair that matters to the audit trail, annotate the
recovery round with `--reason`. For example:

```bash
mkdir -p /gpfs/.../final
yall-run resume campaigns/<campaign-id> --reason "Created missing final output directory"
```

Yall records that explanation in the resume record. It does not claim to have
observed, performed, or independently verified the external repair.

## HTCondor / DAGMan

Condor recovery uses the latest numbered Rescue DAG from the latest recorded
DAGMan submission. Its `DONE` nodes must agree with Yall's verified completed
tasks. A missing or conflicting rescue file stops recovery rather than guessing.

The original DAG and submit descriptions are staged into a new recovery round.
The selected rescue file is copied unchanged and selected explicitly with
`condor_submit_dag -dorescuefrom N campaign.dag`. Scheduler stdout, stderr and
job event logs are redirected to the round's own directory, preserving old
logs. Node scripts still invoke the original bundled worker and archived
wrapper against the original campaign directory. The new DAGMan cluster ID is
recorded and used by `yall-run status`.

Do not use `--cancel-pending` to stop an active Condor DAG. Let DAGMan finish or
explicitly stop it using the scheduler, then allow it to write its rescue file.

Reference: [HTCondor DAGMan completion and rescue documentation](https://htcondor.readthedocs.io/en/latest/automated-workflows/dagman-completion.html).

## Slurm and PBS

These adapters reconstruct the unfinished portion of the workflow. They submit
new held jobs only for failed, interrupted or unstarted tasks, with new
`afterok` dependencies between those jobs. Successful ancestors do not become
new scheduler dependencies. All accepted job IDs are recorded before the held
jobs are released.

This deliberately does not equate Slurm `requeue` or PBS `qrerun` with workflow
recovery. In particular, Slurm documents that a failed dependency does not
become runnable merely because its parent is later requeued successfully.

Failed workflows can leave pending or held descendants in the queue. Recovery
refuses to duplicate them. After checking the plan, explicitly permit their
cancellation and replacement with:

```bash
yall-run resume campaigns/<campaign-id> --cancel-pending --dry-run
yall-run resume campaigns/<campaign-id> --cancel-pending
```

Only recorded jobs belonging to this campaign are targeted. State is checked
again before cancellation and absence is confirmed afterward; asynchronous
cancellation may require another invocation once the jobs have left the queue.
Slurm cancellation additionally uses `scancel --state=PENDING`. PBS lacks that
state-filtered operation, so avoid concurrent job changes by other tools or
operators during recovery. Never use this as a way to bypass an administrative
hold; resolve the site's reason for the hold first.

Slurm and PBS recovery remain experimental, with simulated scheduler tests
rather than production-cluster validation. Federated Slurm submissions and
cross-server PBS migration are not supported by this recovery implementation.

Reference: [Slurm dependency semantics](https://slurm.schedmd.com/sbatch.html#OPT_dependency).

## Status and provenance

For queued campaigns, `status` reports an unfinished on-disk `running` attempt
as `interrupted` when a successful scheduler query finds no active job. If the
query fails, that attempt is `unknown`, not assumed dead. A retained terminal
attempt record can repair a stale mutable state record during resume. Status
itself is read-only; JSON output also includes `recorded_state` and scheduler
query diagnostics. Scheduler evidence does not establish scientific success.

Each recovery round has separate records:

```text
resumes/
  0001/
    resume.json
    condor/                 # or slurm/ or pbs/
      submit.json
      ...staged DAG/scripts...
      logs/
```

The record includes the selected and retained tasks, original and reconciled
states, scheduler snapshot, timestamps, commands, accepted job IDs, the optional
operator-supplied reason and, for Condor, the rescue source and SHA-256. Old
`start.json`, original `submit.json`,
`campaign.json`, worker/wrapper files and attempt directories are not replaced.
New workers use the next attempt number in the same campaign. All recorded
submission generations participate in queue checks, not just the newest IDs.
Relational export includes recovery rounds in the `resume` table, including the
optional reason. Local state-count summaries are also exported through
`resume_count`.

If a submit command times out or reports acceptance without a parseable job ID,
its `submit.json` retains an `in_flight` marker. Further recovery is refused
because a job may have been accepted. Inspect the scheduler and reconcile the
record before retrying; do not erase it and submit blindly. A failed partial
Slurm/PBS submission leaves acknowledged held jobs recorded, so a later
`--cancel-pending` recovery can safely handle them. No jobs are released before
the complete replacement graph has been recorded.

A process killed during recovery may leave `state/resume.lock`. Inspect its
`owner.json`, all recovery records and scheduler jobs before manually removing
a stale lock. Do not run manual scheduler resubmissions concurrently with yall
resume. Manually created scheduler jobs that were never recorded by yall may
require manual reconciliation, particularly on Slurm and PBS.
