import json
from pathlib import Path

from yall_run.cli import main
from yall_run.execution_trace import parse_condor_event_log
from yall_run.status_view import render_status


def _campaign(tmp_path: Path) -> Path:
    c = tmp_path / "campaign"
    (c / "condor").mkdir(parents=True)
    (c / "campaign.json").write_text(json.dumps({
        "id": "trace-test",
        "name": "trace-test",
        "backend": "condor",
        "task_order": ["bad"],
        "tasks": {
            "bad": {
                "command": ["/bin/false"],
                "parents": [],
                "resources": {},
                "retries": 2,
                "startup_retries": 2,
            }
        },
    }))
    (c / "condor" / "submit.json").write_text(json.dumps({"cluster_id": 100}))
    return c


def _attempt(
    c: Path,
    number: int,
    *,
    job_id: str,
    dagman: int,
    starts: int,
    dag_retry: int = 0,
    state: str = "failed",
    returncode: int = 17,
) -> None:
    directory = c / f"bad_attempt_{number:03d}"
    directory.mkdir()
    (directory / "attempt.json").write_text(json.dumps({
        "task": "bad",
        "attempt": number,
        "state": state,
        "returncode": returncode,
        "command_returncode": returncode,
        "failure": {"kind": "command_failed", "returncode": returncode}
        if returncode else None,
    }))
    (directory / "provenance.json").write_text(json.dumps({
        "scheduler": {
            "backend": "condor",
            "job_id": job_id,
            "dagman_job_id": dagman,
            "dag_retry": dag_retry,
            "num_job_starts": starts,
        },
        "task": {"amendments": []},
    }))


def _data(*, attempts: int = 1, state: str = "failed", history=None):
    return {
        "id": "trace-test",
        "backend": "condor",
        "tasks": [{"name": "bad", "state": state, "attempts": attempts}],
        "scheduler": {
            "query_ok": True,
            "backend": "condor",
            "cluster_id": 100,
            "counts": {},
            "dagman": None,
            "nodes": {},
            "active_jobs": {},
        },
        "scheduler_history": {
            "query_ok": True,
            "jobs": history or [],
        },
    }


def test_vvvv_reconstructs_multiple_starts_of_one_condor_job(tmp_path):
    c = _campaign(tmp_path)
    (c / "condor" / "events.log").write_text(
        "000 (101.000.000) 09/17 10:00:00 Job submitted from host: <submit>\n"
        "...\n"
        "001 (101.000.000) 09/17 10:01:00 Job executing on host: <slot1@node17>\n"
        "...\n"
        "005 (101.000.000) 09/17 10:01:02 Job terminated.\n"
        "    (1) Normal termination (return value 100)\n"
        "...\n"
        "001 (101.000.000) 09/17 10:02:00 Job executing on host: <slot1@node22>\n"
        "...\n"
        "004 (101.000.000) 09/17 10:02:20 Job was evicted.\n"
        "    evicted by policy\n"
        "...\n"
        "001 (101.000.000) 09/17 10:03:00 Job executing on host: <slot1@node31>\n"
        "...\n"
        "005 (101.000.000) 09/17 10:03:20 Job terminated.\n"
        "    (1) Normal termination (return value 101)\n"
        "...\n"
    )
    _attempt(c, 1, job_id="101.0", dagman=100, starts=3)
    data = _data(history=[{
        "job_id": "101.0",
        "task": "bad",
        "state": "completed",
        "dagman_job_id": 100,
        "dag_retry": 0,
        "num_job_starts": 3,
        "exit_code": 101,
    }])

    text = render_status(c, data, verbosity=4)
    assert "Execution trace:" in text
    assert "generation 0 initial DAGMan=100" in text
    assert "bad job=101.0 dag-retry=0 NumJobStarts=3" in text
    assert "start 1 09/17 10:01:00 host=slot1@node17" in text
    assert "startup failure before Yall payload marker exit=100" in text
    assert "start 2 09/17 10:02:00 host=slot1@node22" in text
    assert "scheduler eviction" in text
    assert "start 3 09/17 10:03:00 host=slot1@node31" in text
    assert "Yall attempt 1 state=failed" in text
    assert "association=direct" in text
    assert "Yall worker failure after startup classification exit=101" in text


def test_vvvv_groups_resume_generation_and_reason(tmp_path):
    c = _campaign(tmp_path)
    (c / "condor" / "events.log").write_text(
        "000 (101.000.000) 09/17 10:00:00 submitted\n...\n"
        "001 (101.000.000) 09/17 10:01:00 Job executing on host: <slot@old>\n...\n"
        "005 (101.000.000) 09/17 10:01:10 Job terminated.\n"
        "    (1) Normal termination (return value 101)\n...\n"
    )
    resume = c / "resumes" / "0001"
    (resume / "condor").mkdir(parents=True)
    (resume / "condor" / "submit.json").write_text(json.dumps({"cluster_id": 200}))
    (resume / "resume.json").write_text(json.dumps({
        "status": "submitted",
        "reason": "Created missing output directory",
        "selected": ["bad"],
    }))
    (resume / "condor" / "events.log").write_text(
        "000 (201.000.000) 09/17 10:10:00 submitted\n...\n"
        "001 (201.000.000) 09/17 10:11:00 Job executing on host: <slot@new>\n...\n"
        "005 (201.000.000) 09/17 10:11:10 Job terminated.\n"
        "    (1) Normal termination (return value 0)\n...\n"
    )
    _attempt(c, 1, job_id="101.0", dagman=100, starts=1)
    _attempt(
        c, 2, job_id="201.0", dagman=200, starts=1,
        state="completed", returncode=0,
    )
    data = _data(attempts=2, state="completed", history=[
        {"job_id": "101.0", "task": "bad", "state": "completed",
         "dagman_job_id": 100, "dag_retry": 0, "num_job_starts": 1},
        {"job_id": "201.0", "task": "bad", "state": "completed",
         "dagman_job_id": 200, "dag_retry": 0, "num_job_starts": 1},
    ])

    text = render_status(c, data, verbosity=4)
    assert "generation 1 resume 0001 DAGMan=200 reason=Created missing output directory" in text
    assert "bad job=201.0 dag-retry=0 NumJobStarts=1" in text
    assert "Yall attempt 2 state=completed returncode=0 command_returncode=0 association=direct" in text


def test_incomplete_event_log_does_not_invent_absolute_start_number(tmp_path):
    c = _campaign(tmp_path)
    (c / "condor" / "events.log").write_text(
        "001 (101.000.000) 09/17 10:03:00 Job executing on host: <slot@node31>\n"
        "...\n"
        "005 (101.000.000) 09/17 10:03:20 Job terminated.\n"
        "    (1) Normal termination (return value 101)\n"
        "...\n"
    )
    _attempt(c, 1, job_id="101.0", dagman=100, starts=3)
    data = _data(history=[{
        "job_id": "101.0", "task": "bad", "state": "completed",
        "dagman_job_id": 100, "dag_retry": 0, "num_job_starts": 3,
    }])

    text = render_status(c, data, verbosity=4)
    assert "observed start 1 09/17 10:03:00" in text
    assert "start 3 " not in text
    assert "execution=unknown (job matched; start association unavailable)" in text
    assert "association=direct" not in text


def test_malformed_event_log_is_diagnostic_not_fatal(tmp_path):
    c = _campaign(tmp_path)
    (c / "condor" / "events.log").write_text("not an event\n...\n")
    _attempt(c, 1, job_id="101.0", dagman=100, starts=1)
    data = _data(history=[{
        "job_id": "101.0", "task": "bad", "state": "completed",
        "dagman_job_id": 100, "dag_retry": 0, "num_job_starts": 1,
    }])
    text = render_status(c, data, verbosity=4)
    assert "parse warning:" in text
    assert "no parseable scheduler events for the jobs above" in text
    assert "execution=unknown" in text


def test_event_parser_extracts_disconnect_reconnect_and_signal(tmp_path):
    log = tmp_path / "events.log"
    log.write_text(
        "000 (7.000.000) 09/17 09:00:00 submitted\n...\n"
        "001 (7.000.000) 09/17 09:01:00 Job executing on host: <slot@node>\n...\n"
        "022 (7.000.000) 09/17 09:02:00 Job disconnected.\n...\n"
        "023 (7.000.000) 09/17 09:02:05 Job reconnected.\n...\n"
        "005 (7.000.000) 09/17 09:03:00 Job terminated.\n"
        "    (0) Abnormal termination (signal 9)\n...\n"
    )
    parsed = parse_condor_event_log(log)
    assert parsed["read_ok"] is True
    assert [event["code"] for event in parsed["events"]] == [0, 1, 22, 23, 5]
    assert parsed["events"][1]["host"] == "slot@node"
    assert parsed["events"][-1]["exit_signal"] == 9


def test_cli_accepts_vvvv_and_rejects_five_vs(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "Yallfile").write_text(
        "campaign v4-local\nbackend local\n\none:\n    /bin/true\n"
    )
    assert main(["create", "--campaigns-dir", "campaigns"]) == 0
    campaign = Path(capsys.readouterr().out.strip())
    assert main(["start", str(campaign)]) == 0
    capsys.readouterr()

    assert main(["status", str(campaign), "-vvvv"]) == 0
    text = capsys.readouterr().out
    assert "Execution trace: detailed scheduler execution trace is currently Condor-only" in text

    assert main(["status", str(campaign), "-vvvvv"]) == 2
    assert "at most -vvvv" in capsys.readouterr().err



def test_parse_gap_prevents_direct_attempt_to_start_association(tmp_path):
    c = _campaign(tmp_path)
    (c / "condor" / "events.log").write_text(
        "000 (101.000.000) 09/17 10:00:00 submitted\n...\n"
        "001 (101.000.000) 09/17 10:01:00 Job executing on host: <slot@one>\n...\n"
        "this event is damaged\n...\n"
        "005 (101.000.000) 09/17 10:01:10 Job terminated.\n"
        "    (1) Normal termination (return value 101)\n...\n"
    )
    _attempt(c, 1, job_id="101.0", dagman=100, starts=1)
    data = _data(history=[{
        "job_id": "101.0", "task": "bad", "state": "completed",
        "dagman_job_id": 100, "dag_retry": 0, "num_job_starts": 1,
    }])
    text = render_status(c, data, verbosity=4)
    assert "parse warning:" in text
    assert "observed start 1 09/17 10:01:00" in text
    assert "association=direct" not in text
    assert "execution=unknown (job matched; start association unavailable)" in text


def test_event_log_tail_is_bounded_and_labeled_partial(tmp_path, monkeypatch):
    import yall_run.execution_trace as trace

    monkeypatch.setattr(trace, "MAX_EVENT_LOG_BYTES", 512)
    log = tmp_path / "events.log"
    prefix = (
        "000 (9.000.000) 09/17 08:00:00 submitted\n...\n"
        + "x" * 1200
        + "\n...\n"
    )
    suffix = (
        "001 (9.000.000) 09/17 09:00:00 Job executing on host: <slot@tail>\n...\n"
        "005 (9.000.000) 09/17 09:00:05 Job terminated.\n"
        "    (1) Normal termination (return value 0)\n...\n"
    )
    log.write_text(prefix + suffix)
    parsed = parse_condor_event_log(log)
    assert parsed["read_ok"] is True
    assert parsed["truncated"] is True
    assert [event["code"] for event in parsed["events"]] == [1, 5]
