"""Payload-only wrapping with real workers and simulated execution environments.

No live batch service or EIC container is required for these regression tests.
"""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from yall_run.campaign import create_campaign, start_local, campaign_status
from yall_run.cli import main
from yall_run.condor_backend import render_condor
from yall_run.model import ExecutionSpec, load_spec
from yall_run.pbs_backend import render_pbs
from yall_run.slurm_backend import render_slurm
from yall_run.worker import _payload_command, run_task

RENDERERS = {"local": create_campaign, "condor": render_condor,
             "slurm": render_slurm, "pbs": render_pbs}


def write_executable(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
    return path


def campaign(tmp_path, backend, command, *, wrapper_body='exec "$@"\n',
             args=(), directives="", preamble=""):
    wrapper = write_executable(tmp_path / "wrapper's directory/launcher.sh",
                               "#!/bin/sh\n" + wrapper_body)
    source = tmp_path / "Yallfile"
    source.write_text(
        f"campaign payload-wrapper\nbackend {backend}\n{preamble}"
        "%wrapper " + shlex.join([str(wrapper), *args]) + "\n"
        "one:\n" + directives + f"    {command}\n"
    )
    return RENDERERS[backend](load_spec(source), tmp_path / "campaign's directory")


def run(cdir, backend):
    if backend == "local":
        return run_task(cdir, "one")
    # The generated node script must run standalone, without importing yall_run.
    script = cdir / backend / "yall_0000_one.sh"
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    result = subprocess.run(["/bin/bash", str(script)], cwd=cdir,
                            env=env, capture_output=True, text=True, timeout=15)
    return result.returncode


def record(cdir, name="attempt.json", number=1):
    return json.loads((cdir / f"one_attempt_{number:03d}" / name).read_text())


@pytest.mark.parametrize("backend", RENDERERS)
def test_payload_with_no_python_leaves_host_worker_functional(tmp_path, monkeypatch, backend):
    monkeypatch.setenv("BOUNDARY_TEST", "host")
    isolated = tmp_path / "no-python"
    isolated.mkdir()
    cdir = campaign(tmp_path, backend,
        '! if command -v python3 >/dev/null 2>&1; then exit 98; fi; '
        'printf "%s|%s|%s" "$BOUNDARY_TEST" "$YALL_TASK" "$YALL_ATTEMPT" > @output.result',
        wrapper_body='export PATH="$1" PYTHONHOME=/invalid/python PYTHONPATH=/invalid/imports\n'
                     'export BOUNDARY_TEST=payload\nshift\nexec "$@"\n',
        args=(str(isolated),), directives="    @output result result.txt\n")
    assert run(cdir, backend) == 0
    assert (tmp_path / "result.txt").read_text() == "payload|one|1"
    assert os.environ["BOUNDARY_TEST"] == "host"
    provenance = record(cdir, "provenance.json")
    assert provenance["execution"]["context"] == "host"
    assert provenance["execution"]["python"] == sys.version
    assert provenance["execution"]["wrapper"]["args"] == [str(isolated)]
    assert provenance["execution"]["launch_command"][2:4] == ["/bin/sh", "-c"]
    assert record(cdir)["state"] == "completed"
    assert campaign_status(cdir)["tasks"][0]["state"] == "completed"


@pytest.mark.parametrize("backend", RENDERERS)
def test_relative_executable_cwd_and_argument_boundaries(tmp_path, backend):
    cwd = tmp_path / "work's directory"
    arguments = ["", "two words", "a'b", 'a"b', "$HOME", "$(touch BAD)",
                 "*.root", "--", "x;y", "a\\b"]
    target = write_executable(cwd / "payload.sh", '#!/bin/sh\nprintf "%s\\n" "$@"\npwd\n'
                              'printf "%s\\n" "$YALL_TASK_CWD"\n')
    cdir = campaign(tmp_path, backend, shlex.join(["./payload.sh", *arguments]),
                    directives="    %cwd " + shlex.quote(str(cwd)) + "\n")
    assert run(cdir, backend) == 0
    assert (cdir / "one_attempt_001/stdout.log").read_text().splitlines() == [
        *arguments, str(cwd), str(cwd)]
    assert not (cwd / "BAD").exists()
    assert record(cdir)["command"] == ["./payload.sh", *arguments]
    assert record(cdir)["launch_command"][1:] == ["./payload.sh", *arguments]


@pytest.mark.parametrize("backend", RENDERERS)
def test_shell_expansion_pipeline_and_redirect_run_inside_wrapper(tmp_path, monkeypatch, backend):
    monkeypatch.setenv("EXPAND_AT", "host")
    cdir = campaign(tmp_path, backend,
        '! printf "%s" "$EXPAND_AT" | /usr/bin/tr a-z A-Z > @output.result',
        wrapper_body='export EXPAND_AT=wrapped\nexec "$@"\n',
        directives="    @output result shell.txt\n")
    assert run(cdir, backend) == 0
    assert (tmp_path / "shell.txt").read_text() == "WRAPPED"
    assert record(cdir)["launch_command"][1:3] == ["/bin/sh", "-c"]


@pytest.mark.parametrize("backend", RENDERERS)
@pytest.mark.parametrize("guard", ["missing_input", "existing_output"])
def test_guards_run_before_wrapper_invocation(tmp_path, backend, guard):
    marker = tmp_path / "wrapper-started"
    directive = "    @input data absent.txt\n"
    if guard == "existing_output":
        (tmp_path / "result.txt").write_text("keep me")
        directive = "    @output data result.txt\n"
    cdir = campaign(tmp_path, backend, "/bin/true",
        wrapper_body=f": > {shlex.quote(str(marker))}\nexec \"$@\"\n", directives=directive)
    assert run(cdir, backend) == 2
    assert not marker.exists()
    failure = record(cdir)
    assert failure["state"] == "failed"
    assert failure["failure"]["kind"] == ("missing_inputs" if guard == "missing_input" else "outputs_exist")
    if guard == "existing_output":
        assert (tmp_path / "result.txt").read_text() == "keep me"


@pytest.mark.parametrize("backend", RENDERERS)
def test_wrapper_setup_failure_is_recorded_in_attempt_logs(tmp_path, backend):
    cdir = campaign(tmp_path, backend, "/bin/true",
                    wrapper_body="echo setup-out\necho setup-failed >&2\nexit 42\n")
    assert run(cdir, backend) == 42
    assert record(cdir)["state"] == "failed"
    assert record(cdir)["command_returncode"] == 42
    assert record(cdir)["failure"]["kind"] == "command_failed"
    assert (cdir / "one_attempt_001/stdout.log").read_text() == "setup-out\n"
    assert "setup-failed" in (cdir / "one_attempt_001/stderr.log").read_text()


@pytest.mark.parametrize("backend", RENDERERS)
def test_missing_archived_wrapper_finalizes_failed_attempt(tmp_path, backend):
    cdir = campaign(tmp_path, backend, "/bin/true")
    manifest = json.loads((cdir / "campaign.json").read_text())
    Path(manifest["execution"]["wrapper"]["path"]).unlink()
    assert run(cdir, backend) == 2
    attempt = record(cdir)
    assert attempt["state"] == "failed"
    assert attempt["finished_at"]
    assert attempt["failure"]["kind"] == "launch_failed"
    assert attempt["failure"]["errno"] == 2
    assert attempt["command_pid"] is None
    assert attempt["command_returncode"] is None
    assert "launch failed" in (cdir / "one_attempt_001/stderr.log").read_text()


@pytest.mark.parametrize("backend", RENDERERS)
def test_wrapped_command_failure_does_not_remain_running(tmp_path, backend):
    cdir = campaign(tmp_path, backend, "./missing-program")
    assert run(cdir, backend) != 0
    assert record(cdir)["state"] == "failed"
    assert "missing-program" in (cdir / "one_attempt_001/stderr.log").read_text()


@pytest.mark.parametrize("backend", RENDERERS)
def test_missing_output_is_checked_after_wrapper_returns(tmp_path, backend):
    cdir = campaign(tmp_path, backend, "/bin/true", directives="    @output missing nope.txt\n")
    assert run(cdir, backend) == 1
    assert record(cdir)["failure"]["kind"] == "missing_outputs"


def test_payload_command_never_shell_interpolates_argv():
    command = ["/bin/echo", "", "a\nb", "$(touch BAD)", "a'b", "--"]
    wrapper = {"path": "/wrapper path", "args": ["", "a'b"]}
    assert _payload_command(command, wrapper) == ["/wrapper path", "", "a'b", *command]
    text = "printf '%s' \"$VALUE\" | cat > output"
    assert _payload_command(text, wrapper) == ["/wrapper path", "", "a'b", "/bin/sh", "-c", text]


@pytest.mark.parametrize("kwargs", [{"wrapper": ""}, {"wrapper": "a\0b"},
    {"wrapper": "ok", "wrapper_args": ("\0",)}, {"wrapper_args": ("--",)}])
def test_execution_spec_validation(kwargs):
    with pytest.raises(ValueError):
        ExecutionSpec(**kwargs)


def test_plan_reports_generic_payload_policy(tmp_path, capsys):
    campaign(tmp_path, "local", "/bin/true")
    assert main(["plan", str(tmp_path / "Yallfile"), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["execution"]["wrapper"].endswith("launcher.sh")
    assert main(["plan", str(tmp_path / "Yallfile")]) == 0
    assert "Payload wrapper:" in capsys.readouterr().out


@pytest.mark.parametrize("backend", ["condor", "slurm", "pbs"])
def test_resume_reuses_frozen_payload_wrapper(tmp_path, monkeypatch, backend):
    from yall_run import recovery
    from yall_run.campaign import begin_campaign
    from test_recovery import Scheduler

    payload = write_executable(tmp_path / "payload.sh",
        '#!/bin/sh\n[ -f ready ] || exit 9\nprintf "%s" "$FROM_WRAPPER" > result\n')
    cdir = campaign(tmp_path, backend, "./payload.sh",
        wrapper_body='export FROM_WRAPPER=frozen\nexec "$@"\n',
        directives="    @output result result\n")
    begin_campaign(cdir)
    submit = {"returncode": 0}
    if backend == "condor":
        submit["cluster_id"] = 100
        (cdir / backend / "campaign.dag.rescue001").write_text("# no completed tasks\n")
    else:
        submit["jobs"] = {"one": "10.server" if backend == "pbs" else "10"}
    (cdir / backend / "submit.json").write_text(json.dumps(submit))
    assert run(cdir, backend) == 9
    first_attempt = (cdir / "one_attempt_001/attempt.json").read_bytes()
    manifest = (cdir / "campaign.json").read_bytes()
    # Recovery must not use the current wrapper source or rewrap its own worker.
    (tmp_path / "wrapper's directory/launcher.sh").write_text("#!/bin/sh\nexit 99\n")
    (tmp_path / "ready").touch()
    monkeypatch.setattr(recovery, "_run", Scheduler(backend))
    assert recovery.resume_campaign(cdir) == 0
    if backend == "condor":
        assert run(cdir, backend) == 0
    else:
        script = cdir / "resumes/0001" / backend / "yall_0000_one.sh"
        assert subprocess.run(["/bin/bash", str(script)], capture_output=True, timeout=15).returncode == 0
    assert (tmp_path / "result").read_text() == "frozen"
    assert (cdir / "campaign.json").read_bytes() == manifest
    assert (cdir / "one_attempt_001/attempt.json").read_bytes() == first_attempt
    assert record(cdir, number=2)["state"] == "completed"


def test_local_retry_wraps_each_payload_attempt(tmp_path):
    marker = tmp_path / "first-attempt"
    cdir = campaign(tmp_path, "local", "/bin/echo retried",
        wrapper_body=f'if [ ! -f {shlex.quote(str(marker))} ]; then\n'
                     f'  : > {shlex.quote(str(marker))}\n  exit 7\nfi\nexec "$@"\n',
        directives="    %retry 1\n")
    start_local(cdir)
    assert record(cdir)["command_returncode"] == 7
    assert record(cdir, number=2)["state"] == "completed"
