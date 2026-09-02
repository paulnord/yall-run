import io
import json

import pytest

from yawl_run.condor_backend import render_condor, submit_rendered
from yawl_run.model import load_spec


def test_condor_dag_render(tmp_path):
    spec_file = tmp_path / "Yawlfile"
    spec_file.write_text(
        "campaign dag-test\n"
        "backend condor\n\n"
        "left:\n"
        "    %retry 2\n"
        "    echo left\n\n"
        "right:\n"
        "    echo right\n\n"
        "finish: left right\n"
        "    echo finish\n"
    )
    spec = load_spec(spec_file)
    campaign_dir = render_condor(spec, tmp_path / "campaigns")
    dag = (campaign_dir / "condor" / "campaign.dag").read_text()
    assert "RETRY yawl_0000_left 2" in dag
    assert "PARENT yawl_0000_left yawl_0001_right CHILD yawl_0002_finish" in dag
    assert (campaign_dir / "condor" / "yawl_worker.py").is_file()
    assert not (campaign_dir / "start.json").exists()
    manifest = json.loads((campaign_dir / "campaign.json").read_text())
    assert manifest["execution"] == {"condor": {}}


def test_condor_start_submits_without_local_jobs_limit(tmp_path, monkeypatch, capsys):
    spec_file = tmp_path / "Yawlfile"
    spec_file.write_text(
        "campaign submit\n"
        "backend condor\n\n"
        "one:\n"
        "    echo one\n\n"
        "two:\n"
        "    echo two\n"
    )
    spec = load_spec(spec_file)
    campaign_dir = render_condor(spec, tmp_path / "campaigns")
    calls = []

    class FakePopen:
        def __init__(self, command, **kwargs):
            calls.append((command, kwargs))
            self.stdout = io.StringIO("1 job(s) submitted to cluster 12345.\n")

        def wait(self):
            return 0

    monkeypatch.setattr("yawl_run.condor_backend.subprocess.Popen", FakePopen)
    submit_rendered(campaign_dir)

    assert calls[0][0] == ["condor_submit_dag", "campaign.dag"]
    output = capsys.readouterr().out
    assert "submitted to cluster 12345" in output
    assert "[condor] submitted cluster=12345" in output
    start = json.loads((campaign_dir / "start.json").read_text())
    assert start["execution"] == {"condor": {}}
    submit = json.loads((campaign_dir / "condor" / "submit.json").read_text())
    assert submit["cluster_id"] == 12345


def test_failed_condor_submission_does_not_mark_campaign_started(tmp_path, monkeypatch):
    spec_file = tmp_path / "Yawlfile"
    spec_file.write_text(
        "campaign failed-submit\n"
        "backend condor\n\n"
        "one:\n"
        "    echo one\n"
    )
    spec = load_spec(spec_file)
    campaign_dir = render_condor(spec, tmp_path / "campaigns")

    class FakePopen:
        def __init__(self, command, **kwargs):
            self.stdout = io.StringIO("schedd unavailable\n")

        def wait(self):
            return 1

    monkeypatch.setattr("yawl_run.condor_backend.subprocess.Popen", FakePopen)
    with pytest.raises(RuntimeError, match="schedd unavailable"):
        submit_rendered(campaign_dir)

    assert not (campaign_dir / "start.json").exists()
    submit = json.loads((campaign_dir / "condor" / "submit.json").read_text())
    assert submit["returncode"] == 1


def test_condor_task_resources_override_campaign_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    spec_file = tmp_path / "Yawlfile"
    spec_file.write_text(
        "campaign resources\n"
        "backend condor\n"
        "%cpus 1\n"
        "%memory 2GB\n"
        "%disk 3GB\n\n"
        "heavy:\n"
        "    %cpus 4\n"
        "    %memory 8GB\n"
        "    %disk 10GB\n"
        "    echo heavy\n"
    )
    spec = load_spec(spec_file)
    campaign_dir = render_condor(spec, tmp_path / "campaigns")
    submit = (campaign_dir / "condor" / "yawl_0000_heavy.sub").read_text()
    assert "request_cpus = 4" in submit
    assert "request_memory = 8GB" in submit
    assert "request_disk = 10GB" in submit


def test_condor_wrapper_is_archived(tmp_path):
    wrapper = tmp_path / "container-wrapper.sh"
    wrapper.write_text("#!/bin/bash\nexec \"$@\"\n")
    wrapper.chmod(0o755)
    spec_file = tmp_path / "Yawlfile"
    spec_file.write_text(
        "campaign wrapped\n"
        "backend condor\n"
        "%wrapper container-wrapper.sh\n\n"
        "hello:\n"
        "    echo hello\n"
    )
    spec = load_spec(spec_file)
    campaign_dir = render_condor(spec, tmp_path / "campaigns")
    archived = campaign_dir / "environment" / "condor-wrapper.sh"
    assert archived.read_text() == wrapper.read_text()
    node_script = (campaign_dir / "condor" / "yawl_0000_hello.sh").read_text()
    assert str(archived) in node_script
    render = json.loads((campaign_dir / "condor" / "render.json").read_text())
    assert render["wrapper"]["source"] == str(wrapper)
    assert render["wrapper"]["path"] == str(archived)
    assert len(render["wrapper"]["sha256"]) == 64
