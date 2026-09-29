"""Account snapshots belong to the process at each lifecycle boundary."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest

from yall_run import __version__, campaign, worker
from yall_run.condor_backend import render_condor
from yall_run.model import load_spec
from yall_run.pbs_backend import render_pbs
from yall_run.slurm_backend import render_slurm


def identity(name, uid):
    return {
        "username": name, "effective_username": name,
        "username_source": "pwd.getpwuid",
        "effective_username_source": "pwd.getpwuid",
        "uid": uid, "gid": uid + 100, "euid": uid, "egid": uid + 100,
    }


def fake_accounts(monkeypatch, lookup=None):
    monkeypatch.setattr(worker, "os", SimpleNamespace(
        getuid=lambda: 1001, getgid=lambda: 1101,
        geteuid=lambda: 2002, getegid=lambda: 2202,
    ))
    if lookup is None:
        lookup = lambda uid: SimpleNamespace(pw_name={1001: "alice", 2002: "service"}[uid])
    monkeypatch.setitem(sys.modules, "pwd", SimpleNamespace(getpwuid=lookup))


def read(path):
    return json.loads(path.read_text())


def spec(tmp_path, backend="local", extra="", command="/usr/bin/true"):
    path = tmp_path / "Yallfile"
    path.write_text(
        f"campaign account-test\nbackend {backend}\n\none:\n"
        + extra + f"    {command}\n"
    )
    return load_spec(path)


def test_os_accounts_override_environment_and_distinguish_effective_ids(monkeypatch):
    for name in ("USER", "LOGNAME", "USERNAME", "SUDO_USER"):
        monkeypatch.setenv(name, "inherited-submitter-not-worker")
    fake_accounts(monkeypatch)
    result = worker.user_identity()
    assert result == {
        "username": "alice", "effective_username": "service",
        "username_source": "pwd.getpwuid",
        "effective_username_source": "pwd.getpwuid",
        "uid": 1001, "gid": 1101, "euid": 2002, "egid": 2202,
    }
    assert json.loads(json.dumps(result)) == result


@pytest.mark.parametrize("error", [KeyError, OSError, ValueError, OverflowError, NotImplementedError])
def test_account_lookup_failure_keeps_numeric_ids(monkeypatch, error):
    def fail(uid):
        raise error("account service unavailable")
    fake_accounts(monkeypatch, fail)
    monkeypatch.setenv("USER", "do-not-substitute-me")
    result = worker.user_identity()
    assert result["uid"] == 1001
    assert result["euid"] == 2002
    assert result["username"] is None
    assert result["effective_username"] is None
    assert result["username_source"] is None
    assert set(result["errors"]) == {"username", "effective_username"}


def test_missing_pwd_module_is_nonfatal(monkeypatch):
    fake_accounts(monkeypatch)
    monkeypatch.setitem(sys.modules, "pwd", None)
    result = worker.user_identity()
    assert result["uid"] == 1001
    assert result["username"] is None
    assert "account_lookup" in result["errors"]


def test_missing_posix_apis_are_explicitly_unknown(monkeypatch):
    monkeypatch.setattr(worker, "os", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "pwd", None)
    result = worker.user_identity()
    assert all(result[field] is None for field in ("uid", "gid", "euid", "egid", "username", "effective_username"))
    assert {"uid", "gid", "euid", "egid", "account_lookup"} <= set(result["errors"])


@pytest.mark.parametrize("error", [OSError, ValueError, NotImplementedError])
def test_an_unavailable_id_does_not_erase_other_ids(monkeypatch, error):
    fake_accounts(monkeypatch)
    def fail():
        raise error("uid unavailable")
    monkeypatch.setattr(worker.os, "getuid", fail)
    result = worker.user_identity()
    assert result["uid"] is None
    assert result["username"] is None
    assert result["euid"] == 2002
    assert result["effective_username"] == "service"
    assert "uid" in result["errors"]


@pytest.mark.parametrize("backend", ["local", "condor", "slurm", "pbs"])
def test_creator_submitter_and_worker_are_independent(tmp_path, monkeypatch, backend):
    creator, submitter, executor = identity("alice", 1001), identity("bob", 1002), identity("batch", 1003)
    monkeypatch.setattr(campaign, "user_identity", lambda: creator)
    directory = campaign.create_campaign(spec(tmp_path, backend), tmp_path / "campaigns")
    original_manifest = (directory / "campaign.json").read_bytes()
    assert read(directory / "campaign.json")["creation"]["user"] == creator

    monkeypatch.setattr(campaign, "user_identity", lambda: submitter)
    monkeypatch.setattr(campaign.platform, "node", lambda: "submission-host")
    campaign.prepare_campaign_start(directory)
    assert read(directory / "state/start-pending.json")["user"] == submitter

    # Finalization must use the already frozen request, even in another context.
    monkeypatch.setattr(campaign, "user_identity", lambda: identity("finalizer", 9999))
    monkeypatch.setattr(campaign.platform, "node", lambda: "different-host")
    campaign.begin_campaign(directory)
    start_bytes = (directory / "start.json").read_bytes()
    assert read(directory / "start.json")["user"] == submitter
    assert read(directory / "start.json")["hostname"] == "submission-host"
    assert not (directory / "state/start-pending.json").exists()

    monkeypatch.setattr(worker, "user_identity", lambda: executor)
    assert worker.run_task(directory, "one") == 0
    provenance = read(directory / "one_attempt_001/provenance.json")
    assert provenance["execution"]["user"] == executor
    assert provenance["execution"]["context"] == "host"
    assert (directory / "campaign.json").read_bytes() == original_manifest
    assert (directory / "start.json").read_bytes() == start_bytes


def test_begin_without_preparation_captures_current_account(tmp_path, monkeypatch):
    directory = campaign.create_campaign(spec(tmp_path), tmp_path / "campaigns")
    submitter = identity("bob", 1002)
    monkeypatch.setattr(campaign, "user_identity", lambda: submitter)
    campaign.begin_campaign(directory)
    assert read(directory / "start.json")["user"] == submitter


def test_legacy_pending_request_is_not_misattributed(tmp_path, monkeypatch):
    directory = campaign.create_campaign(spec(tmp_path), tmp_path / "campaigns")
    (directory / "state/start-pending.json").write_text('{"overwrite": false}')
    def unexpected():
        raise AssertionError("Do not invent the old submitter from the current account")
    monkeypatch.setattr(campaign, "user_identity", unexpected)
    campaign.begin_campaign(directory)
    assert read(directory / "start.json")["user"] is None
    assert read(directory / "start.json")["hostname"] is None


def test_cancelled_start_can_capture_a_different_submitter(tmp_path, monkeypatch):
    directory = campaign.create_campaign(spec(tmp_path), tmp_path / "campaigns")
    monkeypatch.setattr(campaign, "user_identity", lambda: identity("alice", 1001))
    campaign.prepare_campaign_start(directory)
    campaign.cancel_prepared_start(directory)
    submitter = identity("bob", 1002)
    monkeypatch.setattr(campaign, "user_identity", lambda: submitter)
    campaign.prepare_campaign_start(directory)
    campaign.begin_campaign(directory)
    assert read(directory / "start.json")["user"] == submitter


@pytest.mark.parametrize("kind", ["missing_inputs", "outputs_exist", "command_failed"])
def test_failed_attempt_still_records_execution_account(tmp_path, monkeypatch, kind):
    extra = ""
    command = "/usr/bin/true"
    if kind == "missing_inputs":
        extra = f"    @input data {tmp_path}/missing.dat\n"
    elif kind == "outputs_exist":
        output = tmp_path / "existing.dat"
        output.write_text("do not alter")
        extra = f"    @output data {output}\n"
    else:
        command = "/usr/bin/false"
    directory = campaign.create_campaign(spec(tmp_path, extra=extra, command=command), tmp_path / "campaigns")
    executor = identity("batch", 1003)
    monkeypatch.setattr(worker, "user_identity", lambda: executor)
    assert worker.run_task(directory, "one") != 0
    assert read(directory / "one_attempt_001/provenance.json")["execution"]["user"] == executor
    assert read(directory / "one_attempt_001/attempt.json")["failure"]["kind"] == kind


def test_each_attempt_has_a_fresh_snapshot(tmp_path, monkeypatch):
    directory = campaign.create_campaign(spec(tmp_path, command="/usr/bin/false"), tmp_path / "campaigns")
    first, second = identity("worker-a", 1001), identity("worker-b", 1002)
    monkeypatch.setattr(worker, "user_identity", lambda: first)
    worker.run_task(directory, "one")
    original = (directory / "one_attempt_001/provenance.json").read_bytes()
    monkeypatch.setattr(worker, "user_identity", lambda: second)
    worker.run_task(directory, "one")
    assert read(directory / "one_attempt_002/provenance.json")["execution"]["user"] == second
    assert (directory / "one_attempt_001/provenance.json").read_bytes() == original


def test_old_campaign_metadata_is_not_backfilled(tmp_path):
    directory = campaign.create_campaign(spec(tmp_path), tmp_path / "campaigns")
    manifest = read(directory / "campaign.json")
    del manifest["creation"]["user"]
    (directory / "campaign.json").write_text(json.dumps(manifest))
    original = (directory / "campaign.json").read_bytes()
    assert worker.run_task(directory, "one") == 0
    assert "user" in read(directory / "one_attempt_001/provenance.json")["execution"]
    assert (directory / "campaign.json").read_bytes() == original


@pytest.mark.parametrize("backend, renderer", [("condor", render_condor), ("slurm", render_slurm), ("pbs", render_pbs)])
def test_bundled_worker_captures_account_without_installed_package(tmp_path, backend, renderer):
    directory = renderer(spec(tmp_path, backend), tmp_path / "campaigns")
    bundled = directory / backend / "yall_worker.py"
    assert bundled.is_file()
    expected = worker.user_identity()
    environment = os.environ.copy()
    environment.update(USER="spoofed", LOGNAME="spoofed", SUDO_USER="spoofed")
    result = subprocess.run(
        [sys.executable, "-I", str(bundled), str(directory), "one"],
        cwd=tmp_path, env=environment, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert read(directory / "one_attempt_001/provenance.json")["execution"]["user"] == expected


def test_local_resume_records_new_invoker_without_replacing_start(tmp_path, monkeypatch):
    directory = campaign.create_campaign(spec(tmp_path), tmp_path / "campaigns")
    campaign.begin_campaign(directory)
    original_start = (directory / "start.json").read_bytes()
    resumer = identity("resumer", 4004)
    monkeypatch.setattr(campaign, "user_identity", lambda: resumer)
    campaign.resume_local(directory, reason="account provenance test")
    assert read(directory / "resumes/resume_001.json")["user"] == resumer
    assert (directory / "start.json").read_bytes() == original_start


@pytest.mark.parametrize("hook", ["preflight", "postflight"])
def test_hook_account_is_recorded(tmp_path, monkeypatch, hook):
    actor = identity("hook-runner", 5005)
    monkeypatch.setattr(campaign, "user_identity", lambda: actor)
    records = campaign._run_hook((("/usr/bin/true",),), tmp_path, hook=hook, cwd=tmp_path)
    assert records[0]["user"] == actor
    assert read(tmp_path / hook / "001/result.json")["user"] == actor


def test_package_versions_match():
    project = Path(__file__).resolve().parents[1] / "pyproject.toml"
    match = re.search(r'^version = "([^"]+)"$', project.read_text(), re.MULTILINE)
    assert match is not None
    assert match.group(1) == __version__
