# Yallfile syntax

`Yallfile` is the reusable workflow description for yall-run. It plays roughly the same role as a Makefile: it describes how work fits together, but it is not itself a particular execution.

A concrete execution is a **campaign** created from the Yallfile.

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

If the file is named exactly `Yallfile`, the specification argument is optional:

```bash
yall-run validate
yall-run plan
yall-run create
```

`Yallfile` is the canonical default spelling. On a case-sensitive filesystem, `yallfile` and `YALLFILE` are different filenames. A differently named workflow file can be supplied explicitly.

`create` freezes the expanded task graph into a new campaign directory. It runs
any explicitly declared `%preflight` commands on the creation host, but does not
run or submit graph tasks. By default, new campaign directories are created under
`./campaigns`. Choose another container directory with:

```bash
yall-run create --campaigns-dir /path/to/campaigns
```

`--campaigns-dir` names the directory that contains campaign directories; it is not the path of the individual campaign itself.

Launch the exact campaign printed by `create` with:

```bash
yall-run start campaigns/<campaign-id>
```

Because `create` writes only that campaign path to standard output, it can be piped directly into `start`:

```bash
yall-run create | yall-run start
```

When the `CAMPAIGN_DIR` argument is omitted, `start` reads exactly one nonblank campaign path from standard input. An explicit argument always takes precedence.

A campaign can be started only once. To run the recipe again, create another campaign from the Yallfile.

For a local campaign, use `-j N` on `create` to freeze the maximum number of concurrently active yall tasks:

```bash
yall-run create --backend local -j 4 | yall-run start
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

Input globs are resolved when the Yallfile is loaded during campaign creation, so the resulting input list is frozen before execution.

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

### Import a named value from the environment

Use `@env NAME` when a value should come from the environment that runs `yall-run validate`, `plan`, or `create`:

```text
@env DATA_ROOT

convert-{run}:
    @each run 101 102 103
    @input raw {DATA_ROOT}/run{run}.dat
    @output result work/run{run}.out
    ./convert @input.raw -o @output.result
```

For example, a shell could provide:

```bash
export DATA_ROOT=/data/experiment/raw
```

`@env DATA_ROOT` reads that value while the Yallfile is parsed. The value participates in the same `{DATA_ROOT}` substitution as an `@set` value. It is therefore resolved before pattern expansion and before the campaign is created.

This is deliberately different from ordinary command environment inheritance. `@env` is a **campaign-definition input**, not a worker-time lookup. Once `yall-run create` succeeds, the expanded task paths and commands in `campaign.json` contain the resolved value, and the imported value is also recorded with the campaign's named values. Changing `DATA_ROOT` later does not change that campaign.

The archived `Yallfile` remains byte-for-byte the source file and still contains the literal `@env DATA_ROOT` line.

A required imported variable must exist. If it is absent, `validate`, `plan`, and `create` fail with a message naming the missing environment variable; yall-run does not substitute an empty string.

`@set` behavior is unchanged. `@set` and `@env` share the same placeholder namespace, so normal source order determines the value if the same name is deliberately declared more than once.

## Execution policy: `%`

`%` directives describe how a task should run rather than what data it consumes.

```text
heavy-analysis:
    %retry 2
    %cpus 4
    %memory 8GB
    %disk 10GB
    %time 2h
    ./Analyze input.root
```

Task-level resource values override campaign defaults. Condor maps CPU, memory, and disk requests directly. Slurm and PBS map CPU and memory requests. `%time` requests elapsed wall time for each scheduled task job: `2h`, `90m`, `1h30m`, or `02:00:00`, for example. It maps to Condor `+MaxRuntime` (a site convention supported by CERN), Slurm `--time`, or PBS `walltime`. The local backend records it without enforcing a timeout. Omitting `%time` leaves the scheduler default unchanged. See [Resources](RESOURCES.md) for duration syntax, scheduler rounding, retry semantics, and frozen-campaign behavior. Disk requests remain in provenance for Slurm and PBS because scratch resources are site-specific.

Campaign-level defaults can appear outside a task:

```text
backend condor
%cpus 1
%memory 2GB
%disk 2GB
%time 2h
```

Other campaign-level directives are `%getenv`, `%wrapper`, `%preflight`, and
`%postflight`.

### Host setup: `%preflight`

Put repeatable `%preflight` commands before the first task to perform lightweight
setup during `yall-run create`, without adding scheduler jobs:

```text
campaign analysis
backend condor
@env WORK

%preflight python3 check_inputs.py
%preflight mkdir -p "{WORK}/results"

analyze:
    @output result "{WORK}/results/summary.txt"
    python3 analyze.py @output.result
```

`validate` checks the declarations and `plan` shows the ordered commands without
running them. `plan --json` includes a separate `preflight` list; `plan --dot`
contains only graph tasks. `create` executes preflights in source order, on its
own host, with the Yallfile's directory as the working directory. They inherit
the creation process's environment, bypass `%wrapper`, and receive no stdin.
For a container workflow, this means setup runs outside the container.

Ordinary commands are argv arrays. `{NAME}` placeholders use resolved `@set`
and `@env` values; a substituted argument stays one argument even if it contains
spaces. For explicit Bash syntax, use `%preflight ! COMMAND`, with the same
textual substitution and shell-quoting responsibility as a task's `!` command.
Task references such as `@input.data`, `@outputs`, and `@each` bindings are not
available in preflights. Separate commands do not share shell variables or
changes of working directory.

Each command has `stdout.log`, `stderr.log`, and `result.json` under
`preflight/001/`, `preflight/002/`, and so on. Records include the resolved
command, working directory, hostname, timestamps, outcome, and exit code.
After each command finishes, its stdout and stderr are also sent to Yall's
stderr. Successful creation still prints only the campaign path to stdout,
so command substitution and `create | start` keep working.

Creation stops on the first failed command. Later commands do not run, no
campaign path is printed, and no launchable `campaign.json` is written. The
source archive and diagnostics remain at the path reported on stderr. An
interrupted creation also remains unlaunchable. Setup side effects are not
rolled back: inspect them before creating another campaign, and prefer
idempotent commands such as `mkdir -p` where appropriate.

Successful preflight results are frozen in `campaign.json`. `start`, task
retries, and `resume` never repeat them; `amend` rejects changes to their
commands or working directory. Recipes without `%preflight` keep their
existing behavior.

Existing Yallfiles need no migration. Before adding `%preflight` or `%postflight`
to a recipe, update Yall on the host running `create`; older versions reject
the new directives.

Use preflights for directory setup and inexpensive input checks. They run only
after parsing and task expansion, so they cannot create files needed for
file-pattern `@each` discovery in the same creation. Host checks do not prove
worker-node or container access. Leave substantial computation, including large
data merges, in ordinary tasks. Create output parent directories rather than
declared output files or output directories: the normal start-time output guard
still applies.

### Host completion: `%postflight`

Use `%postflight` for lightweight work that should happen after every graph task
has completed, such as checksum collection, provenance indexing, or a final
validation report:

```text
%postflight ! python3 collect_provenance.py "$YALL_CAMPAIGN_DIR"
```

Postflight commands are frozen at campaign creation and run on the host that
invokes `yall-run postflight campaigns/<campaign-id>`. They use the campaign
directory as their working directory. `YALL_CAMPAIGN_DIR` and
`YALL_CAMPAIGN_ID` are available to each command, and logs are recorded under
`postflight/`. For queued backends, run postflight after the scheduler reports
the campaign complete; it is not an additional batch task.

### Execution wrappers

Use `%wrapper PATH [ARG ...]` at campaign level when tasks need a site or
container launcher. It applies to **local, Condor, Slurm and PBS** execution:

```text
%wrapper /path/to/launcher --flag value --
```

The first token is the wrapper executable. Remaining tokens are fixed arguments,
placed **before the scientific payload**, not before Yall's Python worker:

```text
<archived-launcher> --flag value -- <scientific-command> <arguments...>
```

For a `!` shell task the payload is explicitly `/bin/sh -c <command-string>`.
Pipelines, redirects and variable expansion consequently run inside the wrapped
environment. A literal `--` is passed only when written; Yall never inserts one.

Quote literal paths or arguments containing spaces. `{name}` substitutions from
`@set` and `@env` apply to each already-parsed token, preserving argument boundaries
and empty strings. Missing values, empty executable paths and NULs are errors.
The wrapper path is relative to the Yallfile unless absolute; `~` in that path
is expanded at creation. Wrapper arguments are literal: `$HOME`, globs, redirects
and command substitutions are not evaluated. Use `@env` for imported values.
Arguments naming files are not automatically made absolute or archived.

At creation, Yall archives the wrapper in `environment/payload-wrapper<suffix>`
and freezes its arguments in `campaign.json` at `execution.wrapper`. The record
contains source path, archived path, size, SHA-256 and arguments. `start.json`
copies this policy. All backends use the same archived wrapper; scheduler
scripts launch only the host-side worker.

The worker launches the wrapper with the task's working directory and `YALL_*`
variables. The wrapper must preserve the directory (or map it deliberately),
forward the complete argument vector, wait for the payload, and propagate its
exit status. See [Execution wrappers](WRAPPERS.md) for the full contract, failure
reporting, filesystem requirements and provenance boundaries.

The existing EIC adapter remains usable without a syntax change:

```text
@env EIC_SHELL
%wrapper ./run-in-eic-shell.sh {EIC_SHELL}
```

It quotes the **payload** argv and pipes the command into `eic-shell`. Yall's
worker needs Python on the execution host, not inside the container. See
[`examples/eic-shell`](../examples/eic-shell/). Archiving the adapter does not
archive the launcher installation or container image it references.

This pre-beta change intentionally replaces worker-level wrapping. There is no
legacy wrapper mode or campaign migration; create fresh campaigns with this
version. A previously frozen worker/script is not upgraded by `resume`.

`%cwd` is task-local.

`%overwrite` is also task-local. It permits that task to run when one or more of its declared output paths already exist:

```text
analysis:
    %overwrite
    @output root results/run308.root
    ./Analyze ...
```

`%overwrite` never deletes, truncates, empties, or otherwise modifies an existing output itself. It only permits the task command to run. The command remains responsible for whatever replacement behavior it performs.

The backend is frozen into the campaign at `create` time. `yall-run create --backend local`, `--backend condor`, `--backend slurm`, or `--backend pbs` can deliberately override the Yallfile for a particular campaign.

`-j` and `%cpus` are deliberately different:

- `-j N` is local campaign concurrency: at most `N` dependency-ready tasks are active at once.
- `%cpus N` is a per-task resource request for queued execution.
- local yall currently records `%cpus` but does not use it as a local scheduling weight.

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

If either `results/run308.root` or `results/run308-plots` already exists, `yall-run start` refuses the campaign by default. The campaign remains unstarted, so the user can inspect or remove the conflicting product, or explicitly retry the start with overwrite permission.

There are two ways to permit replacement:

```text
analysis:
    %overwrite
    @output root results/run308.root
    ./Analyze ...
```

permits replacement for that task, while:

```bash
yall-run start --overwrite campaigns/<campaign-id>
```

permits pre-existing declared outputs for every task in that campaign start. The campaign-wide choice is recorded in `start.json`.

Neither form causes yall-run to remove existing data. They only disable the existence guard for the affected task or campaign start.

The worker repeats the output existence check immediately before each task command. This catches a product that appears after the campaign-wide start preflight but before that particular task becomes runnable. Such an attempt records failure kind `outputs_exist` and does not launch the command.

Two different expanded tasks may not declare the same output path. This is rejected while the Yallfile is validated, regardless of `%overwrite`, because one campaign product has one owning task.

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

## Pattern tasks with `@each`

`@each` creates a family of tasks by binding placeholders in the task name. Bindings can be discovered from matching files, listed explicitly for one placeholder, supplied as correlated rows for several placeholders, or drawn from a top-level named list/table. An explicit or named-source `@each` may bind only some of the task-name placeholders when compatible patterned parents supply the rest. All forms are expanded and frozen into ordinary task definitions when the campaign is created.

### Reuse a named list or table

```text
@table pairs ped run:
    296 298
    296 300

convert-{run}:
    @each run in pairs.ped pairs.run
    echo converting {run}

pedestal-{ped}: convert-{ped}
    @each ped in pairs.ped
    echo pedestal {ped}

calibrate-{ped}-{run}: pedestal-{ped} convert-{run}
    @each ped run in pairs
    echo calibrating {run} with {ped}
```

Declarations belong at the top level before the tasks. A table binds correlated
rows, not a Cartesian product. Column references such as `pairs.ped` provide
unique values in first-seen order, so a shared pedestal is processed once.
Multiple sources after `in` form a first-seen ordered union, not a product.
Each source must have the same width as the binding names. An independent
`@list` preserves its declared order and rejects duplicates.
The existing patterned-parent inheritance and campaign freezing rules apply.

See [Reusable parameter lists and tables](PARAMETERS.md) for quoting, validation,
static substitutions, and amendment semantics. In the non-colon `@each` form,
`in` introduces the named source; use `@each mode: in out` for literal values.

### Discover values from matching files

The existing file-pattern form maps a family of files into a family of tasks. Placeholders in braces are captured from the matching filename:

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

yall-run expands the rule to three ordinary tasks:

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

Each task receives one matched `raw` input and produces its own declared output. Plain placeholders currently capture arbitrary non-path text. Thus `data123a.root` also matches `data{run}.root`, binding `run=123a`. Numeric-only typed captures are not yet part of the Yallfile syntax.

The input set is discovered and frozen when the campaign is created. A file-pattern `@each` that matches nothing is an error.

### Use an explicit value list

When the scientific campaign defines the family independently of which files happen to be visible, list the placeholder values directly:

```text
convert-{run}:
    @each run 296 298 299 300 301 302 303 304 305 306 307 308 309 310
    @input raw /work/eic3/EPIC/TestBeam/LFHCAL/CERN/2026/2026_SPSH2/raw/Run{run}.h2g
    @output root work/converted/rawHGCROC_{run}.root
    ./Convert -i @input.raw -o @output.root
```

Here `run` names the placeholder in `convert-{run}`, and the remaining tokens are exactly the values to bind. No filesystem discovery is performed by the `@each` line. The values stay in the order written, and each expanded task gets its real inputs from the ordinary `@input` declarations.

For example, the rule above creates `convert-296`, `convert-298`, and so on even if other `Run*.h2g` files are also present in the shared directory. Conversely, merely adding another matching file to that directory does not add it to this campaign.

A single explicit value is valid. Explicit rows must be unique.

### Use correlated values for several placeholders

When several scientific values belong together, put the field names before `:` and provide the values row by row:

```text
pedestal-{ped}-{run}-{toa}:
    @each ped run toa: \
        296 298 1 \
        299 300 1 \
        301 302 1 \
        303 304 1 \
        328 329 2 \
        330 331 2
    @output pedestal work/pedestal/rawHGCROC_wPed_{ped}.root
    ./make-pedestal {ped} {run} {toa}
```

The field names before `:` define the row width. The example therefore produces exactly these bindings:

```text
ped=296 run=298 toa=1
ped=299 run=300 toa=1
ped=301 run=302 toa=1
ped=303 run=304 toa=1
ped=328 run=329 toa=2
ped=330 run=331 toa=2
```

Rows are correlated. Yall does **not** form a Cartesian product of pedestal, muon, and ToA values. The number of values after `:` must be an exact multiple of the number of field names. The field names must be unique placeholders from the task name, but they may be a subset when patterned parents supply the omitted placeholders. Duplicate rows are rejected clearly.

A patterned child inherits the complete row normally:

```text
transfer-{ped}-{run}-{toa}: pedestal-{ped}-{run}-{toa}
    @input pedestal work/pedestal/rawHGCROC_wPed_{ped}.root
    @input raw work/converted/rawHGCROC_{run}.root
    @input toa configs/ToAOffsets_{toa}.csv
    ./transfer {ped} {run} {toa}
```

Thus `transfer-299-300-1` depends on `pedestal-299-300-1` and receives `ped=299`, `run=300`, and `toa=1` together. Correlation is preserved through dependency propagation.

The colon is what makes the multi-field form unambiguous. Existing one-dimensional syntax remains unchanged:

```text
@each run 296 298 300
```

and existing file discovery remains unchanged:

```text
@each raw raw/Run{run}.h2g
```

After `yall-run create`, all forms have disappeared into the same concrete campaign model: `campaign.json` contains only the expanded task names, commands, inputs, outputs, dependencies, and execution policy.

### Bind part of a patterned task

An explicit or named-source `@each` can select one dimension while a patterned
parent supplies the remaining task-name placeholders:

```text
@table runs type run:
    pedestal 485
    muon     484
    muon     486

convert-{type}-{run}:
    @each type run in runs
    ./convert {type} {run}

{type}-{run}: convert-{type}-{run}
    @each type pedestal
    ./fit-pedestal {run}
```

The child binds `type=pedestal` explicitly. Its parent family is then filtered
to rows compatible with that binding, so the only inherited value is `run=485`
and the only child is `pedestal-485`, depending on `convert-pedestal-485`.
The same rule applies to named data, such as `@each type in selected_types`.
Rows are compatible when all placeholders shared with the explicit binding
have the same values; a parent need not contain fields that were already bound.

Explicit and named-source binding names must be a nonempty subset of the
placeholders in the task name. If fields are omitted, compatible patterned
parents must supply all of them. Each partial binding row must find a compatible
row in every patterned parent used as a provider; otherwise expansion fails
rather than creating an empty task family. If multiple patterned parents can
supply the omitted fields, their compatible binding sets must agree. Fully
specified `@each` declarations and duplicate-value or duplicate-row checks
retain their existing behavior.

Compatibility filtering also applies when the child binds all of its own
placeholders but fans in over additional parent placeholders:

```text
merge-{type}: convert-{type}-{run}
    @each type muon
    @input parts work/rawHGCROC_{run}.root
    hadd -f merged.root @input.parts
```

This creates one `merge-muon` task. It depends only on the matching `muon`
conversions, and `@input.parts` expands `{run}` from only those parent rows.
File-pattern discovery remains unchanged: its captured placeholders must
exactly match the task-name placeholders.

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

`examples/pi/Yallfile` is a complete map-reduce example. Eight `partial-{chunk}` tasks run the same Python worker against different range files, then one `sum` task fans in all eight outputs.

## Campaign records

When a campaign is created, the exact source Yallfile and the frozen expanded workflow are stored with the campaign:

```text
Yallfile
campaign.json
start.json
state/
    partial-000.json
    partial-001.json
    sum.json
```

The archived `Yallfile` is the exact input used at campaign creation. `campaign.json` records its original source path and SHA-256 alongside campaign identity, creation environment, named values imported with `@set` or `@env`, execution policy, task order, and the frozen definition of each task. Task definitions include dependencies, command, cwd, resources, inputs, outputs, and overwrite policy.

The files under `state/` are mutable bookkeeping only. They contain the current task state, attempt count, and, after execution, the most recent return code. Keeping these files small lets workers update state independently without duplicating the full task definition.

`start.json` records how the frozen campaign was actually launched, including whether the campaign-wide `--overwrite` permission was requested. A rejected output preflight does not create `start.json`.

## Portable attempt provenance

Before each task attempt begins, yall writes:

```text
<task>_attempt_001/provenance.json
```

This launch-provenance record contains the campaign identity, task and attempt identity, resolved inputs, declared outputs, command, cwd, resource requests, overwrite policy, host, Python version, and start time. It is written before the program starts and is not rewritten afterward.

The task process receives environment variables including:

```text
YALL_CAMPAIGN_ID
YALL_CAMPAIGN_NAME
YALL_CAMPAIGN_DIR
YALL_BACKEND
YALL_TASK
YALL_ATTEMPT
YALL_PROVENANCE
```

`YALL_PROVENANCE` points to that JSON file. Application-specific software may copy or embed it into its native output formats while yall-run remains format-agnostic.

`attempt.json` is completed after execution with the return code, finish time, timing, stdout/stderr paths, pre-launch output observations, and final observed output metadata. A worker-level task stopped by the output guard records failure kind `outputs_exist` and no command return code because the command was never launched.
