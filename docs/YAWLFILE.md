# Yawlfile syntax

`Yawlfile` is the reusable workflow description for yawl-run. It plays roughly the same role as a Makefile: it describes how work fits together, but it is not itself a particular execution.

A concrete execution is a **campaign** created from the Yawlfile.

## Smallest useful file

```text
campaign hello
backend local

left:
    echo left

right:
    echo right

finish: left right
    echo done
```

A task header is `task-name:` followed by zero or more parent task names. Indented lines belong to that task.

Backends are `local`, `condor`, `slurm`, and `pbs`. Slurm and PBS support is experimental.

If the file is named exactly `Yawlfile`, the specification argument is optional:

```bash
yawl-run validate
yawl-run plan
yawl-run create
```

`Yawlfile` is the canonical default spelling. On a case-sensitive filesystem, `yawlfile` and `YAWLFILE` are different filenames. A differently named workflow file can be supplied explicitly.

`create` freezes the expanded task graph into a new campaign directory and runs nothing. By default, new campaign directories are created under `./campaigns`. Choose another container directory with:

```bash
yawl-run create --campaigns-dir /path/to/campaigns
```

`--campaigns-dir` names the directory that contains campaign directories; it is not the path of the individual campaign itself.

Launch the exact campaign printed by `create` with:

```bash
yawl-run start campaigns/<campaign-id>
```

Because `create` writes only that campaign path to standard output, it can be piped directly into `start`:

```bash
yawl-run create | yawl-run start
```

When the `CAMPAIGN_DIR` argument is omitted, `start` reads exactly one nonblank campaign path from standard input. An explicit argument always takes precedence.

A campaign can be started only once. To run the recipe again, create another campaign from the Yawlfile.

For a local campaign, use `-j N` on `create` to freeze the maximum number of concurrently active yawl tasks:

```bash
yawl-run create --backend local -j 4 | yawl-run start
```

Local campaigns default to one task at a time. `-j` is intentionally invalid for queued backends. Processor requests belong to `%cpus` task policy instead.

## Data declarations: `@`

`@` declares data or named values.

```text
convert:
    @input raw raw/run137.h2g
    @output root converted/raw_137.root
    ./Convert -i @input.raw -o @output.root
```

The command is parsed into an argv array. A token such as `@input.raw` or `@output.root` expands to the path or paths carrying that role. `@inputs` and `@outputs` expand to every declared input or output.

A role may contain more than one path:

```text
merge:
    @input part a.root b.root c.root
    @output merged merged.root
    ./merge @input.part -o @output.merged
```

Input globs are resolved when the Yawlfile is loaded during campaign creation, so the resulting input list is frozen before execution.

### Static named values

Use `@set` for values that are part of the workflow description but should not be repeated in every path:

```text
@set dataset beam2026

convert:
    @input raw raw/{dataset}_raw_137.h2g
    @output root converted/{dataset}_raw_137.root
    ./Convert -i @input.raw -o @output.root
```

Known `{name}` placeholders from `@set` are substituted before task patterns are expanded.

## Execution policy: `%`

`%` directives describe how a task should run rather than what data it consumes.

```text
heavy-analysis:
    %retry 2
    %cpus 4
    %memory 8GB
    %disk 10GB
    ./Analyze input.root
```

Task-level resource values override campaign defaults. Condor maps CPU, memory, and disk requests directly. Slurm maps CPU and memory requests. PBS maps CPU and memory requests. Disk requests remain in provenance for Slurm and PBS because scratch resources are site-specific.

Campaign-level defaults can appear outside a task:

```text
backend condor
%cpus 1
%memory 2GB
%disk 2GB
```

Other campaign-level directives currently supported are `%getenv` and `%wrapper`.

`%cwd` is task-local.

`%overwrite` is also task-local. It permits that task to run when one or more of its declared output paths already exist:

```text
analysis:
    %overwrite
    @output root result.root
    ./Analyze -o @output.root
```

`%overwrite` never deletes, truncates, empties, or otherwise modifies an existing output itself. It only permits the task command to run. The command remains responsible for whatever replacement behavior it performs.

The backend is frozen into the campaign at `create` time. `yawl-run create --backend local`, `--backend condor`, `--backend slurm`, or `--backend pbs` can deliberately override the Yawlfile for a particular campaign.

`-j` and `%cpus` are deliberately different:

- `-j N` is local campaign concurrency: at most `N` dependency-ready tasks are active at once.
- `%cpus N` is a per-task resource request for queued execution.
- local yawl currently records `%cpus` but does not use it as a local scheduling weight.

## Declared output protection

A declared output is treated as a product owned by its task. Before a campaign is launched, `start` preflights every declared output path that is not covered by `%overwrite`. If any of those paths already exists, the campaign is not started and no local task or queued job is launched.

This applies equally to files, directories, and symlinks. Parent directories that merely contain an output are not protected unless the directory itself is declared as an output.

For example:

```text
analysis:
    @output root results/run308.root
    @output plots results/run308-plots
    ./Analyze ...
```

If either `results/run308.root` or `results/run308-plots` already exists, `yawl-run start` refuses the campaign by default. The campaign remains unstarted, so the user can inspect or remove the conflicting product, or explicitly retry the start with overwrite permission.

There are two ways to permit replacement:

```text
analysis:
    %overwrite
    @output root results/run308.root
    ./Analyze ...
```

permits replacement for that task, while:

```bash
yawl-run start --overwrite campaigns/<campaign-id>
```

permits pre-existing declared outputs for every task in that campaign start. The campaign-wide choice is recorded in `start.json`.

Neither form causes yawl-run to remove existing data. They only disable the existence guard for the affected task or campaign start.

The worker repeats the output existence check immediately before each task command. This catches a product that appears after the campaign-wide start preflight but before that particular task becomes runnable. Such an attempt records failure kind `outputs_exist` and does not launch the command.

Two different expanded tasks may not declare the same output path. This is rejected while the Yawlfile is validated, regardless of `%overwrite`, because one campaign product has one owning task.

Automatic `%retry` follows the same worker-level rule. If a failed attempt leaves a declared output behind, the next attempt will stop at the output guard unless the task has `%overwrite`, the campaign was started with `--overwrite`, or the failed command cleaned up its partial product itself.

## Shell escape hatch

Ordinary commands become argv arrays and therefore do not require shell parsing. If a task intentionally needs shell syntax, prefix the command with `!`:

```text
report:
    @output text report.txt
    ! echo complete > @output.text
```

Named data references are shell-quoted when substituted into a `!` command.

A trailing backslash continues a long logical line.

## Pattern tasks: one input, one task, one output

`@each` maps a family of existing files into a family of tasks. Placeholders in braces are captured from the matching filename.

```text
pedestal-{run}:
    @each raw converted/data{run}.root
    @output pedestal pedestal/run{run}.root
    %memory 4GB
    ./make-pedestal @input.raw -o @output.pedestal
```

If the directory contains:

```text
converted/data123.root
converted/data138.root
converted/data142.root
```

yawl-run expands the rule to three ordinary tasks:

```text
pedestal-123
pedestal-138
pedestal-142
```

and resolves their outputs as:

```text
pedestal/run123.root
pedestal/run138.root
pedestal/run142.root
```

Each task receives one matched `raw` input and produces its own declared output.

Plain placeholders currently capture arbitrary non-path text. Thus `data123a.root` also matches `data{run}.root`, binding `run=123a`. Numeric-only typed captures are not yet part of the Yawlfile syntax.

The input set is discovered and frozen when the campaign is created. An `@each` pattern that matches nothing is an error.

## Patterned dependencies

A patterned child inherits the values of a patterned parent:

```text
check-{run}: pedestal-{run}
    @input pedestal pedestal/run{run}.root
    ./check @input.pedestal
```

This expands one-to-one:

```text
pedestal-123 -> check-123
pedestal-138 -> check-138
pedestal-142 -> check-142
```

A non-patterned child depending on a patterned parent means fan-in from the whole family:

```text
summary: pedestal-{run}
    @input pedestal pedestal/run{run}.root
    ./summarize @input.pedestal
```

The resulting `summary` task depends on every expanded pedestal task, and its `@input.pedestal` collection contains the corresponding files.

`examples/pi/Yawlfile` is a complete map-reduce example. Eight `partial-{chunk}` tasks run the same Python worker against different range files, then one `sum` task fans in all eight outputs.

## Campaign records

When a campaign is created, the exact source Yawlfile and the frozen expanded workflow are stored with the campaign:

```text
Yawlfile
campaign.json
start.json
state/
    partial-000.json
    partial-001.json
    sum.json
```

The archived `Yawlfile` is the exact input used at campaign creation. `campaign.json` records its original source path and SHA-256 alongside campaign identity, creation environment, execution policy, task order, and the frozen definition of each task. Task definitions include dependencies, command, cwd, resources, inputs, outputs, and overwrite policy.

The files under `state/` are mutable bookkeeping only. They contain the current task state, attempt count, and, after execution, the most recent return code. Keeping these files small lets workers update state independently without duplicating the full task definition.

`start.json` records how the frozen campaign was actually launched, including whether the campaign-wide `--overwrite` permission was requested. A rejected output preflight does not create `start.json`.

## Portable attempt provenance

Before each task attempt begins, yawl writes:

```text
<task>_attempt_001/provenance.json
```

This launch-provenance record contains the campaign identity, task and attempt identity, resolved inputs, declared outputs, command, cwd, resource requests, overwrite policy, host, Python version, and start time. It is written before the program starts and is not rewritten afterward.

The task process receives environment variables including:

```text
YAWL_CAMPAIGN_ID
YAWL_CAMPAIGN_NAME
YAWL_CAMPAIGN_DIR
YAWL_BACKEND
YAWL_TASK
YAWL_ATTEMPT
YAWL_PROVENANCE
```

`YAWL_PROVENANCE` points to that JSON file. Application-specific software may copy or embed it into its native output formats while yawl-run remains format-agnostic.

`attempt.json` is completed after execution with the return code, finish time, timing, stdout/stderr paths, pre-launch output observations, and final observed output metadata. A worker-level task stopped by the output guard records failure kind `outputs_exist` and no command return code because the command was never launched.

## One language

Yawlfile syntax is the campaign language. TOML campaign files from the prototype era are intentionally not supported. Keeping one syntax avoids duplicate semantics, duplicate documentation, and two parser paths that can drift apart.
