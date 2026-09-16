import hashlib
import json
import subprocess

import pytest

from yall_run.condor_backend import render_condor
from yall_run.model import load_spec


def test_condor_startup_retry_default_is_separate_from_payload_retry(tmp_path):
    spec_file = tmp_path / "Yallfile"
    spec_file.write_text(
        "campaign startup-retry\n"
        "backend condor\n\n"
        "one:\n"
        "    %retry 3\n"
        "    echo one\n"
    )

    spec = load_spec(spec_file)
    task = spec.tasks[0]
    assert task.retries == 3
    assert task.startup_retries == 2

    campaign_dir = render_condor(spec, tmp_path / "campaigns")
    submit = (campaign_dir / "condor" / "yall_0000_one.sub").read_text()
    dag = (campaign_dir / "condor" / "campaign.dag").read_text()
    launcher = (campaign_dir / "condor" / "yall_0000_one.sh").read_text()
    worker = (campaign_dir / "condor" / "yall_worker.py").read_text()
    manifest = json.loads((campaign_dir / "campaign.json").read_text())
    marker_id = hashlib.sha256(b"one").hexdigest()

    assert "max_retries = 2" in submit
    assert "retry_until = ExitCode =!= 100" in submit
    assert 'requirements = (Machine =!= split(LastRemoteHost, "@")[1])' in submit
    assert "RETRY yall_0000_one 3 UNLESS-EXIT 100" in dag
    assert f"startup/{marker_id}.started" in launcher
    assert "hashlib.sha256" in worker
    assert '"condor" / "startup"' in worker
    assert "startup failed before payload marker" in launcher
    assert "payload failed after startup" in launcher
    assert "exit 100" in launcher
    assert "exit 101" in launcher
    assert manifest["tasks"]["one"]["startup_retries"] == 2


def test_payload_failure_is_classified_after_worker_start(tmp_path):
    spec_file = tmp_path / "Yallfile"
    spec_file.write_text(
        "campaign payload-failure\n"
        "backend condor\n\n"
        "one:\n"
        "    ! exit 37\n"
    )

    campaign_dir = render_condor(load_spec(spec_file), tmp_path / "campaigns")
    launcher = campaign_dir / "condor" / "yall_0000_one.sh"
    result = subprocess.run(["bash", str(launcher)], capture_output=True, text=True)

    assert result.returncode == 101
    assert "payload failed after startup (exit=37)" in result.stderr
    attempt = json.loads((campaign_dir / "one_attempt_001" / "attempt.json").read_text())
    assert attempt["state"] == "failed"
    assert attempt["returncode"] == 37
    assert attempt["command_returncode"] == 37


def test_startup_retry_can_be_overridden_or_disabled(tmp_path):
    spec_file = tmp_path / "Yallfile"
    spec_file.write_text(
        "campaign startup-override\n"
        "backend condor\n\n"
        "enabled:\n"
        "    %startup-retry 4\n"
        "    echo enabled\n\n"
        "disabled:\n"
        "    %startup-retry 0\n"
        "    echo disabled\n"
    )

    spec = load_spec(spec_file)
    assert spec.tasks[0].startup_retries == 4
    assert spec.tasks[1].startup_retries == 0

    campaign_dir = render_condor(spec, tmp_path / "campaigns")
    enabled = (campaign_dir / "condor" / "yall_0000_enabled.sub").read_text()
    disabled = (campaign_dir / "condor" / "yall_0001_disabled.sub").read_text()

    assert "max_retries = 4" in enabled
    assert "retry_until = ExitCode =!= 100" in enabled
    assert "LastRemoteHost" in enabled
    assert "max_retries" not in disabled
    assert "retry_until" not in disabled
    assert "LastRemoteHost" not in disabled


def test_startup_retry_rejects_negative_values(tmp_path):
    spec_file = tmp_path / "Yallfile"
    spec_file.write_text(
        "campaign bad-startup-retry\n\n"
        "one:\n"
        "    %startup-retry -1\n"
        "    echo one\n"
    )

    with pytest.raises(ValueError, match=r"%startup-retry may not be negative"):
        load_spec(spec_file)
