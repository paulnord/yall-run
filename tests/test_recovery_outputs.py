"""Interrupted output recovery with simulated queues; no real jobs or data."""
import io
import os
from pathlib import Path
import shlex
import subprocess
import sys
from types import SimpleNamespace

import pytest

from yall_run import recovery as r
from test_recovery import Scheduler, campaign, contents, write


def partial_output(c, tmp_path, *, directory=False):
    output = tmp_path / "partial"
    if directory:
        output.mkdir()
        (output / "data.root").write_text("interrupted")
    else:
        output.write_text("interrupted")
    manifest = r._read(c / "campaign.json")
    manifest["tasks"]["convert"]["outputs"] = [{"path": str(output)}]
    write(c / "campaign.json", manifest)
    return output


@pytest.mark.parametrize("backend", ["condor", "slurm", "pbs"])
@pytest.mark.parametrize("directory", [False, True])
def test_explicit_overwrite_removes_only_unfinished_outputs(campaign, tmp_path, monkeypatch, backend, directory):
    c = campaign(backend)
    output = partial_output(c, tmp_path, directory=directory)
    completed = tmp_path / "good.root"
    completed.write_text("completed")
    manifest = r._read(c / "campaign.json")
    manifest["tasks"]["prepare"]["outputs"] = [{"path": str(completed)}]
    write(c / "campaign.json", manifest)
    old_start = (c / "start.json").read_bytes()
    old_attempt = contents(c / "convert_attempt_001")
    scheduler = Scheduler(backend)
    monkeypatch.setattr(r, "_run", scheduler)

    assert r.resume_campaign(c, overwrite=True, yes=True) == 0
    assert not output.exists()
    assert completed.read_text() == "completed"
    assert (c / "start.json").read_bytes() == old_start
    assert contents(c / "convert_attempt_001") == old_attempt
    record = r._read(c / "resumes" / "0001" / "resume.json")
    assert record["overwrite_outputs"] == [str(output)]
    assert record["removed_outputs"] == [str(output)]
    assert record["status"] == "submitted"


def test_overwrite_dry_run_lists_paths_without_mutation(campaign, tmp_path, monkeypatch, capsys):
    c = campaign()
    output = partial_output(c, tmp_path)
    before = contents(c)
    scheduler = Scheduler("condor")
    monkeypatch.setattr(r, "_run", scheduler)
    assert r.resume_campaign(c, overwrite=True, dry_run=True) == 0
    assert f"would delete {output}" in capsys.readouterr().out
    assert output.read_text() == "interrupted"
    assert contents(c) == before
    assert scheduler.submissions == 0


@pytest.mark.parametrize("interactive,answer", [(False, ""), (True, "no")])
def test_overwrite_requires_confirmation(campaign, tmp_path, monkeypatch, interactive, answer):
    c = campaign()
    output = partial_output(c, tmp_path)
    scheduler = Scheduler("condor")
    monkeypatch.setattr(r, "_run", scheduler)
    source = io.StringIO(answer + "\n")
    monkeypatch.setattr(source, "isatty", lambda: interactive)
    monkeypatch.setattr(sys, "stdin", source)
    with pytest.raises(ValueError, match="interactive confirmation|cancelled"):
        r.resume_campaign(c, overwrite=True)
    assert output.read_text() == "interrupted"
    assert not (c / "resumes").exists()
    assert scheduler.submissions == 0


@pytest.mark.parametrize("problem", ["stage", "query", "active"])
def test_preparation_failure_preserves_partial_output(campaign, tmp_path, monkeypatch, problem):
    c = campaign()
    output = partial_output(c, tmp_path)
    scheduler = Scheduler("condor")
    monkeypatch.setattr(r, "_run", scheduler)
    stage = r._stage

    def change_state(*args):
        if problem == "stage":
            raise RuntimeError("staging failed")
        result = stage(*args)
        if problem == "query":
            scheduler.fail_query = True
        else:
            scheduler.queue = '[{"ClusterId":100,"JobStatus":2}]'
        return result

    monkeypatch.setattr(r, "_stage", change_state)
    with pytest.raises(RuntimeError, match="staging failed|scheduler state changed"):
        r.resume_campaign(c, overwrite=True, yes=True)
    assert output.read_text() == "interrupted"
    assert scheduler.submissions == 0


@pytest.mark.parametrize("protected", ["completed", "input", "campaign", "symlink_alias"])
def test_overwrite_rejects_protected_overlaps(campaign, tmp_path, monkeypatch, protected):
    c = campaign()
    output = partial_output(c, tmp_path, directory=True)
    manifest = r._read(c / "campaign.json")
    data = output / "data.root"
    if protected == "completed":
        manifest["tasks"]["prepare"]["outputs"] = [{"path": str(data)}]
    elif protected == "input":
        manifest["tasks"]["analyze"]["inputs"] = [{"path": str(data)}]
    elif protected == "campaign":
        manifest["tasks"]["convert"]["outputs"] = [{"path": str(c)}]
    else:
        alias = tmp_path / "alias"
        alias.symlink_to(output, target_is_directory=True)
        manifest["tasks"]["prepare"]["outputs"] = [{"path": str(alias / "data.root")}]
    write(c / "campaign.json", manifest)
    scheduler = Scheduler("condor")
    monkeypatch.setattr(r, "_run", scheduler)
    with pytest.raises(ValueError, match="overlaps protected"):
        r.resume_campaign(c, overwrite=True, yes=True)
    assert data.read_text() == "interrupted"
    assert scheduler.submissions == 0


@pytest.mark.parametrize("change", ["replace", "new_output", "completed"])
def test_changed_recovery_plan_preserves_outputs(campaign, tmp_path, monkeypatch, change):
    c = campaign()
    output = partial_output(c, tmp_path)
    other = tmp_path / "late.root"
    manifest = r._read(c / "campaign.json")
    manifest["tasks"]["analyze"]["outputs"] = [{"path": str(other)}]
    write(c / "campaign.json", manifest)
    scheduler = Scheduler("condor")
    monkeypatch.setattr(r, "_run", scheduler)
    stage = r._stage

    def change_outputs(*args):
        result = stage(*args)
        if change == "replace":
            output.write_text("changed after confirmation")
        elif change == "new_output":
            other.write_text("late output")
        else:
            write(c / "convert_attempt_001" / "attempt.json", {
                "state": "completed", "finished_at": "2026-11-09", "returncode": 0})
            (c / "condor" / "campaign.dag.rescue001").write_text(
                "DONE yall_0000_prepare\nDONE yall_0001_convert\n")
        return result

    monkeypatch.setattr(r, "_stage", change_outputs)
    with pytest.raises((ValueError, RuntimeError), match="changed|disagree"):
        r.resume_campaign(c, overwrite=True, yes=True)
    assert output.exists()
    assert scheduler.submissions == 0


@pytest.mark.parametrize("backend", ["slurm", "pbs"])
def test_pending_cancellation_must_finish_before_cleanup(campaign, tmp_path, monkeypatch, backend):
    c = campaign(backend)
    output = partial_output(c, tmp_path)
    scheduler = Scheduler(backend)
    scheduler.queue = "12|PENDING\n" if backend == "slurm" else "Job Id: 12.server\n    job_state = Q\n"
    scheduler.cancel_takes_effect = False
    monkeypatch.setattr(r, "_run", scheduler)
    with pytest.raises(RuntimeError, match="cancellation not yet confirmed"):
        r.resume_campaign(c, overwrite=True, yes=True, cancel_pending=True)
    assert output.read_text() == "interrupted"
    assert scheduler.submissions == 0


@pytest.mark.parametrize("policy", ["task", "campaign"])
@pytest.mark.parametrize("explicit", [False, True])
def test_existing_overwrite_policy_is_distinct_from_explicit_cleanup(campaign, tmp_path, monkeypatch, policy, explicit):
    c = campaign()
    output = partial_output(c, tmp_path)
    if policy == "task":
        manifest = r._read(c / "campaign.json")
        manifest["tasks"]["convert"]["overwrite"] = True
        write(c / "campaign.json", manifest)
    else:
        write(c / "start.json", {"overwrite": True})
    scheduler = Scheduler("condor")
    monkeypatch.setattr(r, "_run", scheduler)
    assert r.resume_campaign(c, overwrite=explicit, yes=explicit) == 0
    if explicit:
        assert not output.exists()
    else:
        assert output.read_text() == "interrupted"


@pytest.mark.parametrize("consumer", ["descendant", "self", "unrelated", "completed"])
def test_only_unfinished_ancestor_outputs_are_replaceable_inputs(campaign, tmp_path, monkeypatch, consumer):
    c = campaign()
    output = partial_output(c, tmp_path)
    manifest = r._read(c / "campaign.json")
    name = {"descendant": "analyze", "self": "convert",
            "unrelated": "analyze", "completed": "prepare"}[consumer]
    manifest["tasks"][name]["inputs"] = [{"path": str(output)}]
    if consumer == "unrelated":
        manifest["tasks"]["analyze"]["parents"] = ["prepare"]
    write(c / "campaign.json", manifest)
    scheduler = Scheduler("condor")
    monkeypatch.setattr(r, "_run", scheduler)
    if consumer == "descendant":
        assert r.resume_campaign(c, overwrite=True, yes=True) == 0
        assert not output.exists()
    else:
        with pytest.raises(ValueError, match="overlaps protected"):
            r.resume_campaign(c, overwrite=True, yes=True)
        assert output.read_text() == "interrupted"
        assert scheduler.submissions == 0


@pytest.mark.parametrize("kind", ["dangling", "file", "directory"])
def test_cleanup_unlinks_symlinks_without_deleting_targets(campaign, tmp_path, monkeypatch, kind):
    c = campaign()
    output = partial_output(c, tmp_path)
    output.unlink()
    target = tmp_path / "target"
    if kind == "file":
        target.write_text("keep")
    elif kind == "directory":
        target.mkdir()
        (target / "keep.txt").write_text("keep")
    output.symlink_to(target, target_is_directory=kind == "directory")
    scheduler = Scheduler("condor")
    monkeypatch.setattr(r, "_run", scheduler)
    assert r.resume_campaign(c, overwrite=True, yes=True) == 0
    assert not output.is_symlink()
    assert target.exists() == (kind != "dangling")
    if kind == "directory":
        assert (target / "keep.txt").read_text() == "keep"


@pytest.mark.parametrize("change", ["descendant", "parent_alias"])
def test_fingerprint_catches_tree_and_parent_changes(campaign, tmp_path, monkeypatch, change):
    c = campaign()
    output = partial_output(c, tmp_path, directory=True)
    manifest = r._read(c / "campaign.json")
    alias = tmp_path / "alias"
    if change == "parent_alias":
        alias.symlink_to(output, target_is_directory=True)
        manifest["tasks"]["convert"]["outputs"] = [{"path": str(alias / "data.root")}]
        write(c / "campaign.json", manifest)
    scheduler = Scheduler("condor")
    monkeypatch.setattr(r, "_run", scheduler)
    stage = r._stage

    def mutate(*args):
        result = stage(*args)
        if change == "descendant":
            # Editing an existing child does not update the directory mtime.
            before = output.stat().st_mtime_ns
            (output / "data.root").write_text("changed")
            assert output.stat().st_mtime_ns == before
        else:
            # Retain the original inode so this integration case is independent
            # of the filesystem's inode reuse policy.
            alias.rename(tmp_path / "previous-alias")
            alias.symlink_to(output, target_is_directory=True)
        return result

    monkeypatch.setattr(r, "_stage", mutate)
    with pytest.raises(RuntimeError, match="changed"):
        r.resume_campaign(c, overwrite=True, yes=True)
    assert (output / "data.root").exists()
    assert scheduler.submissions == 0


def test_nested_cleanup_outputs_are_refused(campaign, tmp_path, monkeypatch):
    c = campaign()
    output = partial_output(c, tmp_path, directory=True)
    manifest = r._read(c / "campaign.json")
    manifest["tasks"]["analyze"]["outputs"] = [{"path": str(output / "data.root")}]
    write(c / "campaign.json", manifest)
    monkeypatch.setattr(r, "_run", Scheduler("condor"))
    with pytest.raises(ValueError, match="overlapping cleanup outputs"):
        r.resume_campaign(c, overwrite=True, yes=True)
    assert (output / "data.root").exists()


def test_duplicate_cleanup_output_is_deleted_once(campaign, tmp_path, monkeypatch):
    c = campaign()
    output = partial_output(c, tmp_path)
    manifest = r._read(c / "campaign.json")
    manifest["tasks"]["convert"]["outputs"] *= 2
    write(c / "campaign.json", manifest)
    monkeypatch.setattr(r, "_run", Scheduler("condor"))
    assert r.resume_campaign(c, overwrite=True, yes=True) == 0
    record = r._read(c / "resumes" / "0001" / "resume.json")
    assert record["removed_outputs"] == [str(output)]


def test_partial_cleanup_failure_is_journaled(campaign, tmp_path, monkeypatch):
    c = campaign()
    output = partial_output(c, tmp_path)
    second = tmp_path / "second.root"
    second.write_text("interrupted too")
    manifest = r._read(c / "campaign.json")
    manifest["tasks"]["analyze"]["outputs"] = [{"path": str(second)}]
    write(c / "campaign.json", manifest)
    scheduler = Scheduler("condor")
    monkeypatch.setattr(r, "_run", scheduler)
    unlink = Path.unlink

    def fail_second(path, *args, **kwargs):
        if path == second:
            raise PermissionError("cleanup denied")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_second)
    with pytest.raises(PermissionError, match="cleanup denied"):
        r.resume_campaign(c, overwrite=True, yes=True)
    record = r._read(c / "resumes" / "0001" / "resume.json")
    assert record["status"] == "failed"
    assert record["removed_outputs"] == [str(output)]
    assert record["cleanup_in_flight"] == str(second)
    assert second.read_text() == "interrupted too"
    assert scheduler.submissions == 0


def test_submission_failure_keeps_cleanup_audit_record(campaign, tmp_path, monkeypatch):
    c = campaign()
    output = partial_output(c, tmp_path)
    scheduler = Scheduler("condor")
    scheduler.fail_submit_at = 1
    monkeypatch.setattr(r, "_run", scheduler)
    with pytest.raises(RuntimeError, match="submission rejected"):
        r.resume_campaign(c, overwrite=True, yes=True)
    record = r._read(c / "resumes" / "0001" / "resume.json")
    assert record["status"] == "failed"
    assert record["removed_outputs"] == [str(output)]
    assert record["cleanup_in_flight"] is None
    assert scheduler.submissions == 1


@pytest.mark.parametrize("backend", ["condor", "slurm", "pbs"])
def test_archived_worker_restarts_after_partial_output_cleanup(tmp_path, monkeypatch, backend):
    from yall_run.campaign import begin_campaign
    from yall_run.condor_backend import render_condor
    from yall_run.model import load_spec
    from yall_run.pbs_backend import render_pbs
    from yall_run.slurm_backend import render_slurm

    source = tmp_path / "Yallfile"
    command = "from pathlib import Path; Path('result.txt').write_text('finished')"
    source.write_text(
        f"campaign partial-restart\nbackend {backend}\n\n"
        "convert:\n    @output result result.txt\n"
        f"    {shlex.quote(sys.executable)} -c {shlex.quote(command)}\n")
    renderer = {"condor": render_condor, "slurm": render_slurm, "pbs": render_pbs}[backend]
    c = renderer(load_spec(source), tmp_path / "campaigns")
    begin_campaign(c)
    worker = c / backend / "yall_worker.py"
    worker_before = worker.read_bytes()
    (tmp_path / "result.txt").write_text("partial from evicted process")
    write(c / "state" / "convert.json", {"state": "running", "attempts": 1})
    write(c / "convert_attempt_001" / "attempt.json", {"state": "running", "attempt": 1})
    old_attempt = (c / "convert_attempt_001" / "attempt.json").read_bytes()
    if backend == "condor":
        submit = {"cluster_id": 100, "returncode": 0}
        (c / backend / "campaign.dag.rescue001").write_text("# no completed nodes\n")
    else:
        submit = {"jobs": {"convert": "10" if backend == "slurm" else "10.server"}, "returncode": 0}
    write(c / backend / "submit.json", submit)
    monkeypatch.setattr(r, "_run", Scheduler(backend))
    assert r.resume_campaign(c, overwrite=True, yes=True) == 0
    # Invoke the frozen standalone worker, not the installed Python module.
    env = dict(os.environ, PYTHONPATH="")
    result = subprocess.run([sys.executable, str(worker), str(c), "convert"],
                            capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "result.txt").read_text() == "finished"
    assert worker.read_bytes() == worker_before
    assert (c / "convert_attempt_001" / "attempt.json").read_bytes() == old_attempt
    assert r._read(c / "state" / "convert.json")["attempts"] == 2


def test_parent_symlink_fingerprint_detects_reused_inode(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.mkdir()
    (target / "data.root").write_text("partial")
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    path = str(alias / "data.root")
    before = r._output_fingerprint(path)
    original = Path.lstat
    previous = alias.lstat()

    def reused_inode(item, *args, **kwargs):
        if item == alias:
            return SimpleNamespace(st_dev=previous.st_dev, st_ino=previous.st_ino,
                                   st_mode=previous.st_mode,
                                   st_ctime_ns=previous.st_ctime_ns + 1)
        return original(item, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", reused_inode)
    assert r._output_fingerprint(path) != before
