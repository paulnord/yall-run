from __future__ import annotations

from dataclasses import dataclass, field
import glob
import os
from pathlib import Path
import re
import shlex
from typing import Dict, List, Mapping, Sequence, Tuple

from .model import CampaignSpec, CondorSpec, DEFAULT_STARTUP_RETRIES, ExecutionSpec, FileRef, ResourceSpec, TaskSpec, _validate_graph
from .walltime import parse_walltime

_FIELD_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_REF_TOKEN_RE = re.compile(r"^@(input|output)\.([A-Za-z_][A-Za-z0-9_-]*)$")
_SHELL_REF_RE = re.compile(
    r"@(inputs|outputs|input\.[A-Za-z_][A-Za-z0-9_-]*|output\.[A-Za-z_][A-Za-z0-9_-]*)"
)


@dataclass
class _RefTemplate:
    role: str
    paths: List[str]


@dataclass
class _EachTemplate:
    names: List[str]
    values: List[str]
    pattern: bool
    sources: Tuple[str, ...] = ()
    lineno: int = 0


@dataclass
class _Parameters:
    """Recipe-only data, lowered to existing explicit @each bindings."""

    lineno: int
    columns: List[str]  # Empty for a one-dimensional @list.
    rows: List[Tuple[str, ...]]


@dataclass
class _TaskTemplate:
    name: str
    parents: List[str]
    lineno: int
    inputs: List[_RefTemplate] = field(default_factory=list)
    outputs: List[_RefTemplate] = field(default_factory=list)
    each: _EachTemplate | None = None
    retries: int = 0
    startup_retries: int = DEFAULT_STARTUP_RETRIES
    cpus: int | None = None
    memory: str | None = None
    disk: str | None = None
    walltime_seconds: int | None = None
    cwd: str | None = None
    command: str | None = None
    shell: bool = False
    overwrite: bool = False


@dataclass(frozen=True)
class _Family:
    template: _TaskTemplate
    bindings: Tuple[Mapping[str, str], ...]


def _logical_lines(text: str) -> List[Tuple[int, str]]:
    result: List[Tuple[int, str]] = []
    pending: str | None = None
    pending_lineno = 0
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        if pending is not None:
            pending += " " + line.lstrip()
        else:
            pending = line
            pending_lineno = lineno
        if pending.rstrip().endswith("\\"):
            pending = pending.rstrip()[:-1].rstrip()
            continue
        result.append((pending_lineno, pending))
        pending = None
    if pending is not None:
        raise ValueError(f"line {pending_lineno}: trailing continuation")
    return result


def _fields(value: str) -> Tuple[str, ...]:
    return tuple(dict.fromkeys(_FIELD_RE.findall(value)))


def _format(value: str, binding: Mapping[str, str], context: str) -> str:
    missing = [name for name in _fields(value) if name not in binding]
    if missing:
        raise ValueError(f"{context}: no value for {{{missing[0]}}}")
    return _FIELD_RE.sub(lambda match: binding[match.group(1)], value)


def _pattern_regex(pattern: str) -> re.Pattern[str]:
    pieces: List[str] = []
    position = 0
    seen: set[str] = set()
    for match in _FIELD_RE.finditer(pattern):
        pieces.append(re.escape(pattern[position:match.start()]))
        name = match.group(1)
        if name in seen:
            pieces.append(f"(?P={name})")
        else:
            pieces.append(f"(?P<{name}>[^/]+)")
            seen.add(name)
        position = match.end()
    pieces.append(re.escape(pattern[position:]))
    return re.compile("^" + "".join(pieces) + "$")


def _each_bindings(template: _TaskTemplate) -> Tuple[Mapping[str, str], ...]:
    assert template.each is not None
    task_fields = set(_fields(template.name))
    if not task_fields:
        raise ValueError(f"line {template.lineno}: @each requires placeholders in task name")

    if template.each.pattern:
        pattern = template.each.values[0]
        pattern_fields = set(_fields(pattern))
        if pattern_fields != task_fields:
            raise ValueError(
                f"line {template.lineno}: @each placeholders must match task name placeholders"
            )
        wildcard = _FIELD_RE.sub("*", pattern)
        matcher = _pattern_regex(pattern)
        values: List[Mapping[str, str]] = []
        for matched in sorted(glob.glob(wildcard)):
            found = matcher.match(matched)
            if found:
                values.append(found.groupdict())
        if not values:
            raise ValueError(f"line {template.lineno}: @each matched no files: {pattern}")
        return tuple(values)

    names = template.each.names
    if len(set(names)) != len(names):
        raise ValueError(f"line {template.lineno}: explicit @each field names must be unique")
    if set(names) != task_fields:
        raise ValueError(
            f"line {template.lineno}: explicit @each names must match the task placeholders"
        )

    width = len(names)
    if len(template.each.values) % width:
        raise ValueError(
            f"line {template.lineno}: explicit @each has {len(template.each.values)} values "
            f"for {width} fields; value count must be an exact multiple of field count"
        )

    rows = [
        tuple(template.each.values[index:index + width])
        for index in range(0, len(template.each.values), width)
    ]
    if len(set(rows)) != len(rows):
        raise ValueError(f"line {template.lineno}: explicit @each rows must be unique")
    return tuple(dict(zip(names, row)) for row in rows)


def _compatible(candidate: Mapping[str, str], binding: Mapping[str, str]) -> bool:
    return all(
        candidate.get(key) == value
        for key, value in binding.items()
        if key in candidate
    )


def _parse_bool(value: str, lineno: int) -> bool:
    lowered = value.lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"line {lineno}: expected true/false, got {value!r}")


def _positive_int(value: str, lineno: int, name: str) -> int:
    try:
        result = int(value)
    except ValueError:
        raise ValueError(f"line {lineno}: %{name} requires an integer") from None
    if result < 1:
        raise ValueError(f"line {lineno}: %{name} must be positive")
    return result


def _walltime(value: str, lineno: int) -> int:
    try:
        return parse_walltime(value)
    except ValueError as exc:
        raise ValueError(f"line {lineno}: %time: {exc}") from None


def _valid_variable_name(name: str) -> bool:
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name))


def _replace_static(value: str, variables: Mapping[str, str]) -> str:
    return _FIELD_RE.sub(
        lambda match: variables.get(match.group(1), match.group(0)), value
    )


def _resolve_static_variables(variables: Mapping[str, str]) -> Dict[str, str]:
    """Resolve @set/@env references while preserving task placeholders."""
    resolved: Dict[str, str] = {}
    visiting: List[str] = []

    def resolve(name: str) -> str:
        if name in resolved:
            return resolved[name]
        if name in visiting:
            cycle = visiting[visiting.index(name):] + [name]
            raise ValueError(
                "static variable cycle: " + " -> ".join(cycle)
            )
        visiting.append(name)
        value = variables[name]

        def replace(match: re.Match[str]) -> str:
            other = match.group(1)
            return resolve(other) if other in variables else match.group(0)

        value = _FIELD_RE.sub(replace, value)
        visiting.pop()
        resolved[name] = value
        return value

    for name in variables:
        resolve(name)
    return resolved


def _apply_static_variables(
    tasks: Sequence[_TaskTemplate], variables: Mapping[str, str]
) -> None:
    for task in tasks:
        task.name = _replace_static(task.name, variables)
        task.parents = [_replace_static(parent, variables) for parent in task.parents]
        for ref in task.inputs:
            ref.paths = [_replace_static(path, variables) for path in ref.paths]
        for ref in task.outputs:
            ref.paths = [_replace_static(path, variables) for path in ref.paths]
        if task.each is not None:
            task.each.values = [
                _replace_static(value, variables) for value in task.each.values
            ]
        if task.cwd is not None:
            task.cwd = _replace_static(task.cwd, variables)
        if task.command is not None:
            task.command = _replace_static(task.command, variables)


def _explicit_each_parts(parts: List[str], lineno: int) -> Tuple[List[str], List[str]] | None:
    tokens = parts[1:]
    colon_index: int | None = None
    normalized: List[str] = []
    for token in tokens:
        if token == ":":
            if colon_index is not None:
                raise ValueError(f"line {lineno}: explicit @each may contain only one ':'")
            colon_index = len(normalized)
            continue
        if token.endswith(":") and _valid_variable_name(token[:-1]):
            if colon_index is not None:
                raise ValueError(f"line {lineno}: explicit @each may contain only one ':'")
            normalized.append(token[:-1])
            colon_index = len(normalized)
            continue
        normalized.append(token)

    if colon_index is None:
        return None
    names = normalized[:colon_index]
    values = normalized[colon_index:]
    if not names:
        raise ValueError(f"line {lineno}: explicit @each needs at least one field before ':'")
    if not values:
        raise ValueError(f"line {lineno}: explicit @each needs values after ':'")
    bad = [name for name in names if not _valid_variable_name(name)]
    if bad:
        raise ValueError(f"line {lineno}: invalid @each field name {bad[0]!r}")
    return names, values


def _parameter_tokens(text: str, lineno: int) -> List[str]:
    try:
        return shlex.split(text)
    except ValueError as exc:
        raise ValueError(f"line {lineno}: {exc}") from None


def _resolve_parameter_sets(
    tasks: Sequence[_TaskTemplate],
    parameters: Mapping[str, _Parameters],
    variables: Mapping[str, str],
) -> None:
    # Validate every declaration, even unused ones. Preserve declaration order
    # and string spelling (e.g. run 00296) rather than coercing or sorting data.
    for name, parameter in parameters.items():
        context = f"line {parameter.lineno}: parameter set {name!r}"
        if not parameter.rows:
            raise ValueError(f"{context} must not be empty")
        rows = [tuple(_format(value, variables, context) for value in row)
                for row in parameter.rows]
        if any(not value or any(c in value for c in "\0\r\n")
               for row in rows for value in row):
            raise ValueError(f"{context} contains an empty or invalid value")
        if len(set(rows)) != len(rows):
            raise ValueError(f"{context} rows must be unique")
        parameter.rows = rows

    for task in tasks:
        each = task.each
        if each is None or not each.sources:
            continue
        context = f"line {each.lineno}: @each"
        combined: Dict[Tuple[str, ...], None] = {}
        for source in each.sources:
            name, separator, column = source.partition(".")
            parameter = parameters.get(name)
            if parameter is None:
                raise ValueError(f"{context}: unknown parameter set {name!r}")
            if separator:
                if not parameter.columns:
                    raise ValueError(f"{context}: @list {name!r} has no columns")
                if column not in parameter.columns:
                    raise ValueError(f"{context}: unknown column {column!r} in table {name!r}")
                index = parameter.columns.index(column)
                rows = [(row[index],) for row in parameter.rows]
            else:
                rows = parameter.rows
            width = len(rows[0])
            if len(each.names) != width:
                raise ValueError(
                    f"{context}: {source!r} provides {width} field(s), "
                    f"but {len(each.names)} binding name(s) were supplied"
                )
            # Ordered union of complete rows, not a product or a zip. This
            # also deduplicates column projections, including across sources.
            combined.update(dict.fromkeys(rows))
        # Lower to the existing explicit binding/graph validation. No worker
        # change or runtime collection lookup is needed.
        each.values = [value for row in combined for value in row]


def _parse(text: str) -> Tuple[str, str, CondorSpec, ExecutionSpec, List[_TaskTemplate]]:
    campaign_name: str | None = None
    backend = "local"
    condor_cpus = 1
    condor_memory = "2GB"
    condor_disk = "2GB"
    condor_walltime: int | None = None
    condor_getenv = True
    payload_wrapper: str | None = None
    payload_wrapper_args: Tuple[str, ...] = ()
    wrapper_lineno = 0
    tasks: List[_TaskTemplate] = []
    variables: Dict[str, str] = {}
    parameters: Dict[str, _Parameters] = {}
    table: _Parameters | None = None
    current: _TaskTemplate | None = None

    for lineno, raw in _logical_lines(text):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indented = raw[:1].isspace()
        stripped = raw.strip()

        if not indented:
            current = None
            table = None
            if stripped.split()[0] in {"@list", "@table"}:
                directive = stripped.split()[0]
                if tasks:
                    raise ValueError(f"line {lineno}: {directive} must appear before tasks")
                if directive == "@table" and not stripped.endswith(":"):
                    raise ValueError(f"line {lineno}: @table header must end with ':'")
                header = stripped[:-1] if directive == "@table" else stripped
                parts = _parameter_tokens(header, lineno)
                if len(parts) < 3:
                    raise ValueError(f"line {lineno}: {directive} needs a name and "
                                     + ("columns" if directive == "@table" else "values"))
                name = parts[1]
                if not _valid_variable_name(name):
                    raise ValueError(f"line {lineno}: invalid parameter set name {name!r}")
                if name in parameters or name in variables:
                    raise ValueError(f"line {lineno}: duplicate parameter name {name!r}")
                columns = parts[2:] if directive == "@table" else []
                if any(not _valid_variable_name(c) for c in columns):
                    raise ValueError(f"line {lineno}: invalid @table column name")
                if len(set(columns)) != len(columns):
                    raise ValueError(f"line {lineno}: @table column names must be unique")
                parameter = _Parameters(lineno, columns,
                                        [] if columns else [(v,) for v in parts[2:]])
                parameters[name] = parameter
                if columns:
                    table = parameter
                continue
            if stripped.startswith("campaign "):
                campaign_name = stripped[len("campaign "):].strip()
                if not campaign_name:
                    raise ValueError(f"line {lineno}: campaign needs a name")
                continue
            if stripped.startswith("backend "):
                backend = stripped[len("backend "):].strip().lower()
                if backend not in {"local", "condor", "slurm", "pbs"}:
                    raise ValueError(
                        f"line {lineno}: backend must be local, condor, slurm, or pbs"
                    )
                continue
            if stripped.startswith("@set "):
                parts = shlex.split(stripped)
                if len(parts) != 3:
                    raise ValueError(f"line {lineno}: @set needs a name and value")
                name = parts[1]
                if not _valid_variable_name(name):
                    raise ValueError(f"line {lineno}: invalid @set name {name!r}")
                if name in parameters:
                    raise ValueError(f"line {lineno}: duplicate parameter name {name!r}")
                variables[name] = parts[2]
                continue
            if stripped.startswith("@env "):
                parts = shlex.split(stripped)
                if len(parts) != 2:
                    raise ValueError(f"line {lineno}: @env needs exactly one variable name")
                name = parts[1]
                if not _valid_variable_name(name):
                    raise ValueError(f"line {lineno}: invalid @env name {name!r}")
                if name in parameters:
                    raise ValueError(f"line {lineno}: duplicate parameter name {name!r}")
                if name not in os.environ:
                    raise ValueError(
                        f"line {lineno}: required environment variable {name!r} is not set"
                    )
                variables[name] = os.environ[name]
                continue
            if stripped.startswith("%"):
                parts = shlex.split(stripped)
                directive = parts[0][1:]
                values = parts[1:]
                if directive == "cpus" and len(values) == 1:
                    condor_cpus = _positive_int(values[0], lineno, directive)
                elif directive == "memory" and len(values) == 1:
                    condor_memory = values[0]
                elif directive == "disk" and len(values) == 1:
                    condor_disk = values[0]
                elif directive == "time" and len(values) == 1:
                    condor_walltime = _walltime(values[0], lineno)
                elif directive == "getenv" and len(values) == 1:
                    condor_getenv = _parse_bool(values[0], lineno)
                elif directive == "wrapper":
                    if not values:
                        raise ValueError(f"line {lineno}: %wrapper needs an executable path")
                    payload_wrapper = values[0]
                    payload_wrapper_args = tuple(values[1:])
                    wrapper_lineno = lineno
                else:
                    raise ValueError(
                        f"line {lineno}: unknown or malformed campaign directive %{directive}"
                    )
                continue
            if ":" in stripped:
                name, parent_text = stripped.split(":", 1)
                name = name.strip()
                if not name or any(ch.isspace() for ch in name):
                    raise ValueError(f"line {lineno}: invalid task name {name!r}")
                parents = shlex.split(parent_text.strip()) if parent_text.strip() else []
                current = _TaskTemplate(name=name, parents=parents, lineno=lineno)
                tasks.append(current)
                continue
            raise ValueError(
                f"line {lineno}: expected campaign, backend, directive, or task header"
            )

        if table is not None:
            row = tuple(_parameter_tokens(stripped, lineno))
            if len(row) != len(table.columns):
                raise ValueError(
                    f"line {lineno}: @table row needs {len(table.columns)} values, "
                    f"got {len(row)}"
                )
            table.rows.append(row)
            continue

        if current is None:
            raise ValueError(f"line {lineno}: indented line outside a task")

        if stripped.startswith("@"):
            parts = shlex.split(stripped)
            directive = parts[0][1:]
            if directive in {"input", "output"}:
                if len(parts) < 3:
                    raise ValueError(
                        f"line {lineno}: @{directive} needs a role and path"
                    )
                ref = _RefTemplate(role=parts[1], paths=parts[2:])
                if directive == "input":
                    current.inputs.append(ref)
                else:
                    current.outputs.append(ref)
                continue
            if directive == "each":
                if len(parts) < 3:
                    raise ValueError(f"line {lineno}: @each needs fields and values or a path")
                if current.each is not None:
                    raise ValueError(
                        f"line {lineno}: only one @each is supported per task"
                    )
                explicit = _explicit_each_parts(parts, lineno)
                if explicit is not None:
                    names, values = explicit
                    current.each = _EachTemplate(names=names, values=values, pattern=False)
                    continue
                if "in" in parts[2:]:
                    split = len(parts) - 2 if parts[-2] == "in" else parts.index("in", 2)
                    # Preserve identifiers named "in": the last known source
                    # fixes the binding width and therefore the separator.
                    last_name, dot, _ = parts[-1].partition(".")
                    last = parameters.get(last_name)
                    if last is not None:
                        width = 1 if dot or not last.columns else len(last.columns)
                        if width + 1 < len(parts) and parts[width + 1] == "in":
                            split = width + 1
                    names, sources = parts[1:split], parts[split + 1:]
                    if not sources:
                        raise ValueError(f"line {lineno}: @each ... in needs at least one source")
                    source_parts = [source.split(".") for source in sources]
                    if (any(not _valid_variable_name(n) for n in names)
                            or any(len(items) > 2 or any(not _valid_variable_name(n) for n in items)
                                   for items in source_parts)):
                        raise ValueError(f"line {lineno}: invalid named @each binding or source")
                    current.each = _EachTemplate(names, [], False, tuple(sources), lineno)
                    continue
                values = parts[2:]
                current.each = _EachTemplate(
                    names=[parts[1]],
                    values=values,
                    pattern=len(values) == 1 and bool(_fields(values[0])),
                )
                continue
            raise ValueError(f"line {lineno}: unknown data directive @{directive}")

        if stripped.startswith("%"):
            parts = shlex.split(stripped)
            directive = parts[0][1:]
            values = parts[1:]
            if directive == "retry" and len(values) == 1:
                try:
                    current.retries = int(values[0])
                except ValueError:
                    raise ValueError(
                        f"line {lineno}: %retry requires an integer"
                    ) from None
                if current.retries < 0:
                    raise ValueError(f"line {lineno}: %retry may not be negative")
            elif directive == "startup-retry" and len(values) == 1:
                try:
                    current.startup_retries = int(values[0])
                except ValueError:
                    raise ValueError(
                        f"line {lineno}: %startup-retry requires an integer"
                    ) from None
                if current.startup_retries < 0:
                    raise ValueError(
                        f"line {lineno}: %startup-retry may not be negative"
                    )
            elif directive == "cpus" and len(values) == 1:
                current.cpus = _positive_int(values[0], lineno, directive)
            elif directive == "memory" and len(values) == 1:
                current.memory = values[0]
            elif directive == "disk" and len(values) == 1:
                current.disk = values[0]
            elif directive == "time" and len(values) == 1:
                current.walltime_seconds = _walltime(values[0], lineno)
            elif directive == "cwd" and len(values) == 1:
                current.cwd = values[0]
            elif directive == "overwrite" and not values:
                current.overwrite = True
            else:
                raise ValueError(
                    f"line {lineno}: unknown or malformed task directive %{directive}"
                )
            continue

        if current.command is not None:
            raise ValueError(
                f"line {lineno}: task {current.name!r} already has a command; "
                "use \\ for continuation"
            )
        if stripped.startswith("!"):
            current.shell = True
            current.command = stripped[1:].lstrip()
        else:
            current.command = stripped

    if not campaign_name:
        raise ValueError("campaign NAME is required")
    if not tasks:
        raise ValueError("at least one task is required")
    for task in tasks:
        if not task.command:
            raise ValueError(f"line {task.lineno}: task {task.name!r} needs a command")

    variables = _resolve_static_variables(variables)
    _apply_static_variables(tasks, variables)
    _resolve_parameter_sets(tasks, parameters, variables)

    if payload_wrapper is not None:
        # Split before substitution: an imported path or argument containing
        # spaces/quotes must remain one token, not become shell syntax.
        context = f"line {wrapper_lineno}: %wrapper"
        wrapper_tokens = tuple(
            _format(token, variables, context)
            for token in (payload_wrapper, *payload_wrapper_args)
        )
        if not wrapper_tokens[0].strip():
            raise ValueError(f"{context} needs a nonempty executable path")
        if any("\0" in token for token in wrapper_tokens):
            raise ValueError(f"{context} may not contain NUL characters")
        payload_wrapper, *arguments = wrapper_tokens
        payload_wrapper_args = tuple(arguments)

    condor = CondorSpec(
        request_cpus=condor_cpus,
        request_memory=condor_memory,
        request_disk=condor_disk,
        request_walltime_seconds=condor_walltime,
        getenv=condor_getenv,
    )
    execution = ExecutionSpec(wrapper=payload_wrapper, wrapper_args=payload_wrapper_args)
    return campaign_name, backend, condor, execution, tasks


def _family_bindings(
    template: _TaskTemplate,
    template_map: Mapping[str, _TaskTemplate],
    cache: Dict[str, _Family],
    visiting: set[str],
) -> _Family:
    if template.name in cache:
        return cache[template.name]
    if template.name in visiting:
        raise ValueError(f"pattern dependency cycle involving {template.name!r}")
    visiting.add(template.name)
    wanted = set(_fields(template.name))

    if template.each is not None:
        bindings = _each_bindings(template)
    elif not wanted:
        bindings = ({},)
    else:
        candidate_sets: List[Tuple[Mapping[str, str], ...]] = []
        for parent_name in template.parents:
            parent = template_map.get(parent_name)
            if parent is None:
                continue
            parent_fields = set(_fields(parent.name))
            if not wanted.issubset(parent_fields):
                continue
            family = _family_bindings(parent, template_map, cache, visiting)
            projected: List[Mapping[str, str]] = []
            seen: set[Tuple[Tuple[str, str], ...]] = set()
            for binding in family.bindings:
                item = {key: binding[key] for key in wanted}
                marker = tuple(sorted(item.items()))
                if marker not in seen:
                    projected.append(item)
                    seen.add(marker)
            candidate_sets.append(tuple(projected))
        if not candidate_sets:
            raise ValueError(
                f"line {template.lineno}: patterned task {template.name!r} needs @each "
                "or a patterned parent that supplies its placeholders"
            )
        first_markers = {
            tuple(sorted(item.items())) for item in candidate_sets[0]
        }
        for other in candidate_sets[1:]:
            if {tuple(sorted(item.items())) for item in other} != first_markers:
                raise ValueError(
                    f"line {template.lineno}: patterned parents disagree on values "
                    f"for {template.name!r}"
                )
        bindings = candidate_sets[0]

    family = _Family(template=template, bindings=tuple(bindings))
    cache[template.name] = family
    visiting.remove(template.name)
    return family


def _matching_bindings(
    parent_template: str,
    binding: Mapping[str, str],
    families: Mapping[str, _Family],
) -> Tuple[Mapping[str, str], ...]:
    family = families.get(parent_template)
    if family is None:
        return ()
    return tuple(
        item for item in family.bindings if _compatible(item, binding)
    )


def _expand_parent(
    parent: str,
    binding: Mapping[str, str],
    families: Mapping[str, _Family],
) -> List[str]:
    missing = [name for name in _fields(parent) if name not in binding]
    if not missing:
        return [_format(parent, binding, "parent")]
    candidates = _matching_bindings(parent, binding, families)
    if not candidates:
        raise ValueError(f"cannot expand patterned parent {parent!r}")
    return [_format(parent, candidate, "parent") for candidate in candidates]


def _bindings_for_unresolved_ref(
    path: str,
    binding: Mapping[str, str],
    parent_templates: Sequence[str],
    families: Mapping[str, _Family],
) -> Tuple[Mapping[str, str], ...]:
    unresolved = set(_fields(path)) - set(binding)
    providers: List[Tuple[Mapping[str, str], ...]] = []
    for parent in parent_templates:
        family = families.get(parent)
        if family is None:
            continue
        family_fields = set(_fields(parent))
        if unresolved.issubset(family_fields):
            providers.append(_matching_bindings(parent, binding, families))
    if not providers:
        raise ValueError(f"cannot expand placeholders in path {path!r}")
    first = providers[0]
    first_markers = {tuple(sorted(item.items())) for item in first}
    for other in providers[1:]:
        if {tuple(sorted(item.items())) for item in other} != first_markers:
            raise ValueError(
                f"patterned parents disagree while expanding path {path!r}"
            )
    return first


def _expand_path_template(
    path: str,
    binding: Mapping[str, str],
    parent_templates: Sequence[str],
    families: Mapping[str, _Family],
    is_input: bool,
) -> List[str]:
    unresolved = set(_fields(path)) - set(binding)
    if unresolved:
        values = [
            _format(path, {**candidate, **binding}, f"path {path!r}")
            for candidate in _bindings_for_unresolved_ref(
                path, binding, parent_templates, families
            )
        ]
    else:
        values = [_format(path, binding, f"path {path!r}")]

    result: List[str] = []
    for value in values:
        if is_input and any(ch in value for ch in "*?["):
            matches = sorted(glob.glob(value))
            if not matches:
                raise ValueError(f"input pattern matched no files: {value}")
            result.extend(matches)
        else:
            result.append(value)
    return result


def _refs_by_role(refs: Sequence[FileRef], role: str) -> List[str]:
    return [ref.path for ref in refs if ref.role == role]


def _expand_command_argv(
    text: str, inputs: Sequence[FileRef], outputs: Sequence[FileRef]
) -> Tuple[str, ...]:
    result: List[str] = []
    for token in shlex.split(text):
        if token == "@inputs":
            result.extend(ref.path for ref in inputs)
            continue
        if token == "@outputs":
            result.extend(ref.path for ref in outputs)
            continue
        match = _REF_TOKEN_RE.match(token)
        if match:
            kind, role = match.groups()
            refs = inputs if kind == "input" else outputs
            paths = _refs_by_role(refs, role)
            if not paths:
                raise ValueError(
                    f"command references @{kind}.{role}, but no such {kind} is declared"
                )
            result.extend(paths)
            continue
        result.append(token)
    if not result:
        raise ValueError("empty command")
    return tuple(result)


def _expand_command_shell(
    text: str, inputs: Sequence[FileRef], outputs: Sequence[FileRef]
) -> str:
    def replace(match: re.Match[str]) -> str:
        token = match.group(1)
        if token == "inputs":
            paths = [ref.path for ref in inputs]
        elif token == "outputs":
            paths = [ref.path for ref in outputs]
        else:
            kind, role = token.split(".", 1)
            refs = inputs if kind == "input" else outputs
            paths = _refs_by_role(refs, role)
        if not paths:
            raise ValueError(
                f"command references @{token}, but no such data is declared"
            )
        return " ".join(shlex.quote(path) for path in paths)

    return _SHELL_REF_RE.sub(replace, text)


def _instantiate(
    template: _TaskTemplate,
    binding: Mapping[str, str],
    families: Mapping[str, _Family],
) -> TaskSpec:
    name = _format(template.name, binding, "task name")
    parents: List[str] = []
    for parent in template.parents:
        parents.extend(_expand_parent(parent, binding, families))

    inputs: List[FileRef] = []
    if template.each is not None and template.each.pattern:
        each_path = _format(template.each.values[0], binding, "@each path")
        inputs.append(FileRef(path=each_path, role=template.each.names[0]))
    for ref in template.inputs:
        for path in ref.paths:
            for expanded in _expand_path_template(
                path, binding, template.parents, families, True
            ):
                inputs.append(FileRef(path=expanded, role=ref.role))

    outputs: List[FileRef] = []
    for ref in template.outputs:
        for path in ref.paths:
            for expanded in _expand_path_template(
                path, binding, template.parents, families, False
            ):
                outputs.append(FileRef(path=expanded, role=ref.role))

    assert template.command is not None
    command_text = _format(template.command, binding, f"command for {name!r}")
    if template.shell:
        command = _expand_command_shell(command_text, inputs, outputs)
    else:
        command = _expand_command_argv(command_text, inputs, outputs)

    cwd = _format(template.cwd, binding, "%cwd") if template.cwd else None
    return TaskSpec(
        name=name,
        command=command,
        cwd=cwd,
        parents=tuple(dict.fromkeys(parents)),
        retries=template.retries,
        startup_retries=template.startup_retries,
        inputs=tuple(inputs),
        outputs=tuple(outputs),
        resources=ResourceSpec(
            cpus=template.cpus,
            memory=template.memory,
            disk=template.disk,
            walltime_seconds=template.walltime_seconds,
        ),
        overwrite=template.overwrite,
    )


def load_yall_spec(source: Path) -> CampaignSpec:
    campaign_name, backend, condor, execution, templates = _parse(source.read_text())
    template_map: Dict[str, _TaskTemplate] = {}
    for template in templates:
        if template.name in template_map:
            raise ValueError(f"duplicate task template: {template.name}")
        template_map[template.name] = template

    families: Dict[str, _Family] = {}
    for template in templates:
        _family_bindings(template, template_map, families, set())

    tasks: List[TaskSpec] = []
    for template in templates:
        for binding in families[template.name].bindings:
            tasks.append(_instantiate(template, binding, families))

    _validate_graph(tasks, source.parent)
    return CampaignSpec(
        name=campaign_name,
        tasks=tuple(tasks),
        source=source,
        backend=backend,
        condor=condor,
        execution=execution,
    )
