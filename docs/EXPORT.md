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

## Useful SQLite commands

The examples below assume the database is named `yawl.sqlite`. Each block is a complete shell command that can be copied and run directly.

### List campaigns and backends

```bash
sqlite3 -header -column yawl.sqlite \
'select campaign_id,name,backend from campaign order by campaign_id;'
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

```bash
sqlite3 -header -column yawl.sqlite \
'select c.campaign_id,c.backend,count(a.attempt) as attempts
 from campaign c
 left join attempt a using (campaign_id)
 group by c.campaign_id,c.backend
 order by c.campaign_id;'
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

```bash
sqlite3 -header -column yawl.sqlite \
'select campaign_id,task_name,attempt,state,real_seconds,hostname
 from attempt
 join attempt_provenance using (campaign_id,task_name,attempt)
 order by campaign_id,task_name,attempt;'
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

```bash
sqlite3 -header -column yawl.sqlite \
'select campaign_id,task_name,attempt,returncode,failure_kind
 from attempt
 where state = "failed"
 order by campaign_id,task_name,attempt;'
```

A clean archive simply prints the column headings and no rows.

### Trace the declared inputs for one task

Replace the campaign and task names with the ones you want to inspect:

```bash
sqlite3 -header -column yawl.sqlite \
'select role,path,creation_sha256
 from task_input
 where campaign_id = "root-muon-lifetime-20260903T174249Z-8f3f7155"
   and task_name = "fit-00"
 order by input_index;'
```

### Find tasks whose executable hash differs across campaigns

```bash
sqlite3 -header -column yawl.sqlite \
'select task_name,sha256,count(*) as campaigns
 from task_executable
 where sha256 is not null
 group by task_name,sha256
 order by task_name,campaigns desc;'
```

Different hashes for the same task name are a useful signal when comparing campaigns that may have run different executable builds.
