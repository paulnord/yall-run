# Campaigns

A Yawlfile is a reusable workflow recipe. A campaign is one frozen instance of that recipe.

The core model is:

```text
Yawlfile
  -> campaign
       -> task
            -> attempt
```

This separation is central to yawl-run: workflow definition happens before execution, and the exact expanded workflow is preserved for later inspection.

## Create, then start

Create a campaign with:

```bash
yawl-run create
```

By default, yawl-run creates a new campaign directory under `./campaigns` and prints its path. Choose another containing directory with:

```bash
yawl-run create --campaigns-dir /path/to/campaigns
```

`create` freezes the expanded task graph, backend choice, task policy, named data, and campaign creation information. It does not execute or submit the campaign.

Start the exact campaign later:

```bash
yawl-run start ./campaigns/<campaign-id>
```

Because `create` writes the campaign path to standard output, the common one-line form is:

```bash
yawl-run create | yawl-run start
```

If a campaign path is supplied explicitly, `start` uses that argument. Otherwise it reads exactly one nonblank campaign path from standard input.

A campaign can be started only once. Running the same Yawlfile again means creating a new campaign.

## A frozen workflow

The archived Yawlfile records the source recipe. `campaign.json` records the concrete campaign definition after named values, file discovery, pattern expansion, and creation-time options have been resolved.

That makes the campaign directory the provenance anchor for the execution even when the scientific output files live elsewhere.

## Campaign directory layout

A campaign keeps one durable definition of the workflow and small mutable files for current state. A typical local campaign looks like:

```text
campaign.json
start.json
state/
    partial-000.json
    sum.json
partial-000_attempt_001/
    provenance.json
    attempt.json
    stdout.log
    stderr.log
sum_attempt_001/
    provenance.json
    attempt.json
    stdout.log
    stderr.log
```

### `campaign.json`

This is the durable campaign definition. It contains campaign identity, creation information, execution policy, task order, and each frozen task definition, including dependencies, command, working directory, resources, inputs, and outputs.

### `start.json`

This records campaign launch information once the campaign has been started.

### `state/`

Files under `state/` are deliberately small and mutable. A task state file tracks the current state, attempt count, and most recent return code without duplicating the full frozen task definition.

### Attempt directories

Each task attempt gets its own directory containing launch provenance, execution results, stdout, and stderr. Retries therefore preserve earlier attempts instead of overwriting their records.

See [PROVENANCE.md](PROVENANCE.md) for the distinction between `provenance.json` and `attempt.json`.

## Task families and dependencies

Pattern tasks are expanded before the campaign is created. By the time execution begins, the campaign contains ordinary concrete task names and dependencies.

A patterned child can follow the corresponding parent in the same family, while a plain task depending on a patterned parent naturally fans in from the complete expanded family.

For the pattern language, explicit `@each` values, correlated tuples, and named values, see [YAWLFILE.md](YAWLFILE.md).

## Execution policy belongs to the campaign

Options that affect execution are frozen when appropriate rather than rediscovered later. For example, local concurrency is selected when the campaign is created:

```bash
yawl-run create --backend local -j 4
```

Task-level resource and retry policy comes from the Yawlfile. Backend-specific rendering is then derived from the frozen campaign.

For backend behavior and resource translation, see [BACKENDS.md](BACKENDS.md).

## Inspecting a campaign

Check current state with:

```bash
yawl-run status ./campaigns/<campaign-id>
```

The campaign directory remains useful after execution because it ties the frozen workflow, task state, logs, attempts, and provenance together in one durable record.
