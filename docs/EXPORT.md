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

The export is a derived view. Campaign JSON remains the canonical computational record; exporting does not modify campaign directories.

## Example queries

Find failed attempts:

```sql
SELECT campaign_id, task_name, attempt, returncode, failure_kind
FROM attempt
WHERE state = 'failed';
```

Trace the declared inputs for one task:

```sql
SELECT role, path, creation_sha256
FROM task_input
WHERE campaign_id = ? AND task_name = ?
ORDER BY input_index;
```

Find tasks whose executable binary hash differs across campaigns:

```sql
SELECT task_name, sha256, COUNT(*) AS campaigns
FROM task_executable
WHERE sha256 IS NOT NULL
GROUP BY task_name, sha256
ORDER BY task_name, campaigns DESC;
```
