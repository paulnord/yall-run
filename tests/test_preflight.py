"""Creation-time host setup must never become a scheduled/retried task."""
import json
from pathlib import Path
import shlex
import sys

import pytest

from yall_run import campaign
from yall_run.campaign import create_campaign, retry_task, start_local
from yall_run.cli import main
from yall_run.model import load_spec
from yall_run.worker import run_task


def recipe(tmp_path, directives, *, backend="local", payload="echo payload", wrapper=""):
    source = tmp_path / "workflow with spaces" / "Yallfile"
    source.parent.mkdir(exist_ok=True)
    source.write_text(
        f"campaign host-setup\nbackend {backend}\n{wrapper}{directives}\n"
        "analyze:\n"
        f"    {payload}\n"
    )
    return source


@pytest.mark.parametrize("backend", ["local", "condor", "slurm", "pbs"])
def test_create_runs_in_order_on_host_and_stdout_is_only_campaign_path(
    tmp_path, monkeypatch, capsys, backend
):
    monkeypatch.setenv("PREFLIGHT_TEST_VALUE", "inherited")
    wrapper = tmp_path / "container-launcher"
    wrapper.write_text("#!/bin/sh\ntouch wrapper-was-run\nexit 99\n")
    wrapper.chmod(0o755)
    source = recipe(
        tmp_path,
        "%preflight ! printf first > order; mkdir -p results\n"
        "%preflight ! test -d results && printf second >> order && "
        'printf "$PREFLIGHT_TEST_VALUE" && printf diagnostics >&2',
        backend=backend,
        # Neither the container launcher nor task runs during create.
        wrapper=f"%wrapper {shlex.quote(str(wrapper))}\n",
    )
    monkeypatch.chdir(tmp_path)
    assert main(["create", str(source), "--campaigns-dir", str(tmp_path / "campaigns")]) == 0
    captured = capsys.readouterr()
    directory = Path(captured.out.strip())
    assert captured.out == str(directory) + "\n"
    assert "inherited" in captured.err and "diagnostics" in captured.err
    assert (source.parent / "order").read_text() == "firstsecond"
    assert not (tmp_path / "order").exists()
    assert not (source.parent / "wrapper-was-run").exists()
    assert not list(directory.glob("*_attempt_*"))
    manifest = json.loads((directory / "campaign.json").read_text())
    assert manifest["task_order"] == ["analyze"]
    assert manifest["backend"] == backend
    assert [record["state"] for record in manifest["preflight"]] == ["completed"] * 2
    for index, record in enumerate(manifest["preflight"], 1):
        assert record["cwd"] == str(source.parent)
        assert record["returncode"] == 0
        assert record["hostname"]
        assert record["finished_at"] >= record["started_at"]
        assert json.loads((directory / "preflight" / f"{index:03d}" / "result.json").read_text()) == record
        assert (directory / record["stdout"]).is_file()
        assert (directory / record["stderr"]).is_file()
    assert (directory / manifest["preflight"][1]["stdout"]).read_text() == "inherited"
    assert (directory / manifest["preflight"][1]["stderr"]).read_text() == "diagnostics"
    if backend == "condor":
        dag = (directory / "condor" / "campaign.dag").read_text()
        assert sum(line.startswith("JOB ") for line in dag.splitlines()) == 1
        assert "preflight" not in dag


@pytest.mark.parametrize("failure", ["! echo failed >&2; exit 7", "/no/such/preflight-executable"])
def test_failure_stops_list_preserves_logs_and_cannot_launch(tmp_path, capsys, failure):
    source = recipe(tmp_path, f"%preflight {failure}\n%preflight ! touch should-not-exist")
    root = tmp_path / "campaigns"
    assert main(["create", str(source), "--campaigns-dir", str(root)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "preflight 1 failed" in captured.err
    directory, = root.iterdir()
    assert not (source.parent / "should-not-exist").exists()
    assert not (directory / "campaign.json").exists()
    assert not (directory / "preflight" / "002").exists()
    record = json.loads((directory / "preflight" / "001" / "result.json").read_text())
    assert record["state"] == "failed"
    if failure.startswith("!"):
        assert record["returncode"] == 7
        assert (directory / record["stderr"]).read_text() == "failed\n"
    else:
        assert record["returncode"] is None
        assert record["error"]
    assert main(["start", str(directory)]) == 2
    assert "not a yall campaign" in capsys.readouterr().err
    with pytest.raises(ValueError, match="not a yall campaign"):
        run_task(directory, "analyze")
    assert not (directory / "start.json").exists()


def test_interrupted_setup_is_not_launchable(tmp_path, monkeypatch):
    source = recipe(tmp_path, "%preflight echo hello")
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt
    monkeypatch.setattr(campaign.subprocess, "run", interrupt)
    root = tmp_path / "campaigns"
    with pytest.raises(KeyboardInterrupt):
        create_campaign(load_spec(source), root)
    directory, = root.iterdir()
    assert not (directory / "campaign.json").exists()
    record = json.loads((directory / "preflight" / "001" / "result.json").read_text())
    assert record["state"] == "interrupted"


def test_preflight_is_not_repeated_by_start_or_task_retry(tmp_path):
    source = recipe(
        tmp_path, "%preflight ! printf setup >> setup-count",
        payload="! if test -f task-once; then exit 0; else touch task-once; exit 1; fi",
    )
    directory = create_campaign(load_spec(source), tmp_path / "campaigns")
    frozen = (directory / "campaign.json").read_bytes()
    source.write_text("invalid changed source\n")
    # Local execution records task failure; retry must not call setup again.
    with pytest.raises(RuntimeError, match="local campaign failed"):
        start_local(directory)
    assert retry_task(directory, "analyze") == 0
    assert (source.parent / "setup-count").read_text() == "setup"
    assert (directory / "campaign.json").read_bytes() == frozen


def test_preflight_argv_is_literal_and_setup_inputs_are_fingerprinted(tmp_path, monkeypatch):
    source = recipe(
        tmp_path,
        "@env SETUP_VALUE\n"
        f"%preflight {shlex.quote(sys.executable)} setup.py \"{{SETUP_VALUE}}\"",
    )
    script = source.parent / "setup.py"
    script.write_text("from pathlib import Path\nimport sys\nPath('input.txt').write_text(sys.argv[1])\n")
    source.write_text(source.read_text().replace("    echo payload", "    @input data input.txt\n    echo payload"))
    literal = "spaces ' quotes $(touch injected)"
    monkeypatch.setenv("SETUP_VALUE", literal)
    directory = create_campaign(load_spec(source), tmp_path / "campaigns")
    assert (source.parent / "input.txt").read_text() == literal
    assert not (source.parent / "injected").exists()
    manifest = json.loads((directory / "campaign.json").read_text())
    assert manifest["preflight"][0]["command"][-1] == literal
    assert manifest["tasks"]["analyze"]["inputs"][0]["creation_fingerprint"]["size_bytes"] == len(literal)
