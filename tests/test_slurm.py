import json
import subprocess

from yall_run.model import load_spec
from yall_run.slurm_backend import render_slurm, slurm_queue_status, submit_slurm


def test_slurm_render_submit_dependencies_and_status(tmp_path, monkeypatch):
    spec_file = tmp_path / "Yallfile"
    spec_file.write_text(
        "campaign slurm-test\n"
        "backend slurm\n"
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
    assert spec.backend == "slurm"
    campaign_dir = render_slurm(spec, tmp_path / "campaigns")

    first_script = (campaign_dir / "slurm" / "yall_0000_first.sh").read_text()
    assert "#SBATCH --cpus-per-task=4" in first_script
    assert "#SBATCH --mem=8G" in first_script
    assert "max_attempts=2" in first_script
    assert "no portable Slurm disk request" in first_script
    assert not (campaign_dir / "start.json").exists()

    calls = []
    next_job = iter(("101", "102"))

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[0] == "sbatch":
            job = next(next_job)
            return subprocess.CompletedProcess(command, 0, stdout=job + "\n", stderr="")
        if command[0] == "scontrol":
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[0] == "squeue":
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="101|RUNNING\n102|PENDING\n",
                stderr="",
            )
        raise AssertionError(command)

    monkeypatch.setattr("yall_run.slurm_backend.subprocess.run", fake_run)
    submit_slurm(campaign_dir)

    assert calls[0] == ["sbatch", "--parsable", "--hold", "yall_0000_first.sh"]
    assert calls[1] == [
        "sbatch",
        "--parsable",
        "--hold",
        "--dependency=afterok:101",
        "yall_0001_second.sh",
    ]
    assert calls[2] == ["scontrol", "release", "101", "102"]

    submit = json.loads((campaign_dir / "slurm" / "submit.json").read_text())
    assert submit["returncode"] == 0
    assert submit["jobs"] == {"first": "101", "second": "102"}
    assert (campaign_dir / "start.json").is_file()

    status = slurm_queue_status(campaign_dir)
    assert status is not None
    assert status["nodes"]["first"]["state"] == "running"
    assert status["nodes"]["second"]["state"] == "idle"
    assert status["counts"] == {"running": 1, "idle": 1}


def test_slurm_submission_failure_cancels_held_jobs(tmp_path, monkeypatch):
    spec_file = tmp_path / "Yallfile"
    spec_file.write_text(
        "campaign slurm-fail\n"
        "backend slurm\n\n"
        "first:\n"
        "    echo first\n\n"
        "second: first\n"
        "    echo second\n"
    )
    campaign_dir = render_slurm(load_spec(spec_file), tmp_path / "campaigns")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[0] == "sbatch" and len([c for c in calls if c[0] == "sbatch"]) == 1:
            return subprocess.CompletedProcess(command, 0, stdout="201\n", stderr="")
        if command[0] == "sbatch":
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="boom")
        if command[0] == "scancel":
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr("yall_run.slurm_backend.subprocess.run", fake_run)
    try:
        submit_slurm(campaign_dir)
    except RuntimeError as exc:
        assert "boom" in str(exc)
    else:
        raise AssertionError("expected submission failure")

    assert ["scancel", "201"] in calls
    assert not (campaign_dir / "start.json").exists()
