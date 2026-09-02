import json

from yawl_run.backend import render_condor
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
