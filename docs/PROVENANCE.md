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
- Python version
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
```

`YALL_PROVENANCE` points to the attempt's launch-provenance JSON.

Application-specific programs can use that path to copy or embed yall provenance into their own native output formats without requiring yall-run to understand ROOT files, HDF5 files, databases, or other scientific formats.

## Archived execution wrappers

When a `%wrapper` is used for a queued backend, yall-run copies it into the campaign's `environment/` directory and records its source path, size, and SHA-256. This preserves the wrapper that was selected when the campaign was created.

See [BACKENDS.md](BACKENDS.md) for wrapper execution behavior.

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
