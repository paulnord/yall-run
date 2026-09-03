import json
from pathlib import Path
import shlex
import sys

import pytest

from yawl_run.campaign import (
    campaign_status,
    create_campaign,
    resume_local,
    retry_task,
    start_local,
)
from yawl_run.model import load_spec


def _recoverable_campaign(tmp_path: Path) -> Path:
    unstable = tmp_path / "unstable.py"
    unstable.write_text(
        "from pathlib import Path\n"
        "count = Path('unstable.count')\n"
        "n = int(count.read_text()) + 1 if count.exists() else 1\n"
        "count.write_text(str(n))\n"
        "raise SystemExit(0 if n >= 2 else 7)\n"
    )
    yawlfile = tmp_path / "Yawlfile"
    yawlfile.write_text(
        "campaign recovery\n"
        "backend local\n\n"
        "prepare:\n"
        f"    {shlex.quote(sys.executable)} -c "
        + shlex.quote("from pathlib import Path; Path('prepare.count').write_text('1')")
        + "\n\n"
        "unstable: prepare\n"
        f"    {shlex.quote(sys.executable)} {shlex.quote(str(unstable))}\n\n"
        "finish: unstable\n"
        f"    {shlex.quote(sys.executable)} -c "
        + shlex.quote("from pathlib import Path; Path('finished.txt').write_text('done\\n')")
        + "\n"
    )
    return create_campaign(
        load_spec(yawlfile), tmp_path / "campaigns", backend="local", local_jobs=2
    )


def test_retry_is_one_more_attempt_of_failed_local_task(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    campaign_dir = _recoverable_campaign(tmp_path)

    with pytest.raises(RuntimeError, match="local campaign failed"):
        start_local(campaign_dir)

    before = {item["name"]: item for item in campaign_status(campaign_dir)["tasks"]}
    assert before["prepare"]["state"] == "completed"
    assert before["prepare"]["attempts"] == 1
    assert before["unstable"]["state"] == "failed"
    assert before["unstable"]["attempts"] == 1
    assert before["finish"]["state"] == "blocked"
    assert before["finish"]["attempts"] == 0

    assert retry_task(campaign_dir, "unstable") == 0
    after = {item["name"]: item for item in campaign_status(campaign_dir)["tasks"]}
    assert after["unstable"]["state"] == "completed"
    assert after["unstable"]["attempts"] == 2
    assert after["finish"]["state"] == "blocked"
    assert not (tmp_path / "finished.txt").exists()


def test_retry_rejects_completed_task(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yawlfile = tmp_path / "Yawlfile"
    yawlfile.write_text(
        "campaign completed-retry\n\n"
        "work:\n"
        "    echo done\n"
    )
    campaign_dir = create_campaign(load_spec(yawlfile), tmp_path / "campaigns")
    start_local(campaign_dir)

    with pytest.raises(ValueError, match="not failed"):
        retry_task(campaign_dir, "work")
    assert campaign_status(campaign_dir)["tasks"][0]["attempts"] == 1


def test_retry_rejects_unstarted_campaign(tmp_path):
    yawlfile = tmp_path / "Yawlfile"
    yawlfile.write_text(
        "campaign unstarted-retry\n\n"
        "work:\n"
        "    ! exit 1\n"
    )
    campaign_dir = create_campaign(load_spec(yawlfile), tmp_path / "campaigns")

    with pytest.raises(ValueError, match="before the campaign has been started"):
        retry_task(campaign_dir, "work")
    assert not list(campaign_dir.glob("*_attempt_*"))


def test_resume_preserves_completed_and_continues_graph(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    campaign_dir = _recoverable_campaign(tmp_path)

    with pytest.raises(RuntimeError, match="local campaign failed"):
        start_local(campaign_dir)
    capsys.readouterr()

    resume_local(campaign_dir)
    output = capsys.readouterr().out
    assert "mode=resume" in output
    assert "[start] prepare" not in output
    assert "[start] unstable" in output
    assert "[start] finish" in output

    tasks = {item["name"]: item for item in campaign_status(campaign_dir)["tasks"]}
    assert tasks["prepare"]["attempts"] == 1
    assert tasks["unstable"]["attempts"] == 2
    assert tasks["finish"]["attempts"] == 1
    assert all(item["state"] == "completed" for item in tasks.values())
    assert (tmp_path / "finished.txt").read_text() == "done\n"

    record = json.loads((campaign_dir / "resumes" / "resume_001.json").read_text())
    assert record["initial_counts"] == {"blocked": 1, "completed": 1, "failed": 1}
    assert record["final_counts"] == {"completed": 3}
    assert record["result"] == "completed"


def test_resume_reuses_frozen_local_concurrency(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    campaign_dir = _recoverable_campaign(tmp_path)

    with pytest.raises(RuntimeError):
        start_local(campaign_dir)
    capsys.readouterr()
    resume_local(campaign_dir)
    output = capsys.readouterr().out
    assert "jobs=2" in output
    assert "mode=resume" in output


def test_resume_rejects_unstarted_campaign(tmp_path):
    yawlfile = tmp_path / "Yawlfile"
    yawlfile.write_text(
        "campaign unstarted-resume\n\n"
        "work:\n"
        "    echo done\n"
    )
    campaign_dir = create_campaign(load_spec(yawlfile), tmp_path / "campaigns")

    with pytest.raises(ValueError, match="has not been started"):
        resume_local(campaign_dir)


def test_resume_refuses_recorded_running_task(tmp_path):
    yawlfile = tmp_path / "Yawlfile"
    yawlfile.write_text(
        "campaign running-resume\n\n"
        "work:\n"
        "    echo done\n"
    )
    campaign_dir = create_campaign(load_spec(yawlfile), tmp_path / "campaigns")
    (campaign_dir / "start.json").write_text(
        json.dumps({"started_at": "test", "backend": "local", "overwrite": False})
    )
    (campaign_dir / "state" / "work.json").write_text(
        json.dumps({"state": "running", "attempts": 1})
    )

    with pytest.raises(ValueError, match="recorded as running"):
        resume_local(campaign_dir)
    assert not (campaign_dir / "resumes").exists()
