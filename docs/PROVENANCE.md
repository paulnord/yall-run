# Provenance

yall-run treats provenance as part of the campaign model rather than as an optional report generated afterward.

The campaign directory is the provenance anchor. Scientific outputs may live elsewhere, but the campaign records the frozen workflow that produced them and the attempts that executed it.

## Creation provenance

`campaign.json` is the durable definition of the campaign. It records the campaign identity, creation environment, execution policy, task order, and each frozen task definition, including dependencies, command, working directory, resources, inputs, outputs, and overwrite policy.

Named values supplied by the Yallfile, including values imported with `@set` or `@env`, are resolved before execution and recorded with the campaign definition.

The archived `Yallfile` remains the source recipe used to create that campaign.

## User account provenance

Starting with **0.12.0a7**, OS-account snapshots are **off by default**.
Choose once at campaign level, before the tasks:

```text
# Set to full to record operator accounts when permitted by your site's privacy policy.
%account-provenance off
```

Use `full` for deliberate operator attribution, or override the recipe at creation:

```bash
yall-run create --account-provenance full
```

The explicit CLI choice wins over the Yallfile, which otherwise defaults to `off`.
The resolved choice is frozen in `campaign.json` at `provenance_policy.accounts`.
`recipe_accounts` retains the recipe's choice so command-only amendments remain
possible after a CLI override. Editing the recipe cannot change a frozen policy.
`plan` displays the recipe policy; `create` and `start` report the resolved policy
on stderr, preserving create's single-path stdout and structured plan output.

The policy is resolved before preflight and before collecting any identity. The
same frozen choice governs creation, submission, attempts (including failures
and retries), local resumes, and local or standalone scheduler lifecycle hooks.
No live environment variable enables or disables recording on workers.

| Record | Field | Account being recorded when full |
| --- | --- | --- |
| `campaign.json` | `creation.user` | Campaign creator |
| `start.json` | `user` | Initial starter/submitter |
| `<task>_attempt_NNN/provenance.json` | `execution.user` | Host worker for this attempt |

With `off`, Yall does not query OS-account IDs or the account database. Each
snapshot instead contains `{"recorded": false, "reason": "disabled_by_policy"}`.
This distinguishes intentional omission from an unavailable account lookup.
Invalid modes fail before running work; they never fall back to full collection.

With `full`, snapshots contain `username`, `effective_username`, `uid`, `gid`,
`euid`, `egid`, and name-lookup source fields. Names come from `pwd.getpwuid`
using the process's real/effective IDs, not inherited `USER`, `LOGNAME`, or
`SUDO_USER`. Unavailable values stay null with diagnostics; missing POSIX APIs
or account databases do not prevent execution. No full account entry is copied.

The submission snapshot is captured before scheduling in `state/start-pending.json`
and retained at finalization. A legacy pending snapshot without an account stays
unknown when full recording is selected, rather than being attributed to the
finalizer. An off campaign always writes the omission marker.

These describe host process accounts, not the human behind sudo/shared accounts
or a possibly different account inside a payload container. Interpret IDs with
the hostname. These are observations, not authenticated or tamper-proof auditing.

**This is not an anonymization switch.** Paths, command arguments, logs, scheduler
records, and application outputs may still identify people. It does not redact
old records or control what tasks, hooks, wrappers, or the scheduler write.
Access controls, retention, notices, and review before sharing remain site concerns.
Redacted export is a separate feature and is not implemented by this switch.

```bash
jq '.provenance_policy, .creation.user' "$CAMPAIGN/campaign.json"
jq '.user' "$CAMPAIGN/start.json"
jq '.execution.user' "$CAMPAIGN"/*_attempt_*/provenance.json
```

For legacy campaigns lacking a policy, updated code defaults to off for additional
snapshots. Existing history is not erased or backfilled. Already archived workers
and hook runners are unchanged by upgrading an installation; create a fresh
campaign for consistent policy enforcement. SQL/CSV exports are not anonymized.

## Attempt provenance

Each task attempt has two distinct records:

```text
<task>_attempt_001/
    provenance.json
    attempt.json
    stdout.log
    stderr.log
```

### `provenance.json`

`provenance.json` is written before the task command begins and is not rewritten afterward. It records portable launch provenance such as:

- campaign identity
- task and attempt identity
- command
- working directory
- resolved inputs
- declared outputs
- requested resources
- execution host, kernel, architecture, CPU identity, and CPU affinity
- executing host account and real/effective user and group IDs
- selected runtime environment values that can affect threading or numerical execution
- common POSIX resource limits
- host Python version and interpreter path
- archived payload wrapper identity and planned launch argv
- start time

This separation means the launch conditions remain intact even if the task later fails.

### `attempt.json`

`attempt.json` is completed after execution. It records execution results such as:

- return code
- finish time
- timing information and child-process resource usage when available
- stdout and stderr locations
- observed output metadata

Retries receive new attempt directories, preserving the records from earlier attempts.

## Provenance exposed to the launched program

The launched task receives these environment variables:

```text
YALL_CAMPAIGN_ID
YALL_CAMPAIGN_NAME
YALL_CAMPAIGN_DIR
YALL_BACKEND
YALL_TASK
YALL_ATTEMPT
YALL_PROVENANCE
YALL_TASK_CWD
```

`YALL_PROVENANCE` points to the attempt's launch-provenance JSON. `YALL_TASK_CWD`
is the frozen working directory used to launch the wrapper/payload. These
variables are provided to the wrapper, which must forward them when required.
A container that reads the JSON must make that host path accessible.

Application-specific programs can use that path to copy or embed yall provenance into their own native output formats without requiring yall-run to understand ROOT files, HDF5 files, databases, or other scientific formats.

## Archived execution wrappers

For every backend, `%wrapper PATH [ARG ...]` copies the executable into the campaign's `environment/` directory and freezes its arguments separately. The wrapper record contains:

- `source`: resolved source executable path
- `path`: archived executable path used by the jobs
- `sha256` and `size_bytes`: fingerprint of the archived executable bytes
- `args`: ordered argument list, including literal separators and empty strings

The hash covers the executable, not the arguments. Both are recorded so a wrapper invocation can be reconstructed without parsing a shell command. The record is stored in `campaign.json` at `execution.wrapper`. `start.json` copies the execution policy when the campaign starts. Relational exports of started campaigns preserve this policy in `campaign_start.execution_json`.

Path-only wrappers have an empty argument list. Payload-only wrapping is a
pre-beta behavior change; no legacy invocation mode or migration is provided.

Only the wrapper executable itself is archived. Resources named by its arguments or otherwise referenced at runtime are not copied automatically. For example, the EIC example archives `run-in-eic-shell.sh` and freezes the selected `EIC_SHELL` path as an argument, but it does not archive that `eic-shell` installation or its container image. A launcher that ultimately refers to a moving image tag therefore remains non-immutable.

The worker's launch provenance has `execution.context = "host"`, its Python
version/interpreter, `execution.wrapper` (the frozen record, or null), and
`execution.launch_command` (the exact planned argv). The task's `command` remains
the scientific command as written after expansion. A guard can reject the task
before that planned invocation is executed. Final attempts also record
`launch_command`; `launch_pid` identifies the host worker's immediate child
(the wrapper when present), not necessarily the final application process. Timing includes
wrapper setup and payload execution, not queue wait.

For Condor attempts, yall also records an allowlisted snapshot of
`_CONDOR_MACHINE_AD` when HTCondor provides it, including slot identity,
architecture, CPU family/model, assigned CPUs, and memory. The raw machine ad is
not copied into provenance; its SHA-256 is recorded alongside the parsed fields.

The runtime environment snapshot is deliberately allowlisted rather than a dump
of the full environment. It covers common thread-control, accelerator-selection,
locale, and allocator/runtime variables. These values describe the **host
worker** environment. A wrapper can still change the payload environment, and
its frozen argv remains the authoritative record of such explicit wrapper
settings.

This does not probe or assert the payload's Python, OS, compiler, libraries, or
container digest. Creation-time executable lookup is explicitly labeled
`context = "creation_host"`;
a host PATH candidate/hash is not proof of the binary selected inside a wrapper.
Record application/container identity in the application layer when needed.
The complete wrapper/launch details are in canonical JSON; existing relational
exports retain host fields and campaign execution JSON, not separate new wrapper
tables.

See [Execution wrappers](WRAPPERS.md) for the contract.

## Mutable state is separate

Files under `state/` are deliberately small and mutable. They track current task state and attempt counts, while the frozen task definitions remain in `campaign.json` and attempt history remains in attempt directories.

For the complete campaign layout, see [CAMPAIGNS.md](CAMPAIGNS.md).

## Relational export

Campaign JSON remains the canonical record. One or more campaign trees can also be scraped into SQLite, a SQLite-compatible SQL dump, or normalized CSV tables for querying and reporting:

```bash
yall-run export campaigns \
    --sqlite yall.sqlite \
    --sql yall.sql \
    --csv-dir yall-csv
```

Export does not modify the campaign directories, and older campaigns can be included when newer provenance fields are absent.

See [EXPORT.md](EXPORT.md) for the schema, primary keys, and example queries.


## Amendment provenance

A campaign amendment is an immutable overlay under `amendments/NNNN/`; it does not rewrite the archived Yallfile or `campaign.json`. Each amendment archives the revised Yallfile and records exact before/after task command changes. Attempts launched through an amendment record the effective command plus amendment number/path/hash in `provenance.json`. See [Campaign amendments](AMENDMENTS.md).
