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

The backend is frozen into the campaign at `create` time. `yawl-run create --backend local`, `--backend condor`, `--backend slurm`, or `--backend pbs` can deliberately override the Yawlfile for a particular campaign.

`-j` and `%cpus` are deliberately different:

- `-j N` is local campaign concurrency: at most `N` dependency-ready tasks are active at once.
- `%cpus N` is a per-task resource request for queued execution.
- local yawl currently records `%cpus` but does not use it as a local scheduling weight.

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

When a campaign is created, the frozen workflow is stored once in `campaign.json`. The current state of each task is kept separately in compact files:

```text
campaign.json
start.json
state/
    partial-000.json
    partial-001.json
    sum.json
```

`campaign.json` contains campaign identity, creation environment, execution policy, task order, and the frozen definition of each task. Task definitions include dependencies, command, cwd, resources, inputs, and outputs.

The files under `state/` are mutable bookkeeping only. They contain the current task state, attempt count, and, after execution, the most recent return code. Keeping these files small lets workers update state independently without duplicating the full task definition.

## Portable attempt provenance

Before each task attempt begins, yawl writes:

```text
<task>_attempt_001/provenance.json
```

This launch-provenance record contains the campaign identity, task and attempt identity, resolved inputs, declared outputs, command, cwd, resource requests, host, Python version, and start time. It is written before the program starts and is not rewritten afterward.

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

`attempt.json` is completed after execution with the return code, finish time, timing, stdout/stderr paths, and observed output metadata.

## One language

Yawlfile syntax is the campaign language. TOML campaign files from the prototype era are intentionally not supported. Keeping one syntax avoids duplicate semantics, duplicate documentation, and two parser paths that can drift apart.
