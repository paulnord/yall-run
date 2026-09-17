# Reusable parameter lists and tables

Declare reusable scientific configuration before the task definitions, then
reference it with `@each`. The lists are recipe data, not shell variables and
not code executed on a worker.

## Independent lists

```text
@list runs 296 298 299 300 303 304

convert-{run}:
    @each run in runs
    echo converting {run}
```

List names follow the same identifier rules as `@set`: letters, digits and
underscores, beginning with a letter or underscore. A list must contain at least
one nonempty value. Values remain strings, in declaration order: `00296` does
not become `296`. Duplicate values are rejected rather than silently producing
duplicate tasks. Use the usual backslash continuation for a long list.

Quoted values may contain spaces. For filenames, data references preserve the
filename as one argument:

```text
@list files "first sample.dat" "second sample.dat"

inspect-{file}:
    @each file in files
    @input raw {file}
    cat @input.raw
```

The named list itself performs no file discovery. Declared input paths retain
the usual input validation and glob rules.

## Correlated tables

```text
@table pairs ped run:
    296 298
    296 300
    303 304

prepare:
    echo preparing

convert-ped-{ped}: prepare
    @each ped in pairs.ped
    echo converting pedestal {ped}

convert-muon-{run}: prepare
    @each run in pairs.run
    echo converting muon {run}

calibrate-{ped}-{run}: convert-ped-{ped} convert-muon-{run}
    @each ped run in pairs
    echo calibrating {run} with pedestal {ped}

analyze-{ped}-{run}: calibrate-{ped}-{run}
    echo analyzing {run} with pedestal {ped}
```

Each indented logical line is one row and must have exactly as many values as
the header has columns. Blank lines and full-line `#` comments are allowed.
The next unindented declaration or task header ends the table. Column names
must be unique identifiers; a table must have at least one column and one row.
Duplicate complete rows are rejected, but the same value may appear in a column
in several distinct rows.

`@each ped run in pairs` binds rows together. It does not form a Cartesian
product. Binding names are positional: `@each p m in pairs` also works with a
task named `calibrate-{p}-{m}` and binds columns in their declared order.
The number of names must match the width of the source, and the names must match
the task-name placeholders just as for an explicit `@each`.

`pairs.ped` selects a column and removes repeated values, preserving the order
of their first occurrence. In this example it gives `296 303`, so there is only
one `convert-ped-296` task. `pairs.run` gives `298 300 304`.
Downstream patterned tasks inherit the bindings as usual; they do not need
another `@each` or another copy of the table.

## Scope, substitution, and errors

Put `@list` and `@table` at the top level before all tasks. Lists, tables,
`@set`, and `@env` cannot share a name. Scalar `@set` and `@env` behavior is
otherwise unchanged, including their existing source-order rules.

List values and table cells can use scalar `{NAME}` substitutions from `@set`
and `@env`. The parser resolves those values before expanding the task graph.
Unknown substitutions, unknown sets or columns, malformed rows, mismatched
binding counts, and duplicates after substitution are errors, including in
unused declarations. Lists and tables cannot reference one another or contain
expressions, nested collections, file imports, or implicit products.

Existing file-pattern and explicit-list/row forms of `@each` remain available.
In the non-colon form, `in` introduces a named source. To use the word `in` as
an explicit data value, write `@each mode: in out`.

## Frozen campaigns and amendments

Named data is expanded while loading the Yallfile. The campaign contains the
same concrete tasks, commands, dependencies, inputs and outputs that the
corresponding explicit `@each` definitions would produce. The original Yallfile,
including the declarations, is archived unchanged. No worker-side collection
lookup or new campaign schema is needed.

Editing a source list or table does not change an existing campaign. An
inline-to-named refactor that produces identical task definitions is not a
semantic amendment. Changes that add, remove, or rewire tasks still require a
new campaign under the existing amendment rules; named data does not bypass
those checks. Imported environment values retain the existing frozen comparison
behavior for amendments.

See the runnable [parameter-sets example](../examples/parameter-sets/README.md).
