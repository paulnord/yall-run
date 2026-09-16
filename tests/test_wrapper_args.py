"""Wrapper argv must survive parsing, archiving and every batch renderer."""

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess

import pytest

from yall_run.batch_common import worker_command
from yall_run.campaign import begin_campaign, create_campaign, start_local
from yall_run.condor_backend import render_condor
from yall_run.model import CondorSpec, load_spec
from yall_run.pbs_backend import render_pbs
from yall_run.slurm_backend import render_slurm

RENDERERS = {"condor": render_condor, "slurm": render_slurm, "pbs": render_pbs}


def write_spec(tmp_path, directives, command="echo payload", backend="condor"):
    path = tmp_path / "Yallfile"
    path.write_text(
        f"campaign wrapper-test\nbackend {backend}\n{directives}\n"
        f"one:\n    {command}\n"
    )
    return path


@pytest.mark.parametrize("args", [(), ("--",), ("--flag", "alpha beta", "", "--"),
                                  ("$HOME", "*.root", "a'b", 'a"b', ";", "--")])
def test_wrapper_parse_arguments(tmp_path, args):
    path = write_spec(tmp_path, "%wrapper " + shlex.join(["./launcher", *args]))
    spec = load_spec(path)
    assert spec.condor.wrapper == "./launcher"
    assert spec.condor.wrapper_args == args


def test_wrapper_imported_values_are_single_tokens(tmp_path, monkeypatch):
    value = str(tmp_path / "EIC path's directory" / "eic-shell")
    label = 'a label with "quotes", $HOME and $(touch NEVER)'
    monkeypatch.setenv("EIC_SHELL", value)
    monkeypatch.setenv("LABEL", label)
    path = write_spec(tmp_path,
        '@env EIC_SHELL\n@env LABEL\n@set END --\n'
        '%wrapper {EIC_SHELL} --label {LABEL} {END}')
    spec = load_spec(path)
    assert spec.condor.wrapper == value
    assert spec.condor.wrapper_args == ("--label", label, "--")
    assert dict(spec.set_values)["EIC_SHELL"] == value


def test_wrapper_continuation_and_later_set(tmp_path):
    path = write_spec(tmp_path,
        '%wrapper "{LAUNCHER}" \\\n    --label "two words" \\\n    --\n'
        '@set LAUNCHER "path with spaces/eic-shell"')
    spec = load_spec(path)
    assert spec.condor.wrapper == "path with spaces/eic-shell"
    assert spec.condor.wrapper_args == ("--label", "two words", "--")


def test_replacing_wrapper_clears_old_arguments(tmp_path):
    spec = load_spec(write_spec(tmp_path, "%wrapper ./first --\n%wrapper ./second"))
    assert spec.condor.wrapper == "./second"
    assert spec.condor.wrapper_args == ()


@pytest.mark.parametrize("directive, message", [
    ("%wrapper", "executable path"),
    ('%wrapper "" --', "nonempty executable"),
    ('%wrapper "   " --', "nonempty executable"),
    ("%wrapper {MISSING} --", "no value for"),
    ("%wrapper ./ok {MISSING}", "no value for"),
    ('%wrapper "unclosed', "No closing quotation"),
    ("%wrapper ./ok bad\0argument", "NUL"),
])
def test_invalid_wrapper_directives(tmp_path, directive, message):
    with pytest.raises(ValueError, match=message):
        load_spec(write_spec(tmp_path, directive))


def test_empty_imported_launcher_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("EIC_SHELL", "")
    with pytest.raises(ValueError, match="nonempty executable"):
        load_spec(write_spec(tmp_path, "@env EIC_SHELL\n%wrapper {EIC_SHELL} --"))


def test_missing_imported_launcher_is_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("EIC_SHELL", raising=False)
    with pytest.raises(ValueError, match="required environment variable"):
        load_spec(write_spec(tmp_path, "@env EIC_SHELL\n%wrapper {EIC_SHELL} --"))


def test_wrapper_stays_campaign_level(tmp_path):
    path = write_spec(tmp_path, "", "%wrapper ./launcher --")
    with pytest.raises(ValueError, match="task directive %wrapper"):
        load_spec(path)


def test_legacy_model_has_empty_wrapper_args():
    spec = CondorSpec(wrapper="./legacy.sh")
    assert spec.wrapper_args == ()
    assert asdict(spec)["wrapper"] == "./legacy.sh"
    assert CondorSpec(**{"wrapper": "./legacy.sh"}).wrapper_args == ()


def test_command_quoting_preserves_each_argument(tmp_path):
    worker = tmp_path / "worker's file.py"
    campaign = tmp_path / "campaign with spaces"
    wrapper = tmp_path / "wrapper's file"
    args = ("--label", "$(touch UNEXPECTED)", "", "a\nb", '"quote"', "--")
    command = worker_command(worker, campaign, "one", wrapper, args)
    assert shlex.split(command) == [str(wrapper), *args, "/usr/bin/env", "python3",
                                    str(worker), str(campaign), "one"]
    with pytest.raises(ValueError, match="require a wrapper"):
        worker_command(worker, campaign, "one", None, ("--",))


def make_launcher(tmp_path, *, separator=True, exit_code=None):
    """Stand-in launcher: record argv, consume '--', then exec the real worker."""
    wrapper = tmp_path / "launcher directory" / "eic-shell"
    wrapper.parent.mkdir(exist_ok=True)
    capture = tmp_path / "wrapper-argv.jsonl"
    code = (
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        f"with open({str(capture)!r}, 'a') as out:\n"
        "    out.write(json.dumps(sys.argv[1:]) + '\\n')\n"
    )
    if exit_code is not None:
        code += f"sys.exit({exit_code})\n"
    elif separator:
        code += (
            "if '--' not in sys.argv[1:]:\n"
            "    sys.exit(97)\n"
            "command = sys.argv[sys.argv.index('--') + 1:]\n"
            "os.execvp(command[0], command)\n"
        )
    else:
        code += "os.execvp(sys.argv[1], sys.argv[1:])\n"
    wrapper.write_text(code)
    wrapper.chmod(0o755)
    return wrapper, capture


def node_script(campaign, backend):
    return campaign / backend / "yall_0000_one.sh"


@pytest.mark.parametrize("backend", RENDERERS)
def test_backend_wrapper_args_are_frozen_and_executed(tmp_path, monkeypatch, backend):
    wrapper, capture = make_launcher(tmp_path)
    original = wrapper.read_bytes()
    args = ("--label", "two words", "", "$(touch UNEXPECTED)", "a'b", "--")
    monkeypatch.setenv("EIC_SHELL", str(wrapper))
    path = write_spec(tmp_path, "@env EIC_SHELL\n%wrapper {EIC_SHELL} " + shlex.join(args))
    spec = load_spec(path)
    campaign = RENDERERS[backend](spec, tmp_path / "campaign root")
    manifest = json.loads((campaign / "campaign.json").read_text())
    render = json.loads((campaign / backend / "render.json").read_text())
    record = render["wrapper"]
    assert record["args"] == list(args)
    assert record["sha256"] == hashlib.sha256(original).hexdigest()
    assert record["size_bytes"] == len(original)
    assert record["source"] == str(wrapper)
    assert manifest["execution"][backend]["wrapper"] == record
    assert (campaign / "Yallfile").read_bytes() == path.read_bytes()
    assert Path(record["path"]).read_bytes() == original
    assert os.access(record["path"], os.X_OK)
    if backend == "condor":
        assert render["condor"]["wrapper_args"] == list(args)

    # Editing/removing the source and changing the host environment cannot alter
    # an already rendered campaign's wrapper argv or executable bytes.
    wrapper.unlink()
    monkeypatch.setenv("EIC_SHELL", "/not/the/original/launcher")
    path.write_text("not the original workflow\n")
    begin_campaign(campaign)
    assert json.loads((campaign / "start.json").read_text())["execution"][backend]["wrapper"] == record
    script = node_script(campaign, backend)
    subprocess.run(["bash", "-n", str(script)], check=True)
    result = subprocess.run(["bash", str(script)], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    received = json.loads(capture.read_text().splitlines()[0])
    assert received == [*args, "/usr/bin/env", "python3",
                        str(campaign / backend / "yall_worker.py"), str(campaign), "one"]
    assert not (tmp_path / "UNEXPECTED").exists()
    attempt = campaign / "one_attempt_001"
    assert (attempt / "stdout.log").read_text().strip() == "payload"
    assert json.loads((attempt / "attempt.json").read_text())["state"] == "completed"


@pytest.mark.parametrize("backend", RENDERERS)
def test_backend_path_only_wrapper_still_runs(tmp_path, backend):
    wrapper, capture = make_launcher(tmp_path, separator=False)
    path = write_spec(tmp_path, "%wrapper " + shlex.quote(str(wrapper)))
    campaign = RENDERERS[backend](load_spec(path), tmp_path / "campaigns")
    result = subprocess.run(["bash", str(node_script(campaign, backend))], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(capture.read_text().splitlines()[0])[0] == "/usr/bin/env"
    assert json.loads((campaign / backend / "render.json").read_text())["wrapper"]["args"] == []


@pytest.mark.parametrize("backend", RENDERERS)
def test_backend_without_wrapper_is_unchanged(tmp_path, backend):
    path = write_spec(tmp_path, "")
    campaign = RENDERERS[backend](load_spec(path), tmp_path / "campaigns")
    manifest = json.loads((campaign / "campaign.json").read_text())
    assert manifest["execution"] == {backend: {}}
    assert json.loads((campaign / backend / "render.json").read_text())["wrapper"] is None
    result = subprocess.run(["bash", str(node_script(campaign, backend))], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("backend", RENDERERS)
def test_backend_failure_exit_code_and_retries(tmp_path, backend):
    wrapper, capture = make_launcher(tmp_path, exit_code=37)
    path = write_spec(tmp_path, "%wrapper " + shlex.quote(str(wrapper)) + " --",
                      "%retry 1\n    echo never")
    campaign = RENDERERS[backend](load_spec(path), tmp_path / "campaigns")
    result = subprocess.run(["bash", str(node_script(campaign, backend))], capture_output=True, text=True)
    assert result.returncode == (100 if backend == "condor" else 37)
    # Condor classifies a wrapper failure before worker entry as startup failure;
    # PBS/Slurm retain their existing in-script %retry behavior.
    assert len(capture.read_text().splitlines()) == (1 if backend == "condor" else 2)
    assert not (campaign / "one_attempt_001").exists()


@pytest.mark.parametrize("backend", RENDERERS)
def test_backend_missing_launcher_is_reported(tmp_path, backend):
    path = write_spec(tmp_path, "%wrapper ./missing --")
    with pytest.raises(ValueError, match="wrapper does not exist"):
        RENDERERS[backend](load_spec(path), tmp_path / "campaigns")


@pytest.mark.parametrize("backend", RENDERERS)
def test_backend_relative_and_tilde_launcher_paths(tmp_path, monkeypatch, backend):
    wrapper, _ = make_launcher(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    for i, spelling in enumerate((str(wrapper.relative_to(tmp_path)), "~/" + str(wrapper.relative_to(tmp_path)))):
        path = write_spec(tmp_path, "%wrapper " + shlex.quote(spelling) + " --")
        campaign = RENDERERS[backend](load_spec(path), tmp_path / f"campaigns-{i}")
        record = json.loads((campaign / backend / "render.json").read_text())["wrapper"]
        assert record["source"] == str(wrapper)


def test_local_backend_does_not_apply_batch_wrapper(tmp_path):
    path = write_spec(tmp_path, "%wrapper ./does-not-exist --", backend="local")
    campaign = create_campaign(load_spec(path), tmp_path / "campaigns")
    start_local(campaign)
    assert (campaign / "one_attempt_001/stdout.log").read_text().strip() == "payload"
    assert not (campaign / "environment").exists()


def test_eic_shell_example_parses(monkeypatch):
    monkeypatch.setenv("EIC_SHELL", "/shared/eic/eic-shell")
    path = Path(__file__).resolve().parents[1] / "examples/eic-shell/Yallfile"
    spec = load_spec(path)
    assert spec.backend == "condor"
    assert spec.condor.wrapper == "./run-in-eic-shell.sh"
    assert spec.condor.wrapper_args == ("/shared/eic/eic-shell",)
    assert len(spec.tasks) == 3
    assert spec.tasks[-1].parents == ("root-version", "python-version")
