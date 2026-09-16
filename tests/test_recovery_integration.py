"""Exercise recovery with the real renderers, workers and CLI.

Scheduler commands are simulated; these are not production-cluster tests.
"""
import json

import pytest

from yall_run import recovery
from yall_run.campaign import begin_campaign, campaign_status
from yall_run.cli import main
from yall_run.condor_backend import render_condor
from yall_run.model import load_spec
from yall_run.pbs_backend import render_pbs
from yall_run.slurm_backend import render_slurm
from yall_run.worker import run_task
from test_recovery import Scheduler, write


@pytest.fixture
def rendered(tmp_path):
    def make(backend):
        source = tmp_path / "Yallfile"
        source.write_text(
            f"campaign recovery-integration\nbackend {backend}\n\n"
            "prepare:\n    /bin/true\n\n"
            "convert: prepare\n    ./convert.sh\n\n"
            "analyze: convert\n    /bin/true\n"
        )
        renderer = {"condor": render_condor, "slurm": render_slurm, "pbs": render_pbs}[backend]
        c = renderer(load_spec(source), tmp_path / "campaigns")
        begin_campaign(c)
        record = {"returncode": 0}
        if backend == "condor":
            record["cluster_id"] = 100
            (c / backend / "campaign.dag.rescue001").write_text("DONE yall_0000_prepare\n")
        else:
            record["jobs"] = {name: str(i) + (".server" if backend == "pbs" else "")
                              for i, name in enumerate(("prepare", "convert", "analyze"), 10)}
        write(c / backend / "submit.json", record)
        assert run_task(c, "prepare") == 0
        # Reproduce the missing Convert failure that motivated this change.
        with pytest.raises(OSError):
            run_task(c, "convert")
        assert campaign_status(c)["tasks"][1]["state"] == "running"
        (tmp_path / "convert.sh").write_text("#!/bin/sh\nexit 0\n")
        (tmp_path / "convert.sh").chmod(0o755)
        return c
    return make


@pytest.mark.parametrize("backend", ["condor", "slurm", "pbs"])
def test_resume_integration_preserves_campaign_and_increments_attempts(rendered, monkeypatch, backend):
    c = rendered(backend)
    scheduler = Scheduler(backend)
    monkeypatch.setattr(recovery, "_run", scheduler)
    manifest_before = (c / "campaign.json").read_bytes()
    worker_before = (c / backend / "yall_worker.py").read_bytes()
    failed_attempt = (c / "convert_attempt_001" / "attempt.json").read_bytes()
    assert recovery.resume_campaign(c) == 0
    assert run_task(c, "convert") == 0
    assert run_task(c, "analyze") == 0
    status = campaign_status(c)
    assert status["counts"] == {"completed": 3}
    assert [task["attempts"] for task in status["tasks"]] == [1, 2, 1]
    assert (c / "campaign.json").read_bytes() == manifest_before
    assert (c / backend / "yall_worker.py").read_bytes() == worker_before
    assert (c / "convert_attempt_001" / "attempt.json").read_bytes() == failed_attempt
    assert recovery.resume_campaign(c) == 0  # Completed campaign is a no-op.


@pytest.mark.parametrize("backend", ["condor", "slurm", "pbs"])
def test_cli_resume_dispatch_and_reconciled_status(rendered, monkeypatch, capsys, backend):
    c = rendered(backend)
    scheduler = Scheduler(backend)
    monkeypatch.setattr(recovery, "_run", scheduler)
    assert main(["resume", str(c), "--dry-run"]) == 0
    assert "keep 1 completed" in capsys.readouterr().out
    assert scheduler.submissions == 0
    assert main(["resume", str(c)]) == 0
    capsys.readouterr()
    assert main(["status", str(c), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["tasks"][1]["state"] == "interrupted"
    assert data["scheduler"]["query_ok"] is True
    if backend == "condor":
        assert data["scheduler"]["cluster_id"] == 201
    scheduler.fail_query = True
    assert main(["status", str(c)]) == 0
    text = capsys.readouterr().out
    assert "scheduler: unknown" in text
    assert "not-in-queue" not in text
    assert main(["resume", str(c)]) == 2
    assert "resume refused" in capsys.readouterr().err


def test_local_resume_dispatch_remains_available(tmp_path, monkeypatch):
    from yall_run import campaign
    source = tmp_path / "Yallfile"
    source.write_text("campaign local\nbackend local\nhello:\n    /bin/true\n")
    c = campaign.create_campaign(load_spec(source), tmp_path / "campaigns")
    seen = []
    monkeypatch.setattr(campaign, "resume_local", lambda path: seen.append(path))
    assert main(["resume", str(c)]) == 0
    assert seen == [c]
