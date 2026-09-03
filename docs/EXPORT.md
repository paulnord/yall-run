# Relational provenance export

`yawl-run export` reads one campaign directory or recursively discovers campaigns below one or more directories and writes the durable yawl JSON records into normalized relational tables.

```bash
yawl-run export campaigns \
    --sqlite yawl.sqlite \
    --sql yawl.sql \
    --csv-dir yawl-csv
```

Any one of the three output forms may be requested:

- `--sqlite FILE` creates or updates a SQLite database.
- `--sql FILE` writes SQLite-compatible schema and `INSERT OR REPLACE` statements.
- `--csv-dir DIR` writes one CSV file per table.

The same natural identity used by yawl is used as the relational primary key. No surrogate campaign, task, or attempt IDs are invented:

| Table | Primary key |
| --- | --- |
| `campaign` | `campaign_id` |
| `campaign_set` | `campaign_id, name` |
| `task` | `campaign_id, task_name` |
| `task_parent` | `campaign_id, task_name, parent_task_name` |
| `task_input` | `campaign_id, task_name, input_index` |
| `task_output` | `campaign_id, task_name, output_index` |
| `task_executable` | `campaign_id, task_name` |
| `campaign_start` | `campaign_id` |
| `task_state` | `campaign_id, task_name` |
| `attempt` | `campaign_id, task_name, attempt` |
| `attempt_input` | `campaign_id, task_name, attempt, input_index` |
| `attempt_output` | `campaign_id, task_name, attempt, output_index` |
| `attempt_provenance` | `campaign_id, task_name, attempt` |
| `resume` | `campaign_id, resume_number` |
| `resume_count` | `campaign_id, resume_number, phase, state` |

The input/output indexes preserve the order of repeated JSON entries and only exist where the underlying record is one-to-many. Timing values stay on `attempt` because there is exactly one timing record per attempt. Resume state counts are separated into `resume_count` because each resume has multiple counts for two phases.

The export is a derived view. Campaign JSON remains the canonical computational record; exporting does not modify campaign directories. Older campaigns can still be exported when newer provenance fields are absent; unavailable values are left `NULL`.

## Useful SQLite queries

The examples below use `sqlite3 -header -column yawl.sqlite` so results are easy to read.

### List campaigns and backends

```sql
SELECT campaign_id, name, backend
FROM campaign
ORDER BY campaign_id;
```

Example output:

```text
campaign_id                                   name                backend
--------------------------------------------  ------------------  -------
hello-yawl-20260902T143712Z-c95e7276          hello-yawl          condor
pi-map-reduce-20260903T174458Z-f07e15a4       pi-map-reduce       local
root-muon-lifetime-20260903T174249Z-8f3f7155  root-muon-lifetime  local
```

### Count recorded attempts by campaign

A `LEFT JOIN` keeps campaigns that have no attempt records, which is useful when looking at older campaign formats.

```sql
SELECT c.campaign_id,
       c.backend,
       COUNT(a.attempt) AS attempts
FROM campaign AS c
LEFT JOIN attempt AS a USING (campaign_id)
GROUP BY c.campaign_id, c.backend
ORDER BY c.campaign_id;
```

Example output:

```text
campaign_id                                   backend  attempts
--------------------------------------------  -------  --------
hello-yawl-20260902T143712Z-c95e7276          condor   0
pi-map-reduce-20260903T174458Z-f07e15a4       local    10
root-muon-lifetime-20260903T174249Z-8f3f7155  local    25
```

### Show attempt timing and execution host

This joins the post-execution attempt record to the launch provenance record using yawl's natural attempt key.

```sql
SELECT campaign_id,
       task_name,
       attempt,
       state,
       real_seconds,
       hostname
FROM attempt
JOIN attempt_provenance USING (campaign_id, task_name, attempt)
ORDER BY campaign_id, task_name, attempt;
```

Example output:

```text
campaign_id                                   task_name    attempt  state      real_seconds  hostname
--------------------------------------------  -----------  -------  ---------  ------------  ----------------------
pi-map-reduce-20260903T174458Z-f07e15a4       partial-000  1        completed  0.113744      starsub01.sdcc.bnl.gov
pi-map-reduce-20260903T174458Z-f07e15a4       sum          1        completed  0.066942      starsub01.sdcc.bnl.gov
root-muon-lifetime-20260903T174249Z-8f3f7155  fit-00       1        completed  8.558412      starsub01.sdcc.bnl.gov
root-muon-lifetime-20260903T174249Z-8f3f7155  simulate-00  1        completed  3.053926      starsub01.sdcc.bnl.gov
```

### Find failed attempts

```sql
SELECT campaign_id,
       task_name,
       attempt,
       returncode,
       failure_kind
FROM attempt
WHERE state = 'failed'
ORDER BY campaign_id, task_name, attempt;
```

A clean archive simply prints the column headings and no rows.

### Find legacy tasks with no frozen command

Older campaign records may predate frozen task commands. The exporter preserves that absence as `NULL` rather than inventing a value.

```sql
SELECT campaign_id, task_name
FROM task
WHERE command_json IS NULL
ORDER BY campaign_id, task_name;
```

This is also a quick way to identify which campaigns were written with an older provenance schema.

### Trace the declared inputs for one task

```sql
SELECT role, path, creation_sha256
FROM task_input
WHERE campaign_id = ? AND task_name = ?
ORDER BY input_index;
```

For interactive SQLite use, replace the `?` placeholders with quoted values:

```sql
SELECT role, path, creation_sha256
FROM task_input
WHERE campaign_id = 'root-muon-lifetime-20260903T174249Z-8f3f7155'
  AND task_name = 'fit-00'
ORDER BY input_index;
```

### Find tasks whose executable hash differs across campaigns

```sql
SELECT task_name,
       sha256,
       COUNT(*) AS campaigns
FROM task_executable
WHERE sha256 IS NOT NULL
GROUP BY task_name, sha256
ORDER BY task_name, campaigns DESC;
```

Different hashes for the same task name are a useful signal when comparing campaigns that may have run different executable builds.
