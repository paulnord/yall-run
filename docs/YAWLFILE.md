# Yawlfile syntax

`Yawlfile` is the human-facing campaign format for yawl-run. The goal is to keep ordinary workflows readable without giving up the precise internal campaign model.

TOML remains supported as a generated/interchange format.

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

If the file is named `Yawlfile`, the specification argument is optional:

```bash
yawl-run validate
yawl-run plan
yawl-run start
```

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

Input globs are resolved when the campaign specification is loaded, so the resulting input list is frozen before execution.

### Static named values

Use `@set` for values that are part of the campaign description but should not be repeated in every path:

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

Task-level resource values override campaign defaults. The values are generic yawl-run policy; the Condor backend translates them to `request_cpus`, `request_memory`, and `request_disk`.

Campaign-level defaults can appear outside a task:

```text
backend condor
%cpus 1
%memory 2GB
%disk 2GB
```

Other campaign-level directives currently supported are `%getenv` and `%wrapper`.

`%cwd` is task-local.

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
@set dataset beam2026

pedestal-{run}:
    @each raw converted/{dataset}_raw_{run}.root
    @output pedestal pedestal/{dataset}_pedestal_{run}.root
    %memory 4GB
    ./make-pedestal @input.raw -o @output.pedestal
```

If the directory contains:

```text
converted/beam2026_raw_137.root
converted/beam2026_raw_138.root
converted/beam2026_raw_142.root
```

yawl-run expands the rule to three ordinary tasks:

```text
pedestal-137
pedestal-138
pedestal-142
```

Each task receives one matched `raw` input and produces its own declared output.

The input set is discovered and frozen when the specification is loaded. An `@each` pattern that matches nothing is an error.

## Patterned dependencies

A patterned child inherits the values of a patterned parent:

```text
check-{run}: pedestal-{run}
    @input pedestal pedestal/beam2026_pedestal_{run}.root
    ./check @input.pedestal
```

This expands one-to-one:

```text
pedestal-137 -> check-137
pedestal-138 -> check-138
pedestal-142 -> check-142
```

A non-patterned child depending on a patterned parent means fan-in from the whole family:

```text
summary: pedestal-{run}
    @input pedestal pedestal/beam2026_pedestal_{run}.root
    ./summarize @input.pedestal
```

The resulting `summary` task depends on every expanded pedestal task, and its `@input.pedestal` collection contains the corresponding files.

## TOML compatibility

Files ending in `.toml` continue to use the original precise TOML format. Existing TOML campaigns therefore keep working. Yawlfile syntax is intended for people; TOML remains useful for generators and interchange.
