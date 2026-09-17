"""Recovery regression tests using recorded campaign layouts and fake schedulers."""
import json
from pathlib import Path
import subprocess

import pytest

from yall_run import recovery as r


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


@pytest.fixture
def campaign(tmp_path):
    def make(backend="condor", legacy=False):
        c = tmp_path / backend
        c.mkdir()
        tasks = {
            "prepare": {"parents": [], "command": ["/bin/true"], "inputs": [], "outputs": []},
            "convert": {"parents": ["prepare"], "command": ["/bin/true"], "inputs": [], "outputs": []},
            "analyze": {"parents": ["convert"], "command": ["/bin/true"], "inputs": [], "outputs": []},
        }
        for task in tasks.values():
            task["cwd"] = str(c)
        write(c / "campaign.json", {"id": "test-campaign", "backend": backend,
              "task_order": list(tasks), "tasks": list(tasks) if legacy else tasks})
        write(c / "start.json", {"started_at": "2026-09-16", "overwrite": False})
        states = {"prepare": {"state": "completed", "attempts": 1},
                  "convert": {"state": "running", "attempts": 1},
                  "analyze": {"state": "pending", "attempts": 0}}
        for name, state in states.items():
            if legacy:
                write(c / "tasks" / f"{name}.json", {**tasks[name], **state})
            else:
                write(c / "state" / f"{name}.json", state)
        write(c / "convert_attempt_001" / "attempt.json", {"state": "running", "attempt": 1})
        (c / "convert_attempt_001" / "stdout.log").write_text("old attempt\n")
        d = c / backend
        d.mkdir()
        (d / "logs").mkdir()
        if backend == "condor":
            names = {name: f"yall_{i:04d}_{name}" for i, name in enumerate(tasks)}
            write(d / "render.json", {"node_names": names})
            write(d / "submit.json", {"cluster_id": 100, "returncode": 0})
            dag = []
            for name, node in names.items():
                script = d / f"{node}.sh"
                script.write_text("#!/bin/bash\nexit 0\n")
                script.chmod(0o755)
                (d / f"{node}.sub").write_text(
                    f"executable = {script}\noutput = old.out\nerror = old.err\n"
                    "log = old.log\nrequest_memory = 4GB\nqueue 1\n")
                dag.append(f"JOB {node} {node}.sub")
            dag += ["PARENT yall_0000_prepare CHILD yall_0001_convert",
                    "PARENT yall_0001_convert CHILD yall_0002_analyze"]
            (d / "campaign.dag").write_text("\n".join(dag) + "\n")
            (d / "campaign.dag.rescue001").write_text("# partial rescue\nDONE yall_0000_prepare\n")
        else:
            write(d / "submit.json", {"jobs": {"prepare": "10" if backend == "slurm" else "10.server",
                                               "convert": "11" if backend == "slurm" else "11.server",
                                               "analyze": "12" if backend == "slurm" else "12.server"},
                                       "returncode": 0})
            write(d / "render.json", {"scripts": {name: name + ".sh" for name in tasks}})
            for name in tasks:
                directives = ("#SBATCH --output=old.out\n#SBATCH --error=old.err\n#SBATCH --mem=4G\n"
                              if backend == "slurm" else "#PBS -o old.out\n#PBS -e old.err\n#PBS -l select=1:ncpus=1:mem=4gb\n")
                script = d / (name + ".sh")
                script.write_text("#!/bin/bash\n" + directives + "exec /bin/true\n")
                script.chmod(0o755)
        return c
    return make


class Scheduler:
    def __init__(self, backend):
        self.backend = backend
        self.calls = []
        self.queue = "[]" if backend == "condor" else ""
        self.next_id = 200
        self.fail_query = False
        self.fail_submit_at = None
        self.submissions = 0
        self.bad_id = False
        self.cancel_takes_effect = True
        self.fail_release = False

    def __call__(self, argv, cwd=None):
        self.calls.append((argv, cwd))
        command = argv[0]
        if command in {"condor_q", "squeue", "qstat"}:
            return subprocess.CompletedProcess(argv, 1 if self.fail_query else 0,
                                               self.queue, "server unavailable" if self.fail_query else "")
        if command in {"scancel", "qdel"}:
            if self.cancel_takes_effect:
                self.queue = ""
            return subprocess.CompletedProcess(argv, 0, "", "")
        if command in {"scontrol", "qrls"}:
            return subprocess.CompletedProcess(argv, 1 if self.fail_release else 0, "", "release denied")
        if command in {"sbatch", "qsub", "condor_submit_dag"}:
            self.submissions += 1
            if self.submissions == self.fail_submit_at:
                return subprocess.CompletedProcess(argv, 1, "", "submission rejected")
            self.next_id += 1
            output = (f"1 job(s) submitted to cluster {self.next_id}.\n" if command == "condor_submit_dag"
                      else f"{self.next_id}.server\n" if command == "qsub" else f"{self.next_id}\n")
            if self.bad_id:
                output = "accepted but unparseable\n"
            return subprocess.CompletedProcess(argv, 0, output, "")
        raise AssertionError(argv)


def contents(directory):
    return {str(p.relative_to(directory)): p.read_bytes() for p in directory.rglob("*") if p.is_file()}


def status_data(c):
    _, manifest, tasks = r._load(c)
    return {"backend": manifest["backend"], "id": "test-campaign",
            "tasks": [{"name": name, **r._state(c, manifest, name)} for name in tasks]}


@pytest.mark.parametrize("backend", ["condor", "slurm", "pbs"])
def test_stale_running_becomes_interrupted_without_mutating_attempt(campaign, monkeypatch, backend):
    c = campaign(backend)
    scheduler = Scheduler(backend)
    monkeypatch.setattr(r, "_run", scheduler)
    before = contents(c)
    data = r.reconcile_status(c, status_data(c))
    assert data["tasks"][1]["state"] == "interrupted"
    assert data["tasks"][1]["recorded_state"] == "running"
    assert contents(c) == before


@pytest.mark.parametrize("backend", ["condor", "slurm", "pbs"])
def test_failed_query_is_unknown_not_empty_and_resume_fails_closed(campaign, monkeypatch, backend):
    c = campaign(backend)
    scheduler = Scheduler(backend)
    scheduler.fail_query = True
    monkeypatch.setattr(r, "_run", scheduler)
    data = r.reconcile_status(c, status_data(c))
    assert not data["scheduler"]["query_ok"]
    assert data["tasks"][1]["state"] == "unknown"
    with pytest.raises(RuntimeError, match="resume refused"):
        r.resume_campaign(c)
    assert scheduler.submissions == 0
    assert not (c / "resumes").exists()


@pytest.mark.parametrize("backend,queue", [
    ("condor", '[{"ClusterId":100,"ProcId":0,"JobStatus":2}]'),
    ("slurm", "11|RUNNING\n"),
    ("pbs", "Job Id: 11.server\n    job_state = R\n"),
])
def test_running_jobs_cannot_be_duplicated_even_with_cancel_pending(campaign, monkeypatch, backend, queue):
    c = campaign(backend)
    scheduler = Scheduler(backend)
    scheduler.queue = queue
    monkeypatch.setattr(r, "_run", scheduler)
    with pytest.raises(ValueError, match="active scheduler jobs"):
        r.resume_campaign(c, cancel_pending=True)
    assert scheduler.submissions == 0
    assert not any(a[0] in {"scancel", "qdel"} for a, _ in scheduler.calls)


@pytest.mark.parametrize("backend", ["condor", "slurm", "pbs"])
def test_dry_run_does_not_change_campaign_or_submit(campaign, monkeypatch, backend):
    c = campaign(backend)
    scheduler = Scheduler(backend)
    monkeypatch.setattr(r, "_run", scheduler)
    before = contents(c)
    assert r.resume_campaign(c, dry_run=True) == 0
    assert contents(c) == before
    assert not (c / "resumes").exists()
    assert scheduler.submissions == 0


def test_condor_uses_latest_native_rescue_and_preserves_original_evidence(campaign, monkeypatch):
    c = campaign()
    d = c / "condor"
    (d / "campaign.dag.rescue002").write_text("# latest\nDONE yall_0000_prepare\n")
    original = contents(c)
    scheduler = Scheduler("condor")
    monkeypatch.setattr(r, "_run", scheduler)
    assert r.resume_campaign(c) == 0
    rd = c / "resumes" / "0001" / "condor"
    submission = r._read(rd / "submit.json")
    assert submission["cluster_id"] == 201
    assert submission["commands"] == [["condor_submit_dag", "-dorescuefrom", "2", "campaign.dag"]]
    assert (rd / "campaign.dag.rescue002").read_bytes() == (d / "campaign.dag.rescue002").read_bytes()
    submit_text = (rd / "yall_0001_convert.sub").read_text()
    output_path = rd / "logs" / "yall_0001_convert.out"
    error_path = rd / "logs" / "yall_0001_convert.err"
    event_path = rd / "events.log"
    assert f"output = {output_path}" in submit_text
    assert f"error = {error_path}" in submit_text
    assert f"log = {event_path}" in submit_text
    assert f'output = "{output_path}"' not in submit_text
    assert f'error = "{error_path}"' not in submit_text
    assert f'log = "{event_path}"' not in submit_text
    assert "request_memory = 4GB" in submit_text
    for name, value in original.items():
        if name != "state/convert.json":
            assert (c / name).read_bytes() == value
    assert r._read(c / "state" / "convert.json")["state"] == "interrupted"
    assert r._read(c / "state" / "convert.json")["attempts"] == 1
    assert r._read(c / "resumes" / "0001" / "resume.json")["selected"] == ["convert", "analyze"]
    assert r.scheduler_snapshot(c)["cluster_id"] == 201


@pytest.mark.parametrize("backend", ["slurm", "pbs"])
def test_resubmits_only_unfinished_graph_with_new_dependencies_and_separate_logs(campaign, monkeypatch, backend):
    c = campaign(backend)
    scheduler = Scheduler(backend)
    monkeypatch.setattr(r, "_run", scheduler)
    assert r.resume_campaign(c) == 0
    commands = [a for a, _ in scheduler.calls if a[0] in {"sbatch", "qsub"}]
    assert len(commands) == 2
    assert commands[0][-1] == "convert.sh"
    assert commands[1][-1] == "analyze.sh"
    assert not any("afterok" in arg for arg in commands[0])
    dependency = "--dependency=afterok:201" if backend == "slurm" else "depend=afterok:201.server"
    assert dependency in commands[1]
    assert all(("--hold" if backend == "slurm" else "-h") in a for a in commands)
    rd = c / "resumes" / "0001" / backend
    assert str(rd / "logs") in (rd / "convert.sh").read_text()
    assert r._read(c / backend / "submit.json")["jobs"]["convert"].startswith("11")
    assert len(r._read(rd / "submit.json")["jobs"]) == 2
    assert scheduler.calls[-1][0][0] in {"scontrol", "qrls"}


@pytest.mark.parametrize("backend,queue", [
    ("slurm", "12|PENDING\n"),
    ("pbs", "Job Id: 12.server\n    job_state = H\n"),
])
def test_cancel_pending_is_explicit_and_confirmed(campaign, monkeypatch, backend, queue):
    c = campaign(backend)
    scheduler = Scheduler(backend)
    scheduler.queue = queue
    monkeypatch.setattr(r, "_run", scheduler)
    with pytest.raises(ValueError, match="active scheduler jobs"):
        r.resume_campaign(c)
    assert r.resume_campaign(c, cancel_pending=True) == 0
    cancel = [a for a, _ in scheduler.calls if a[0] in {"scancel", "qdel"}]
    assert cancel == [["scancel", "--state=PENDING", "12"]] if backend == "slurm" else cancel == [["qdel", "12.server"]]


def test_delayed_cancellation_does_not_submit_duplicates(campaign, monkeypatch):
    c = campaign("slurm")
    scheduler = Scheduler("slurm")
    scheduler.queue = "12|PENDING\n"
    scheduler.cancel_takes_effect = False
    monkeypatch.setattr(r, "_run", scheduler)
    with pytest.raises(RuntimeError, match="cancellation not yet confirmed"):
        r.resume_campaign(c, cancel_pending=True)
    assert scheduler.submissions == 0


@pytest.mark.parametrize("backend", ["slurm", "pbs"])
def test_partial_submission_records_every_acknowledged_job(campaign, monkeypatch, backend):
    c = campaign(backend)
    scheduler = Scheduler(backend)
    scheduler.fail_submit_at = 2
    monkeypatch.setattr(r, "_run", scheduler)
    with pytest.raises(RuntimeError, match="submission rejected"):
        r.resume_campaign(c)
    record = r._read(c / "resumes" / "0001" / backend / "submit.json")
    assert list(record["jobs"]) == ["convert"]
    assert not record["in_flight"]
    assert not any(a[0] in {"scontrol", "qrls"} for a, _ in scheduler.calls)


@pytest.mark.parametrize("backend", ["condor", "slurm", "pbs"])
def test_unparseable_acceptance_blocks_blind_retry(campaign, monkeypatch, backend):
    c = campaign(backend)
    scheduler = Scheduler(backend)
    scheduler.bad_id = True
    monkeypatch.setattr(r, "_run", scheduler)
    with pytest.raises(RuntimeError, match="unconfirmed"):
        r.resume_campaign(c)
    with pytest.raises(RuntimeError, match="unconfirmed scheduler response"):
        r.resume_campaign(c)
    assert scheduler.submissions == 1


def test_query_timeout_fails_closed(campaign, monkeypatch):
    c = campaign()
    def timeout(argv, cwd=None):
        raise subprocess.TimeoutExpired(argv, 60)
    monkeypatch.setattr(r, "_run", timeout)
    assert not r.scheduler_snapshot(c)["query_ok"]
    with pytest.raises(RuntimeError, match="resume refused"):
        r.resume_campaign(c)


def test_second_condor_resume_uses_latest_round_rescue(campaign, monkeypatch):
    c = campaign()
    scheduler = Scheduler("condor")
    monkeypatch.setattr(r, "_run", scheduler)
    r.resume_campaign(c)
    rd = c / "resumes" / "0001" / "condor"
    # The copied rescue001 remains; a subsequent DAG failure writes rescue002.
    (rd / "campaign.dag.rescue002").write_text("# latest round\nDONE yall_0000_prepare\n")
    r.resume_campaign(c)
    next_dir = c / "resumes" / "0002" / "condor"
    assert (next_dir / "campaign.dag.rescue002").read_text().startswith("# latest round")
    assert r.scheduler_snapshot(c)["cluster_id"] == 202


def test_old_round_active_job_also_blocks_resume(campaign, monkeypatch):
    c = campaign("slurm")
    scheduler = Scheduler("slurm")
    monkeypatch.setattr(r, "_run", scheduler)
    r.resume_campaign(c)
    scheduler.queue = "11|RUNNING\n"  # Original submission, not the latest IDs.
    with pytest.raises(ValueError, match="active scheduler jobs"):
        r.resume_campaign(c)


@pytest.mark.parametrize("kind", ["completed_missing", "partial", "missing_input"])
def test_filesystem_preflight_preserves_outputs(campaign, monkeypatch, kind):
    c = campaign()
    scheduler = Scheduler("condor")
    monkeypatch.setattr(r, "_run", scheduler)
    manifest = r._read(c / "campaign.json")
    path = c / "data.root"
    if kind == "completed_missing":
        manifest["tasks"]["prepare"]["outputs"] = [{"path": str(path)}]
    elif kind == "partial":
        path.write_text("partial data")
        manifest["tasks"]["convert"]["outputs"] = [{"path": str(path)}]
    else:
        manifest["tasks"]["convert"]["inputs"] = [{"path": str(path)}]
    write(c / "campaign.json", manifest)
    with pytest.raises(ValueError):
        r.resume_campaign(c)
    assert scheduler.submissions == 0
    if kind == "partial":
        assert path.read_text() == "partial data"


def test_future_generated_inputs_need_not_exist(campaign, monkeypatch):
    c = campaign()
    scheduler = Scheduler("condor")
    monkeypatch.setattr(r, "_run", scheduler)
    manifest = r._read(c / "campaign.json")
    ref = {"path": str(c / "converted.root")}
    manifest["tasks"]["convert"]["outputs"] = [ref]
    manifest["tasks"]["analyze"]["inputs"] = [ref]
    write(c / "campaign.json", manifest)
    assert r.resume_campaign(c, dry_run=True) == 0


def test_rescue_disagreement_refused(campaign, monkeypatch):
    c = campaign()
    scheduler = Scheduler("condor")
    monkeypatch.setattr(r, "_run", scheduler)
    (c / "condor" / "campaign.dag.rescue001").write_text("DONE yall_0001_convert\n")
    with pytest.raises(ValueError, match="disagree"):
        r.resume_campaign(c)
    assert scheduler.submissions == 0


def test_latest_terminal_attempt_repairs_stale_state(campaign, monkeypatch):
    c = campaign()
    scheduler = Scheduler("condor")
    monkeypatch.setattr(r, "_run", scheduler)
    write(c / "convert_attempt_001" / "attempt.json",
          {"state": "failed", "finished_at": "2026-09-16", "returncode": 2})
    assert r.reconcile_status(c, status_data(c))["tasks"][1]["state"] == "failed"


def test_legacy_campaign_state_keeps_definitions(campaign, monkeypatch):
    c = campaign(legacy=True)
    scheduler = Scheduler("condor")
    monkeypatch.setattr(r, "_run", scheduler)
    r.resume_campaign(c)
    task = r._read(c / "tasks" / "convert.json")
    assert task["command"] == ["/bin/true"]
    assert task["state"] == "interrupted"


def test_existing_lock_blocks_concurrent_resume(campaign, monkeypatch):
    c = campaign()
    (c / "state" / "resume.lock").mkdir()
    scheduler = Scheduler("condor")
    monkeypatch.setattr(r, "_run", scheduler)
    with pytest.raises(ValueError, match="resume already"):
        r.resume_campaign(c)
    assert not scheduler.calls


def test_unstarted_campaign_rejected(campaign, monkeypatch):
    c = campaign()
    (c / "start.json").unlink()
    with pytest.raises(ValueError, match="not been started"):
        r.resume_campaign(c)


@pytest.mark.parametrize("backend,raw", [("condor", "{}"), ("slurm", "unexpected banner"), ("pbs", "unexpected banner")])
def test_malformed_scheduler_response_not_treated_as_empty(campaign, monkeypatch, backend, raw):
    c = campaign(backend)
    scheduler = Scheduler(backend)
    scheduler.queue = raw
    monkeypatch.setattr(r, "_run", scheduler)
    assert not r.scheduler_snapshot(c)["query_ok"]


@pytest.mark.parametrize("backend", ["slurm", "pbs"])
def test_release_failure_keeps_new_ids_for_monitoring(campaign, monkeypatch, backend):
    c = campaign(backend)
    scheduler = Scheduler(backend)
    scheduler.fail_release = True
    monkeypatch.setattr(r, "_run", scheduler)
    with pytest.raises(RuntimeError, match="do not submit duplicates"):
        r.resume_campaign(c)
    record = r._read(c / "resumes" / "0001" / backend / "submit.json")
    assert len(record["jobs"]) == 2
    assert record["returncode"] == 1


@pytest.mark.parametrize("backend", ["condor", "slurm", "pbs"])
def test_all_completed_is_noop(campaign, monkeypatch, backend):
    c = campaign(backend)
    scheduler = Scheduler(backend)
    monkeypatch.setattr(r, "_run", scheduler)
    for name in ("convert", "analyze"):
        write(c / "state" / f"{name}.json", {"state": "completed", "attempts": 1})
    assert r.resume_campaign(c) == 0
    assert scheduler.submissions == 0
    assert not (c / "resumes").exists()


def test_condor_dry_run_checks_rescue_before_any_submission(campaign, monkeypatch):
    c = campaign()
    scheduler = Scheduler("condor")
    monkeypatch.setattr(r, "_run", scheduler)
    (c / "condor" / "campaign.dag.rescue001").unlink()
    with pytest.raises(ValueError, match="no Rescue DAG"):
        r.resume_campaign(c, dry_run=True)
    assert not (c / "resumes").exists()


def test_submission_timeout_records_uncertainty(campaign, monkeypatch):
    c = campaign()
    scheduler = Scheduler("condor")
    def run(argv, cwd=None):
        if argv[0] == "condor_submit_dag":
            raise subprocess.TimeoutExpired(argv, 60)
        return scheduler(argv, cwd)
    monkeypatch.setattr(r, "_run", run)
    with pytest.raises(RuntimeError, match="unconfirmed scheduler submission"):
        r.resume_campaign(c)
    assert r._read(c / "resumes" / "0001" / "condor" / "submit.json")["in_flight"]
    with pytest.raises(RuntimeError, match="unconfirmed scheduler response"):
        r.resume_campaign(c)


def test_query_failure_after_staging_does_not_submit(campaign, monkeypatch):
    c = campaign()
    scheduler = Scheduler("condor")
    queries = 0
    def run(argv, cwd=None):
        nonlocal queries
        if argv[0] == "condor_q":
            queries += 1
            if queries == 2:
                scheduler.fail_query = True
        return scheduler(argv, cwd)
    monkeypatch.setattr(r, "_run", run)
    with pytest.raises(RuntimeError, match="scheduler state changed"):
        r.resume_campaign(c)
    assert scheduler.submissions == 0


def test_incomplete_pbs_query_is_not_a_purged_job(campaign, monkeypatch):
    c = campaign("pbs")
    scheduler = Scheduler("pbs")
    scheduler.queue = "Job Id: 10.server\n    job_state = F\nJob Id: 11.server\n"
    monkeypatch.setattr(r, "_run", scheduler)
    snapshot = r.scheduler_snapshot(c)
    assert not snapshot["query_ok"]
    assert snapshot["nodes"] == {}


def test_new_scheduler_attempt_overrides_old_failed_display(campaign, monkeypatch):
    c = campaign("slurm")
    scheduler = Scheduler("slurm")
    monkeypatch.setattr(r, "_run", scheduler)
    write(c / "state" / "convert.json", {"state": "failed", "attempts": 1})
    r.resume_campaign(c)
    scheduler.queue = "201|PENDING\n202|PENDING\n"
    data = r.reconcile_status(c, status_data(c))
    assert data["tasks"][1]["state"] == "queued"
    assert data["tasks"][1]["recorded_state"] == "failed"
    assert data["scheduler"]["nodes"]["convert"]["job_id"] == "201"


def test_inconsistent_attempt_cannot_be_treated_as_success(campaign, monkeypatch):
    c = campaign()
    scheduler = Scheduler("condor")
    monkeypatch.setattr(r, "_run", scheduler)
    write(c / "convert_attempt_001" / "attempt.json",
          {"state": "completed", "finished_at": "2026-09-16", "returncode": 2})
    with pytest.raises(ValueError, match="inconsistent terminal attempt"):
        r.resume_campaign(c)


def test_completed_dependency_chain_must_be_consistent(campaign, monkeypatch):
    c = campaign()
    scheduler = Scheduler("condor")
    monkeypatch.setattr(r, "_run", scheduler)
    write(c / "state" / "analyze.json", {"state": "completed", "attempts": 1})
    with pytest.raises(ValueError, match="inconsistent completed dependency chain"):
        r.resume_campaign(c)


@pytest.mark.parametrize("backend,queue", [
    ("slurm", "11|SUSPENDED\n"),
    ("pbs", "Job Id: 11.server\n    job_state = S\n"),
    ("condor", '[{"ClusterId":101,"DAGManJobId":100,"DAGNodeName":"yall_0001_convert","JobStatus":5}]'),
])
def test_suspended_unknown_and_condor_held_jobs_block_recovery(campaign, monkeypatch, backend, queue):
    c = campaign(backend)
    scheduler = Scheduler(backend)
    scheduler.queue = queue
    monkeypatch.setattr(r, "_run", scheduler)
    with pytest.raises(ValueError, match="active scheduler jobs"):
        r.resume_campaign(c, cancel_pending=True)
    assert scheduler.submissions == 0


def test_pending_job_that_started_before_cancellation_is_not_cancelled(campaign, monkeypatch):
    c = campaign("slurm")
    scheduler = Scheduler("slurm")
    scheduler.queue = "12|PENDING\n"
    queries = 0
    def run(argv, cwd=None):
        nonlocal queries
        if argv[0] == "squeue":
            queries += 1
            if queries == 2:
                scheduler.queue = "12|RUNNING\n"
        return scheduler(argv, cwd)
    monkeypatch.setattr(r, "_run", run)
    with pytest.raises(RuntimeError, match="changed before cancellation"):
        r.resume_campaign(c, cancel_pending=True)
    assert not any(a[0] == "scancel" for a, _ in scheduler.calls)


def test_no_completed_output_deleted_even_when_overwrite_allowed(campaign, monkeypatch):
    c = campaign("slurm")
    scheduler = Scheduler("slurm")
    monkeypatch.setattr(r, "_run", scheduler)
    manifest = r._read(c / "campaign.json")
    result = c / "prepared.root"
    result.write_text("good data")
    manifest["tasks"]["prepare"]["outputs"] = [{"path": str(result)}]
    write(c / "campaign.json", manifest)
    write(c / "start.json", {"overwrite": True})
    r.resume_campaign(c)
    assert result.read_text() == "good data"
    assert all(a[-1] != "prepare.sh" for a, _ in scheduler.calls if a[0] == "sbatch")


def test_queued_resume_records_operator_reason(campaign, monkeypatch):
    c = campaign("condor")
    scheduler = Scheduler("condor")
    monkeypatch.setattr(r, "_run", scheduler)
    reason = "Created missing final output directory"
    assert r.resume_campaign(c, reason=reason) == 0
    record = r._read(c / "resumes" / "0001" / "resume.json")
    assert record["reason"] == reason
    assert record["status"] == "submitted"
