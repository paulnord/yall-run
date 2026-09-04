import json
import subprocess

from yall_run.model import load_spec
from yall_run.pbs_backend import pbs_queue_status, render_pbs, submit_pbs


def test_pbs_render_submit_dependencies_and_status(tmp_path, monkeypatch):
    spec_file = tmp_path / "Yallfile"
    spec_file.write_text(
        "campaign pbs-test\n"
        "backend pbs\n"
        "%cpus 1\n"
        "%memory 2GB\n"
        "%disk 3GB\n\n"
        "first:\n"
        "    %retry 1\n"
        "    %cpus 4\n"
        "    %memory 8GB\n"
        "    echo first\n\n"
        "second: first\n"
        "    echo second\n"
    )
    spec = load_spec(spec_file)
    assert spec.backend == "pbs"
    campaign_dir = render_pbs(spec, tmp_path / "campaigns")

    first_script = (campaign_dir / "pbs" / "yall_0000_first.sh").read_text()
    assert "#PBS -l select=1:ncpus=4:mem=8gb" in first_script
    assert "#PBS -V" in first_script
    assert "max_attempts=2" in first_script
    assert "no portable PBS disk request" in first_script
    assert not (campaign_dir / "start.json").exists()

    calls = []
    next_job = iter(("301.server", "302.server"))

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[0] == "qsub":
            job = next(next_job)
            return subprocess.CompletedProcess(command, 0, stdout=job + "\n", stderr="")
        if command[0] == "qrls":
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[0] == "qstat":
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    "Job Id: 301.server\n"
                    "    job_state = R\n"
                    "Job Id: 302.server\n"
                    "    job_state = Q\n"
                ),
                stderr="",
            )
        raise AssertionError(command)

    monkeypatch.setattr("yall_run.pbs_backend.subprocess.run", fake_run)
    submit_pbs(campaign_dir)

    assert calls[0] == ["qsub", "-h", "yall_0000_first.sh"]
    assert calls[1] == [
        "qsub",
        "-h",
        "-W",
        "depend=afterok:301.server",
        "yall_0001_second.sh",
    ]
    assert calls[2] == ["qrls", "301.server", "302.server"]

    submit = json.loads((campaign_dir / "pbs" / "submit.json").read_text())
    assert submit["returncode"] == 0
    assert submit["jobs"] == {"first": "301.server", "second": "302.server"}
    assert (campaign_dir / "start.json").is_file()

    status = pbs_queue_status(campaign_dir)
    assert status is not None
    assert status["nodes"]["first"]["state"] == "running"
    assert status["nodes"]["second"]["state"] == "idle"
    assert status["counts"] == {"running": 1, "idle": 1}


def test_pbs_submission_failure_cancels_held_jobs(tmp_path, monkeypatch):
    spec_file = tmp_path / "Yallfile"
    spec_file.write_text(
        "campaign pbs-fail\n"
        "backend pbs\n\n"
        "first:\n"
        "    echo first\n\n"
        "second: first\n"
        "    echo second\n"
    )
    campaign_dir = render_pbs(load_spec(spec_file), tmp_path / "campaigns")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        qsub_count = len([c for c in calls if c[0] == "qsub"])
        if command[0] == "qsub" and qsub_count == 1:
            return subprocess.CompletedProcess(command, 0, stdout="401.server\n", stderr="")
        if command[0] == "qsub":
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="boom")
        if command[0] == "qdel":
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr("yall_run.pbs_backend.subprocess.run", fake_run)
    try:
        submit_pbs(campaign_dir)
    except RuntimeError as exc:
        assert "boom" in str(exc)
    else:
        raise AssertionError("expected submission failure")

    assert ["qdel", "401.server"] in calls
    assert not (campaign_dir / "start.json").exists()
