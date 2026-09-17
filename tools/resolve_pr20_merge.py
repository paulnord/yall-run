from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text()
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one occurrence, found {count}: {old!r}")
    p.write_text(text.replace(old, new, 1))


# model.py and README auto-merge cleanly. Guard the important main-side pieces.
model = Path("src/yall_run/model.py").read_text()
assert "DEFAULT_STARTUP_RETRIES = 2" in model
assert "startup_retries: int = DEFAULT_STARTUP_RETRIES" in model
assert "- [Retries](docs/RETRIES.md)" in Path("README.md").read_text()

# syntax.py: keep PR #20's generic ExecutionSpec and add PR #21 startup retry syntax.
replace_once(
    "src/yall_run/syntax.py",
    "from .model import CampaignSpec, CondorSpec, ExecutionSpec, FileRef, ResourceSpec, TaskSpec, _validate_graph\n",
    "from .model import CampaignSpec, CondorSpec, DEFAULT_STARTUP_RETRIES, ExecutionSpec, FileRef, ResourceSpec, TaskSpec, _validate_graph\n",
)
replace_once(
    "src/yall_run/syntax.py",
    "    retries: int = 0\n    cpus: int | None = None\n",
    "    retries: int = 0\n    startup_retries: int = DEFAULT_STARTUP_RETRIES\n    cpus: int | None = None\n",
)
replace_once(
    "src/yall_run/syntax.py",
    '                if current.retries < 0:\n                    raise ValueError(f"line {lineno}: %retry may not be negative")\n            elif directive == "cpus" and len(values) == 1:\n',
    '                if current.retries < 0:\n                    raise ValueError(f"line {lineno}: %retry may not be negative")\n            elif directive == "startup-retry" and len(values) == 1:\n                try:\n                    current.startup_retries = int(values[0])\n                except ValueError:\n                    raise ValueError(\n                        f"line {lineno}: %startup-retry requires an integer"\n                    ) from None\n                if current.startup_retries < 0:\n                    raise ValueError(\n                        f"line {lineno}: %startup-retry may not be negative"\n                    )\n            elif directive == "cpus" and len(values) == 1:\n',
)
replace_once(
    "src/yall_run/syntax.py",
    "        retries=template.retries,\n        inputs=tuple(inputs),\n",
    "        retries=template.retries,\n        startup_retries=template.startup_retries,\n        inputs=tuple(inputs),\n",
)

# Condor keeps the Python worker on the host. The scheduler-facing launcher
# classifies wrapper startup failures vs failures after payload entry.
replace_once(
    "src/yall_run/condor_backend.py",
    "from dataclasses import asdict\nimport json\n",
    "from dataclasses import asdict\nimport hashlib\nimport json\n",
)
replace_once(
    "src/yall_run/condor_backend.py",
    "import re\nimport subprocess\n",
    "import re\nimport shlex\nimport subprocess\n",
)
replace_once(
    "src/yall_run/condor_backend.py",
    '_CLUSTER_RE = re.compile(r"cluster\\s+(\\d+)", re.IGNORECASE)\n_STATUS_NAMES = {\n',
    '_CLUSTER_RE = re.compile(r"cluster\\s+(\\d+)", re.IGNORECASE)\n_STARTUP_FAILURE_EXIT = 100\n_PAYLOAD_FAILURE_EXIT = 101\n_STATUS_NAMES = {\n',
)
replace_once(
    "src/yall_run/condor_backend.py",
    "def render_condor(spec: CampaignSpec, root: str | Path) -> Path:\n",
    'def _startup_marker(campaign_dir: Path, task_name: str) -> Path:\n    marker_id = hashlib.sha256(task_name.encode("utf-8")).hexdigest()\n    return campaign_dir / "condor" / "startup" / f"{marker_id}.started"\n\n\ndef render_condor(spec: CampaignSpec, root: str | Path) -> Path:\n',
)
replace_once(
    "src/yall_run/condor_backend.py",
    '    logs_dir = condor_dir / "logs"\n    logs_dir.mkdir()\n\n    worker_source = ',
    '    logs_dir = condor_dir / "logs"\n    logs_dir.mkdir()\n    startup_dir = condor_dir / "startup"\n    startup_dir.mkdir()\n\n    worker_source = ',
)
replace_once(
    "src/yall_run/condor_backend.py",
    '        node_script = condor_dir / f"{node}.sh"\n        command = worker_command(worker, campaign_dir, task.name)\n        node_script.write_text(\n            "#!/bin/bash\\n"\n            "set -e\\n"\n            f"exec {command}\\n"\n        )\n',
    '        marker = _startup_marker(campaign_dir, task.name)\n        command = worker_command(worker, campaign_dir, task.name)\n        node_script = condor_dir / f"{node}.sh"\n        marker_word = shlex.quote(str(marker))\n        node_script.write_text(\n            "#!/bin/bash\\n"\n            "set -u\\n"\n            f"marker={marker_word}\\n"\n            "if ! rm -f \\"$marker\\"; then\\n"\n            "    echo \\"yall: could not clear startup marker: $marker\\" >&2\\n"\n            f"    exit {_STARTUP_FAILURE_EXIT}\\n"\n            "fi\\n"\n            "rc=0\\n"\n            f"if {command}; then\\n"\n            "    rc=0\\n"\n            "else\\n"\n            "    rc=$?\\n"\n            "fi\\n"\n            "if [ ! -e \\"$marker\\" ]; then\\n"\n            "    echo \\"yall: startup failed before payload marker (exit=$rc)\\" >&2\\n"\n            f"    exit {_STARTUP_FAILURE_EXIT}\\n"\n            "fi\\n"\n            "if [ \\"$rc\\" -ne 0 ]; then\\n"\n            "    echo \\"yall: payload failed after startup (exit=$rc)\\" >&2\\n"\n            f"    exit {_PAYLOAD_FAILURE_EXIT}\\n"\n            "fi\\n"\n            "exit 0\\n"\n        )\n',
)
replace_once(
    "src/yall_run/condor_backend.py",
    '        time_line = f"+MaxRuntime = {walltime}\\n" if walltime is not None else ""\n\n        submit = ',
    '        time_line = f"+MaxRuntime = {walltime}\\n" if walltime is not None else ""\n        startup_retry_lines = ""\n        if task.startup_retries:\n            startup_retry_lines = (\n                f"max_retries = {task.startup_retries}\\n"\n                f"retry_until = ExitCode =!= {_STARTUP_FAILURE_EXIT}\\n"\n                \'requirements = (Machine =!= split(LastRemoteHost, "@")[1])\\n\'\n            )\n\n        submit = ',
)
replace_once(
    "src/yall_run/condor_backend.py",
    '            f"{time_line}"\n            f"getenv = ',
    '            f"{time_line}"\n            f"{startup_retry_lines}"\n            f"getenv = ',
)
replace_once(
    "src/yall_run/condor_backend.py",
    '        if task.retries:\n            dag_lines.append(f"RETRY {node} {task.retries}")\n',
    '        if task.retries:\n            dag_lines.append(f"RETRY {node} {task.retries} UNLESS-EXIT {_STARTUP_FAILURE_EXIT}")\n',
)

# worker.py auto-merges, but PR #21 originally marked startup at worker entry.
# Under PR #20 the worker is host-native, so only wrapped payloads need an
# inside-wrapper marker trampoline. No-wrapper tasks keep their original argv
# and launch_failed behavior, with the marker touched immediately before launch.
replace_once(
    "src/yall_run/worker.py",
    'def _payload_command(command: str | list[str], wrapper: dict[str, Any] | None) -> list[str]:\n    payload = ["/bin/sh", "-c", command] if isinstance(command, str) else list(command)\n    if wrapper is not None:\n        return [str(wrapper["path"]), *wrapper.get("args", []), *payload]\n    return payload\n',
    'def _payload_command(command: str | list[str], wrapper: dict[str, Any] | None, startup_marker: Path | None = None) -> list[str]:\n    payload = ["/bin/sh", "-c", command] if isinstance(command, str) else list(command)\n    if startup_marker is not None and wrapper is not None:\n        entry = \'marker=$1; shift; : > "$marker" || exit 125; exec "$@"\'\n        payload = ["/bin/sh", "-c", entry, "yall-payload-start", str(startup_marker), *payload]\n    if wrapper is not None:\n        return [str(wrapper["path"]), *wrapper.get("args", []), *payload]\n    return payload\n',
)
old_marker = '''def _startup_marker(campaign_dir: str | Path, task_name: str) -> Path | None:\n    campaign = Path(campaign_dir).expanduser()\n    if not campaign.is_absolute():\n        campaign = Path.cwd() / campaign\n    marker_dir = campaign.absolute() / "condor" / "startup"\n    if not marker_dir.is_dir():\n        return None\n    marker_id = hashlib.sha256(task_name.encode("utf-8")).hexdigest()\n    return marker_dir / f"{marker_id}.started"\n\n\ndef _mark_payload_started(campaign_dir: str | Path, task_name: str) -> bool:\n    marker = _startup_marker(campaign_dir, task_name)\n    if marker is None:\n        return True\n    try:\n        marker.touch()\n    except OSError as exc:\n        print(f"yall-worker: could not write startup marker {marker}: {exc}", file=sys.stderr)\n        return False\n    return True\n\n\n'''
new_marker = '''def _startup_marker(campaign_dir: Path, task_name: str) -> Path | None:\n    marker_dir = campaign_dir / "condor" / "startup"\n    if not marker_dir.is_dir():\n        return None\n    marker_id = hashlib.sha256(task_name.encode("utf-8")).hexdigest()\n    return marker_dir / f"{marker_id}.started"\n\n\ndef _mark_nonstartup_failure(marker: Path | None, stderr_path: Path) -> None:\n    if marker is None:\n        return\n    try:\n        marker.touch()\n    except OSError as exc:\n        with stderr_path.open("a") as err:\n            err.write(f"yall-worker: could not write startup marker {marker}: {exc}\\n")\n\n\n'''
replace_once("src/yall_run/worker.py", old_marker, new_marker)
replace_once(
    "src/yall_run/worker.py",
    '    task = _task_definition(campaign_dir, manifest, task_name)\n\n    number = _next_attempt_number(',
    '    task = _task_definition(campaign_dir, manifest, task_name)\n    startup_marker = _startup_marker(campaign_dir, task_name)\n\n    number = _next_attempt_number(',
)
replace_once(
    "src/yall_run/worker.py",
    '    launch_command = _payload_command(command, wrapper)\n',
    '    launch_command = _payload_command(command, wrapper, startup_marker)\n',
)
replace_once(
    "src/yall_run/worker.py",
    '        stderr_path.write_text(\n            "yall-worker: declared input missing: " + ", ".join(missing_inputs) + "\\n"\n        )\n        finished = _utc_now()\n',
    '        stderr_path.write_text(\n            "yall-worker: declared input missing: " + ", ".join(missing_inputs) + "\\n"\n        )\n        _mark_nonstartup_failure(startup_marker, stderr_path)\n        finished = _utc_now()\n',
)
replace_once(
    "src/yall_run/worker.py",
    '        stderr_path.write_text(\n            "yall-worker: declared output already exists: "\n            + ", ".join(existing_outputs)\n            + "\\n"\n        )\n        finished = _utc_now()\n',
    '        stderr_path.write_text(\n            "yall-worker: declared output already exists: "\n            + ", ".join(existing_outputs)\n            + "\\n"\n        )\n        _mark_nonstartup_failure(startup_marker, stderr_path)\n        finished = _utc_now()\n',
)
replace_once(
    "src/yall_run/worker.py",
    '    launch_error: OSError | None = None\n    with stdout_path.open("w") as out, stderr_path.open("w") as err:\n        try:\n',
    '    launch_error: OSError | None = None\n    with stdout_path.open("w") as out, stderr_path.open("w") as err:\n        if startup_marker is not None and wrapper is None:\n            try:\n                startup_marker.touch()\n            except OSError as exc:\n                err.write(f"yall-worker: could not write startup marker {startup_marker}: {exc}\\n")\n        try:\n',
)
replace_once(
    "src/yall_run/worker.py",
    '    if not _mark_payload_started(values[0], values[1]):\n        return 2\n',
    '',
)

# Tests: preserve worker attempt return codes while accepting Condor's synthetic
# scheduler-facing 100/101 classification and the wrapper-internal marker argv.
p = Path("tests/test_payload_wrapper.py")
text = p.read_text()
replacements = [
    (
        '    assert attempt["launch_command"][1:] == ["./payload.sh", *arguments]\n',
        '    if backend == "condor":\n        assert attempt["launch_command"][-(len(arguments) + 1):] == ["./payload.sh", *arguments]\n    else:\n        assert attempt["launch_command"][1:] == ["./payload.sh", *arguments]\n',
    ),
    (
        '    assert run(cdir, backend) == 2\n    assert not marker.exists()\n',
        '    assert run(cdir, backend) == (101 if backend == "condor" else 2)\n    assert not marker.exists()\n',
    ),
    (
        '    assert run(cdir, backend) == 42\n    assert record(cdir)["state"] == "failed"\n',
        '    assert run(cdir, backend) == (100 if backend == "condor" else 42)\n    assert record(cdir)["state"] == "failed"\n',
    ),
    (
        '    assert run(cdir, backend) == 2\n    attempt = record(cdir)\n',
        '    assert run(cdir, backend) == (100 if backend == "condor" else 2)\n    attempt = record(cdir)\n',
    ),
    (
        '    assert run(cdir, backend) == 1\n    assert record(cdir)["failure"]["kind"] == "missing_outputs"\n',
        '    assert run(cdir, backend) == (101 if backend == "condor" else 1)\n    assert record(cdir)["failure"]["kind"] == "missing_outputs"\n',
    ),
    (
        '    assert run(cdir, backend) == 9\n    first_attempt = ',
        '    assert run(cdir, backend) == (101 if backend == "condor" else 9)\n    first_attempt = ',
    ),
]
for old, new in replacements:
    if old not in text:
        raise SystemExit(f"tests/test_payload_wrapper.py pattern missing: {old!r}")
    text = text.replace(old, new, 1)
p.write_text(text)

p = Path("tests/test_wrapper_args.py")
text = p.read_text()
old = '    received = json.loads(capture.read_text().splitlines()[0])\n    assert received == [*args, "echo", "payload"]\n'
new = '    received = json.loads(capture.read_text().splitlines()[0])\n    if backend == "condor":\n        assert received[:len(args)] == list(args)\n        assert received[-2:] == ["echo", "payload"]\n        assert received[len(args):len(args) + 4] == [\n            "/bin/sh", "-c", \'marker=$1; shift; : > "$marker" || exit 125; exec "$@"\',\n            "yall-payload-start",\n        ]\n        assert Path(received[len(args) + 4]).parent.name == "startup"\n    else:\n        assert received == [*args, "echo", "payload"]\n'
if old not in text:
    raise SystemExit("wrapper args capture pattern missing")
text = text.replace(old, new, 1)
old = '    assert json.loads(capture.read_text().splitlines()[0]) == ["echo", "payload"]\n'
new = '    received = json.loads(capture.read_text().splitlines()[0])\n    if backend == "condor":\n        assert received[-2:] == ["echo", "payload"]\n        assert received[:4] == [\n            "/bin/sh", "-c", \'marker=$1; shift; : > "$marker" || exit 125; exec "$@"\',\n            "yall-payload-start",\n        ]\n        assert Path(received[4]).parent.name == "startup"\n    else:\n        assert received == ["echo", "payload"]\n'
if old not in text:
    raise SystemExit("path-only wrapper capture pattern missing")
text = text.replace(old, new, 1)
p.write_text(text)

# Keep PR #21's wording now that the outer launcher is looking at worker status.
p = Path("tests/test_startup_retry.py")
text = p.read_text().replace(
    'payload failed after startup (exit=37)',
    'payload failed after startup (exit=37)',
)
p.write_text(text)
