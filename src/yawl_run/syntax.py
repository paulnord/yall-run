from __future__ import annotations

from dataclasses import dataclass, field
import glob
from pathlib import Path
import re
import shlex
from typing import Dict, List, Mapping, Sequence, Tuple

from .model import CampaignSpec, CondorSpec, FileRef, ResourceSpec, TaskSpec, _validate_graph

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
class _TaskTemplate:
    name: str
    parents: List[str]
    lineno: int
    inputs: List[_RefTemplate] = field(default_factory=list)
    outputs: List[_RefTemplate] = field(default_factory=list)
    each: _RefTemplate | None = None
    retries: int = 0
    cpus: int | None = None
    memory: str | None = None
    disk: str | None = None
    cwd: str | None = None
    command: str | None = None
    shell: bool = False


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
    if len(template.each.paths) != 1:
        raise ValueError(f"line {template.lineno}: @each takes exactly one path pattern")
    pattern = template.each.paths[0]
    pattern_fields = set(_fields(pattern))
    name_fields = set(_fields(template.name))
    if not name_fields:
        raise ValueError(f"line {template.lineno}: @each requires placeholders in task name")
    if pattern_fields != name_fields:
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


def _replace_static(value: str, variables: Mapping[str, str]) -> str:
    return _FIELD_RE.sub(
        lambda match: variables.get(match.group(1), match.group(0)), value
    )


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
            task.each.paths = [_replace_static(path, variables) for path in task.each.paths]
        if task.cwd is not None:
            task.cwd = _replace_static(task.cwd, variables)
        if task.command is not None:
            task.command = _replace_static(task.command, variables)


def _parse(text: str) -> Tuple[str, str, CondorSpec, List[_TaskTemplate]]:
    campaign_name: str | None = None
    backend = "local"
    condor_cpus = 1
    condor_memory = "2GB"
    condor_disk = "2GB"
    condor_getenv = True
    condor_wrapper: str | None = None
    tasks: List[_TaskTemplate] = []
    variables: Dict[str, str] = {}
    current: _TaskTemplate | None = None

    for lineno, raw in _logical_lines(text):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indented = raw[:1].isspace()
        stripped = raw.strip()

        if not indented:
            current = None
            if stripped.startswith("campaign "):
                campaign_name = stripped[len("campaign "):].strip()
                if not campaign_name:
                    raise ValueError(f"line {lineno}: campaign needs a name")
                continue
            if stripped.startswith("backend "):
                backend = stripped[len("backend "):].strip().lower()
                if backend not in {"local", "condor"}:
                    raise ValueError(f"line {lineno}: backend must be local or condor")
                continue
            if stripped.startswith("@set "):
                parts = shlex.split(stripped)
                if len(parts) != 3:
                    raise ValueError(f"line {lineno}: @set needs a name and value")
                name = parts[1]
                if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
                    raise ValueError(f"line {lineno}: invalid @set name {name!r}")
                variables[name] = parts[2]
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
                elif directive == "getenv" and len(values) == 1:
                    condor_getenv = _parse_bool(values[0], lineno)
                elif directive == "wrapper" and len(values) == 1:
                    condor_wrapper = values[0]
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

        if current is None:
            raise ValueError(f"line {lineno}: indented line outside a task")

        if stripped.startswith("@"):
            parts = shlex.split(stripped)
            directive = parts[0][1:]
            if directive in {"input", "output", "each"}:
                if len(parts) < 3:
                    raise ValueError(
                        f"line {lineno}: @{directive} needs a role and path"
                    )
                ref = _RefTemplate(role=parts[1], paths=parts[2:])
                if directive == "input":
                    current.inputs.append(ref)
                elif directive == "output":
                    current.outputs.append(ref)
                else:
                    if current.each is not None:
                        raise ValueError(
                            f"line {lineno}: only one @each is supported per task"
                        )
                    current.each = ref
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
            elif directive == "cpus" and len(values) == 1:
                current.cpus = _positive_int(values[0], lineno, directive)
            elif directive == "memory" and len(values) == 1:
                current.memory = values[0]
            elif directive == "disk" and len(values) == 1:
                current.disk = values[0]
            elif directive == "cwd" and len(values) == 1:
                current.cwd = values[0]
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

    _apply_static_variables(tasks, variables)

    condor = CondorSpec(
        request_cpus=condor_cpus,
        request_memory=condor_memory,
        request_disk=condor_disk,
        getenv=condor_getenv,
        wrapper=condor_wrapper,
    )
    return campaign_name, backend, condor, tasks


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
    if template.each is not None:
        each_path = _format(template.each.paths[0], binding, "@each path")
        inputs.append(FileRef(path=each_path, role=template.each.role))
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
        inputs=tuple(inputs),
        outputs=tuple(outputs),
        resources=ResourceSpec(
            cpus=template.cpus,
            memory=template.memory,
            disk=template.disk,
        ),
    )


def load_yawl_spec(source: Path) -> CampaignSpec:
    campaign_name, backend, condor, templates = _parse(source.read_text())
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

    _validate_graph(tasks)
    return CampaignSpec(
        name=campaign_name,
        tasks=tuple(tasks),
        source=source,
        backend=backend,
        condor=condor,
    )
