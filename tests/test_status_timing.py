"""Default status keeps useful, correctly attributed timing after jobs leave a queue."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shlex
import sys

import pytest

from yall_run.cli import main
from yall_run.status_view import render_status


def _campaign(tmp_path, *, state="completed", attempts=1):
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    (campaign / "campaign.json").write_text(json.dumps({
        "id": "status-timing", "name": "status-timing", "backend": "condor",
        "tasks": {"fit": {"command": ["/bin/true"], "resources": {}}},
        "task_order": ["fit"],
    }))
    data = {
        "id": "status-timing", "backend": "condor",
        "tasks": [{"name": "fit", "state": state, "attempts": attempts}],
        "scheduler": {
            "query_ok": True, "cluster_id": 99, "dagman": None,
            "counts": {}, "nodes": {},
        },
    }
    return campaign, data


def _record(campaign, *, state="completed", timing=None, scheduler=None, started_at=None):
    directory = campaign / "fit_attempt_001"
    directory.mkdir()
    provenance = directory / "provenance.json"
    provenance.write_text(json.dumps({"scheduler": scheduler or {}}))
    attempt = {
        "task": "fit", "attempt": 1, "state": state,
        "returncode": 0 if state == "completed" else 7,
        "provenance": str(provenance),
    }
    if timing is not None:
        attempt["timing"] = timing
    if started_at is not None:
        attempt["started_at"] = started_at
    path = directory / "attempt.json"
    path.write_text(json.dumps(attempt))
    return path, provenance


def _row(campaign, data):
    return next(line for line in render_status(campaign, data).splitlines()
                if line.lstrip().startswith("fit "))


def _scheduler(**changes):
    result = {
        "backend": "condor", "job_id": "100.0", "num_job_starts": 1,
        "qdate": 1700000000, "job_start_date": 1700003907,
    }
    result.update(changes)
    return result


@pytest.mark.parametrize("state,exit_code", [("completed", 0), ("failed", 7)])
def test_finished_row_keeps_saved_timing_job_and_queue_after_queue_empties(
        tmp_path, state, exit_code):
    campaign, data = _campaign(tmp_path, state=state)
    _record(campaign, state=state, timing={
        "real_seconds": 3661.0, "user_seconds": 119.0, "sys_seconds": 2.0,
    }, scheduler=_scheduler())

    row = _row(campaign, data)
    assert "wall=01:01:01" in row
    assert "cpu=00:02:01" in row
    assert "queue=01:05:07" in row
    assert f"exit={exit_code}" in row
    assert "job=100.0" in row
    assert "elapsed=" not in row


@pytest.mark.parametrize("changes", [
    {"num_job_starts": 2},
    {"num_job_starts": None},
    {"num_job_starts": "1"},
    {"num_job_starts": True},
    {"num_job_starts": False},
    {"num_job_starts": 1.0},
    {"num_job_starts": 0},
    {"num_job_starts": 0, "job_current_start_date": 1700004000},
    {"num_job_starts": 0, "job_current_start_date": 0},
    {"num_job_starts": 0, "job_current_start_date": "1700003907"},
    {"num_job_starts": 1, "job_current_start_date": 1700004000},
    {"num_job_starts": 1, "job_current_start_date": True},
    {"num_job_starts": 1, "job_current_start_date": "1700003907"},
    {"num_job_starts": 2, "job_current_start_date": 1700003907},
    {"qdate": None},
    {"qdate": 0},
    {"qdate": -1},
    {"qdate": "1700000000"},
    {"qdate": True},
    {"qdate": float("nan")},
    {"job_start_date": 1699999999},
    {"job_start_date": float("inf")},
])
def test_queue_duration_requires_unambiguous_valid_first_start(tmp_path, changes):
    campaign, data = _campaign(tmp_path)
    _record(campaign, timing={"real_seconds": 60}, scheduler=_scheduler(**changes))
    row = _row(campaign, data)
    assert "wall=00:01:00" in row
    assert "job=100.0" in row
    assert "queue=" not in row


@pytest.mark.parametrize("num_starts", [0, 1])
def test_equal_current_and_first_start_supports_initial_condor_snapshot(tmp_path, num_starts):
    campaign, data = _campaign(tmp_path)
    # BNL's worker-side ad can still report zero starts during its first run.
    _record(campaign, scheduler=_scheduler(
        num_job_starts=num_starts, qdate=1790023784,
        job_start_date=1790023785, job_current_start_date=1790023785,
    ))
    assert "queue=00:00:01" in _row(campaign, data)


def test_zero_queue_duration_is_known_not_missing(tmp_path):
    campaign, data = _campaign(tmp_path)
    _record(campaign, scheduler=_scheduler(job_start_date=1700000000))
    assert "queue=00:00:00" in _row(campaign, data)


@pytest.mark.parametrize("timing", [
    {"real_seconds": -1, "user_seconds": 4},
    {"real_seconds": float("nan"), "sys_seconds": 2},
    {"real_seconds": float("inf"), "user_seconds": 4, "sys_seconds": -1},
    {"real_seconds": "42", "user_seconds": 4, "sys_seconds": "2"},
    {"real_seconds": True, "user_seconds": False, "sys_seconds": 2},
    [],
])
def test_invalid_or_incomplete_measurements_are_omitted(tmp_path, timing):
    campaign, data = _campaign(tmp_path)
    _record(campaign, timing=timing)
    row = _row(campaign, data)
    assert "wall=" not in row
    assert "cpu=" not in row
    assert "exit=0" in row


def test_zero_measurements_and_more_than_one_day_are_displayed(tmp_path):
    campaign, data = _campaign(tmp_path)
    _record(campaign, timing={
        "real_seconds": 90061, "user_seconds": 0, "sys_seconds": 0,
    })
    row = _row(campaign, data)
    assert "wall=25:01:01" in row
    assert "cpu=00:00:00" in row


def test_running_attempt_has_elapsed_time_from_aware_worker_start(tmp_path):
    campaign, data = _campaign(tmp_path, state="running")
    _record(campaign, state="running", started_at=(
        datetime.now(timezone(timedelta(hours=-5))) - timedelta(minutes=10)
    ).isoformat(), scheduler=_scheduler())
    row = _row(campaign, data)
    match = re.search(r"elapsed=(\d+):(\d{2}):(\d{2})", row)
    assert match, row
    seconds = sum(int(value) * scale for value, scale in zip(match.groups(), (3600, 60, 1)))
    assert 600 <= seconds < 660
    assert "wall=" not in row
    assert "cpu=" not in row
    assert "exit=" not in row


@pytest.mark.parametrize("started_at", [
    "2026-01-01T01:02:03", "not-a-date", None, [],
    "9999-01-01T00:00:00+00:00",
])
def test_running_elapsed_requires_valid_nonfuture_aware_timestamp(tmp_path, started_at):
    campaign, data = _campaign(tmp_path, state="running")
    _record(campaign, state="running", started_at=started_at)
    assert "elapsed=" not in _row(campaign, data)


def test_pending_task_does_not_invent_job_or_timing(tmp_path):
    campaign, data = _campaign(tmp_path, state="pending", attempts=0)
    row = _row(campaign, data)
    for field in ("wall=", "cpu=", "queue=", "elapsed=", "exit=", "job="):
        assert field not in row


def test_default_row_uses_latest_attempt_without_summing_retries(tmp_path):
    campaign, data = _campaign(tmp_path, attempts=2)
    attempt, provenance = _record(campaign, state="failed", timing={
        "real_seconds": 3600, "user_seconds": 2000, "sys_seconds": 100,
    }, scheduler=_scheduler())
    latest = campaign / "fit_attempt_002"
    latest.mkdir()
    record = json.loads(attempt.read_text())
    record.update(attempt=2, state="completed", returncode=0,
                  timing={"real_seconds": 61, "user_seconds": 30, "sys_seconds": 2})
    (latest / "attempt.json").write_text(json.dumps(record))
    (latest / "provenance.json").write_text(json.dumps({
        "scheduler": _scheduler(job_id="101.0"),
    }))
    row = _row(campaign, data)
    assert "attempts=2 job=101.0 wall=00:01:01 cpu=00:00:32 exit=0" in row
    assert "100.0" not in row and "01:00:00" not in row


def test_new_live_job_does_not_inherit_old_attempt_measurements(tmp_path):
    campaign, data = _campaign(tmp_path, state="queued")
    _record(campaign, timing={
        "real_seconds": 3661, "user_seconds": 119, "sys_seconds": 2,
    }, scheduler=_scheduler())
    data["scheduler"]["nodes"]["fit"] = {"state": "idle", "job_id": "200.0"}
    row = _row(campaign, data)
    assert "condor=idle job=200.0" in row
    assert "100.0" not in row
    for field in ("wall=", "cpu=", "queue=", "elapsed=", "exit="):
        assert field not in row


def test_matching_live_job_keeps_completed_measurements_without_duplicate_job(tmp_path):
    campaign, data = _campaign(tmp_path)
    _record(campaign, timing={"real_seconds": 61}, scheduler=_scheduler())
    data["scheduler"]["nodes"]["fit"] = {"state": "completed", "job_id": "100.0"}
    row = _row(campaign, data)
    assert "wall=00:01:01" in row
    assert "exit=0" in row
    assert row.count("job=100.0") == 1


@pytest.mark.parametrize("saved_state", ["completed", "running"])
def test_same_job_reexecution_does_not_reuse_previous_execution_measurements(tmp_path, saved_state):
    campaign, data = _campaign(tmp_path, state="running")
    _record(campaign, state=saved_state, timing={
        "real_seconds": 3661, "user_seconds": 119, "sys_seconds": 2,
    }, started_at=(datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
        scheduler=_scheduler(job_current_start_date=1700003907))
    data["scheduler"]["nodes"]["fit"] = {
        "state": "running", "job_id": "100.0", "num_job_starts": 2,
        "job_start_date": 1700003907, "job_current_start_date": 1700004000,
    }
    row = _row(campaign, data)
    assert "condor=running job=100.0" in row
    for field in ("wall=", "cpu=", "queue=", "elapsed=", "exit="):
        assert field not in row


@pytest.mark.parametrize("bad_content", [None, "not json", "[]", "null"])
def test_missing_or_corrupt_attempt_still_renders_state(tmp_path, bad_content):
    campaign, data = _campaign(tmp_path)
    path, _ = _record(campaign, timing={"real_seconds": 60})
    if bad_content is None:
        path.unlink()
    else:
        path.write_text(bad_content)
    row = _row(campaign, data)
    assert "completed" in row and "attempts=1" in row
    assert "wall=" not in row
    assert "exit=" not in row


@pytest.mark.parametrize("bad_content", [None, "not json", "[]", "null"])
def test_missing_or_corrupt_provenance_does_not_hide_valid_walltime(tmp_path, bad_content):
    campaign, data = _campaign(tmp_path)
    _, path = _record(campaign, timing={"real_seconds": 60}, scheduler=_scheduler())
    if bad_content is None:
        path.unlink()
    else:
        path.write_text(bad_content)
    row = _row(campaign, data)
    assert "wall=00:01:00" in row
    assert "exit=0" in row
    assert "job=" not in row
    assert "queue=" not in row


def test_local_cli_default_shows_finished_timing_without_changing_json(
        tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "Yallfile").write_text(
        "campaign timing-local\nbackend local\n\nhello:\n    "
        + shlex.join([sys.executable, "-c", "pass"]) + "\n"
    )
    assert main(["create", "--campaigns-dir", "campaigns"]) == 0
    campaign = Path(capsys.readouterr().out.strip())
    assert main(["start", str(campaign)]) == 0
    capsys.readouterr()
    assert main(["status", str(campaign)]) == 0
    output = capsys.readouterr().out
    assert "completed" in output
    assert re.search(r"wall=\d+:\d{2}:\d{2}", output)
    if hasattr(os, "wait4"):
        assert re.search(r"cpu=\d+:\d{2}:\d{2}", output)
    assert "exit=0" in output
    assert "queue=" not in output
    assert "job=" not in output
    assert "Diagnostics:" not in output

    assert main(["status", str(campaign), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["tasks"] == [{
        "name": "hello", "state": "completed", "attempts": 1, "parents": [],
    }]
