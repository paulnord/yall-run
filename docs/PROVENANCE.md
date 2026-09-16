# Provenance

yall-run treats provenance as part of the campaign model rather than as an optional report generated afterward.

The campaign directory is the provenance anchor. Scientific outputs may live elsewhere, but the campaign records the frozen workflow that produced them and the attempts that executed it.

## Creation provenance

`campaign.json` is the durable definition of the campaign. It records the campaign identity, creation environment, execution policy, task order, and each frozen task definition, including dependencies, command, working directory, resources, inputs, outputs, and overwrite policy.

Named values supplied by the Yallfile, including values imported with `@set` or `@env`, are resolved before execution and recorded with the campaign definition.

The archived `Yallfile` remains the source recipe used to create that campaign.

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
- execution host
- host Python version and interpreter path
- archived payload wrapper identity and planned launch argv
- start time

This separation means the launch conditions remain intact even if the task later fails.

### `attempt.json`

`attempt.json` is completed after execution. It records execution results such as:

- return code
- finish time
- timing information
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
`launch_command`; `command_pid` identifies the immediate child (the wrapper
when present), not necessarily the final application process. Timing includes
wrapper setup and payload execution, not queue wait.

This does not probe or assert the payload's Python, OS or container digest.
Creation-time executable lookup is explicitly labeled `context = "creation_host"`;
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
