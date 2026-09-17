import json
from pathlib import Path

from yall_run.cli import main
from yall_run.status_view import _tail, render_status


def _failed_local(tmp_path: Path, monkeypatch, capsys) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "Yallfile").write_text(
        "campaign status-verbosity\n"
        "backend local\n\n"
        "bad:\n"
        "    ! printf 'first\\ndiagnostic-line\\n' >&2; exit 7\n"
    )
    assert main(["create", "--campaigns-dir", "campaigns"]) == 0
    campaign_dir = Path(capsys.readouterr().out.strip())
    assert main(["start", str(campaign_dir)]) == 2
    capsys.readouterr()
    return campaign_dir


def test_status_default_stays_compact_and_v_explains_failure(tmp_path, monkeypatch, capsys):
    c = _failed_local(tmp_path, monkeypatch, capsys)

    assert main(["status", str(c)]) == 0
    plain = capsys.readouterr().out
    assert "bad" in plain and "failed" in plain
    assert "Diagnostics:" not in plain
    assert "diagnostic-line" not in plain

    assert main(["status", str(c), "-v"]) == 0
    verbose = capsys.readouterr().out
    assert "Diagnostics:" in verbose
    assert "returncode=7 command_returncode=7 failure=command_failed" in verbose
    assert "stderr last: diagnostic-line" in verbose
    assert "_attempt_001" in verbose
    assert "command:" not in verbose


def test_status_vv_adds_execution_context_and_bounded_logs(tmp_path, monkeypatch, capsys):
    c = _failed_local(tmp_path, monkeypatch, capsys)
    assert main(["status", str(c), "-vv"]) == 0
    text = capsys.readouterr().out
    assert "command:" in text
    assert "cwd:" in text
    assert "resources:" in text
    assert "stderr:" in text
    assert "stderr tail (2 lines):" in text
    assert "diagnostic-line" in text


def test_status_vvv_adds_attempt_and_provenance_history(tmp_path, monkeypatch, capsys):
    c = _failed_local(tmp_path, monkeypatch, capsys)
    assert main(["status", str(c), "-vvv"]) == 0
    text = capsys.readouterr().out
    assert "attempt history:" in text
    assert "1: failed returncode=7 failure=command_failed" in text
    assert "launch:" in text
    assert "provenance:" in text


def test_status_rejects_json_verbosity_and_more_than_three_vs(tmp_path, monkeypatch, capsys):
    c = _failed_local(tmp_path, monkeypatch, capsys)
    assert main(["status", str(c), "--json", "-v"]) == 2
    assert "text-only" in capsys.readouterr().err
    assert main(["status", str(c), "-vvvv"]) == 2
    assert "at most -vvv" in capsys.readouterr().err


def test_verbose_status_reports_condor_hold_reason(tmp_path):
    c = tmp_path / "campaign"
    c.mkdir()
    (c / "campaign.json").write_text(json.dumps({
        "id": "hold-test", "name": "hold-test", "backend": "condor",
        "tasks": {"bad": {"command": ["/bin/true"], "resources": {}}},
        "task_order": ["bad"],
    }))
    data = {
        "id": "hold-test", "backend": "condor",
        "tasks": [{"name": "bad", "state": "held", "attempts": 0}],
        "scheduler": {
            "query_ok": True, "backend": "condor", "cluster_id": 44,
            "counts": {"held": 1}, "dagman": "running",
            "nodes": {"bad": {
                "state": "held", "job_id": "45.0",
                "hold_reason": "Failed to open output file",
                "hold_reason_code": 7, "hold_reason_subcode": 2,
            }},
        },
    }
    text = render_status(c, data, verbosity=1)
    assert "scheduler: state=held job_id=45.0 reason=Failed to open output file hold=7/2" in text


def test_vvv_shows_recent_resume_reason(tmp_path):
    c = tmp_path / "campaign"
    (c / "resumes" / "0001").mkdir(parents=True)
    (c / "campaign.json").write_text(json.dumps({
        "id": "resume-test", "name": "resume-test", "backend": "condor",
        "tasks": {"bad": {"command": ["/bin/false"], "resources": {}}},
        "task_order": ["bad"],
    }))
    (c / "resumes" / "0001" / "resume.json").write_text(json.dumps({
        "status": "submitted", "reason": "Created missing output directory",
        "selected": ["bad"],
    }))
    data = {
        "id": "resume-test", "backend": "condor",
        "tasks": [{"name": "bad", "state": "failed", "attempts": 0}],
        "scheduler": {"query_ok": True, "backend": "condor", "cluster_id": 44,
                      "counts": {}, "dagman": None, "nodes": {}},
    }
    text = render_status(c, data, verbosity=3)
    assert "Recent recovery rounds:" in text
    assert "reason=Created missing output directory" in text



def test_log_tail_does_not_use_path_read_text(tmp_path, monkeypatch):
    log = tmp_path / "huge.log"
    log.write_bytes(b"x" * (1024 * 1024) + b"\nlast-one\nlast-two\n")
    original = Path.read_text

    def guarded(self, *args, **kwargs):
        if self == log:
            raise AssertionError("tail must not load the whole log with Path.read_text")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded)
    assert _tail(log, 2) == ["last-one", "last-two"]


def test_v_explains_blocked_parent_and_scheduler_query_failure(tmp_path):
    c = tmp_path / "campaign"
    c.mkdir()
    (c / "campaign.json").write_text(json.dumps({
        "id": "blocked-test", "name": "blocked-test", "backend": "condor",
        "tasks": {
            "parent": {"command": ["/bin/false"], "resources": {}, "parents": []},
            "child": {"command": ["/bin/true"], "resources": {}, "parents": ["parent"]},
        },
        "task_order": ["parent", "child"],
    }))
    data = {
        "id": "blocked-test", "backend": "condor",
        "tasks": [
            {"name": "parent", "state": "failed", "attempts": 0},
            {"name": "child", "state": "blocked", "attempts": 0},
        ],
        "scheduler": {
            "query_ok": False, "backend": "condor", "nodes": {}, "active_jobs": {},
            "error": "condor_q failed: collector unavailable",
        },
    }
    rendered = render_status(c, data, verbosity=1)
    assert "scheduler query: condor_q failed: collector unavailable" in rendered
    assert "parents: parent=failed" in rendered



def test_condor_history_snapshot_normalizes_old_job(tmp_path, monkeypatch):
    import subprocess
    from yall_run import recovery

    c = tmp_path / "campaign"
    (c / "condor").mkdir(parents=True)
    (c / "campaign.json").write_text(json.dumps({
        "id": "history-test", "name": "history-test", "backend": "condor",
        "tasks": {"bad": {"command": ["/bin/false"]}}, "task_order": ["bad"],
    }))
    (c / "condor" / "submit.json").write_text(json.dumps({"cluster_id": 100, "returncode": 0}))
    (c / "condor" / "render.json").write_text(json.dumps({"node_names": {"bad": "yall_0000_bad"}}))

    ads = [{
        "ClusterId": 101, "ProcId": 0, "DAGManJobId": 100,
        "DAGNodeName": "yall_0000_bad", "JobStatus": 3,
        "HoldReason": "Failed to open output file", "HoldReasonCode": 7,
        "HoldReasonSubCode": 2, "RemoveReason": "removed after hold",
        "ExitCode": 100, "EnteredCurrentStatus": 1234,
    }]

    def fake_run(argv, cwd=None):
        assert argv[0] == "condor_history"
        return subprocess.CompletedProcess(argv, 0, json.dumps(ads), "")

    monkeypatch.setattr(recovery, "_run", fake_run)
    history = recovery.condor_history_snapshot(c)
    assert history["query_ok"] is True
    assert history["jobs"] == [{
        "job_id": "101.0", "state": "removed", "task": "bad",
        "dagman_job_id": 100, "hold_reason": "Failed to open output file",
        "hold_reason_code": 7, "hold_reason_subcode": 2,
        "remove_reason": "removed after hold", "exit_code": 100,
        "entered_current_status": 1234,
    }]


def test_vvv_shows_condor_history_and_submit_artifacts_for_recovered_task(tmp_path):
    c = tmp_path / "campaign"
    condor = c / "condor"
    resume = c / "resumes" / "0001" / "condor"
    condor.mkdir(parents=True)
    resume.mkdir(parents=True)
    task_name = "final-f1"
    node = "yall_0017_final-f1"
    (c / "campaign.json").write_text(json.dumps({
        "id": "forensic-test", "name": "forensic-test", "backend": "condor",
        "tasks": {task_name: {"command": ["/bin/true"], "resources": {}}},
        "task_order": [task_name],
    }))
    (condor / "render.json").write_text(json.dumps({"node_names": {task_name: node}}))
    (condor / f"{node}.sub").write_text(
        "executable = /tmp/node.sh\n"
        "output = /tmp/old.out\nerror = /tmp/old.err\nlog = /tmp/events.log\nqueue 1\n"
    )
    (condor / "campaign.dag").write_text("JOB x x.sub\n")
    (resume / f"{node}.sub").write_text(
        "executable = /tmp/node.sh\n"
        "output = /tmp/new.out\nerror = /tmp/new.err\nlog = /tmp/new-events.log\nqueue 1\n"
    )
    (resume / "campaign.dag").write_text("JOB x x.sub\n")
    (resume / "campaign.dag.rescue001").write_text("DONE x\n")
    (resume / "submit.json").write_text("{}")

    for number, state, rc in ((1, "failed", 2), (2, "completed", 0)):
        attempt_dir = c / f"{task_name}_attempt_{number:03d}"
        attempt_dir.mkdir()
        (attempt_dir / "attempt.json").write_text(json.dumps({
            "task": task_name, "attempt": number, "state": state,
            "returncode": rc, "command_returncode": rc,
            "failure": {"kind": "command_failed", "returncode": rc} if rc else None,
            "stderr": str(attempt_dir / "stderr.log"),
        }))
        (attempt_dir / "stderr.log").write_text("old failure\n" if rc else "")
        (attempt_dir / "provenance.json").write_text(json.dumps({
            "execution": {"launch_command": ["/bin/true"]}, "task": {},
        }))

    data = {
        "id": "forensic-test", "backend": "condor",
        "tasks": [{"name": task_name, "state": "completed", "attempts": 2}],
        "scheduler": {"query_ok": True, "backend": "condor", "cluster_id": 100,
                      "counts": {}, "dagman": None, "nodes": {}, "active_jobs": {}},
        "scheduler_history": {"query_ok": True, "jobs": [{
            "job_id": "101.0", "task": task_name, "state": "removed",
            "hold_reason": "Failed to open output file", "hold_reason_code": 7,
            "hold_reason_subcode": 2, "exit_code": 100,
        }]},
    }
    text = render_status(c, data, verbosity=3)
    assert "final-f1: completed attempt=2" in text
    assert "attempt history:" in text
    assert "condor history:" in text
    assert "hold=7/2" in text
    assert "reason=Failed to open output file" in text
    assert "condor artifacts:" in text
    assert "resume 0001:" in text
    assert "output = /tmp/new.out" in text
    assert "rescue:" in text


def test_vvv_reports_condor_history_query_failure(tmp_path):
    c = tmp_path / "campaign"
    (c / "condor").mkdir(parents=True)
    (c / "campaign.json").write_text(json.dumps({
        "id": "history-fail", "name": "history-fail", "backend": "condor",
        "tasks": {"bad": {"command": ["/bin/false"], "resources": {}}},
        "task_order": ["bad"],
    }))
    (c / "condor" / "render.json").write_text(json.dumps({"node_names": {"bad": "yall_0000_bad"}}))
    data = {
        "id": "history-fail", "backend": "condor",
        "tasks": [{"name": "bad", "state": "failed", "attempts": 0}],
        "scheduler": {"query_ok": True, "backend": "condor", "cluster_id": 100,
                      "counts": {}, "dagman": None, "nodes": {}, "active_jobs": {}},
        "scheduler_history": {"query_ok": False, "jobs": [], "error": "condor_history not found"},
    }
    rendered = render_status(c, data, verbosity=3)
    assert "Condor history: unavailable (condor_history not found)" in rendered
