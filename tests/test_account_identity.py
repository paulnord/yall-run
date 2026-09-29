"""Account provenance distinguishes creation, submission, and worker execution."""

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from types import SimpleNamespace

import pytest

from yall_run import __version__
from yall_run import campaign as campaigns
from yall_run import worker
from yall_run.condor_backend import render_condor
from yall_run.model import load_spec
from yall_run.pbs_backend import render_pbs
from yall_run.slurm_backend import render_slurm


ID_FIELDS = ("uid", "gid", "euid", "egid")


def read(path):
    return json.loads(path.read_text())


def make_spec(tmp_path, *, declarations="", command=None):
    command = command or [sys.executable, "-c", "print('identity-check')"]
    source = tmp_path / "Yallfile"
    source.write_text(
        "campaign identity-check\nbackend local\none:\n"
        + declarations + "    " + shlex.join(command) + "\n"
    )
    return load_spec(source)


def fake_identity(name, uid):
    return {
        "username": name, "effective_username": name,
        "uid": uid, "gid": uid + 10, "euid": uid, "egid": uid + 10,
        "username_source": "pwd.getpwuid",
        "effective_username_source": "pwd.getpwuid",
    }


def fake_ids(monkeypatch, uid=1001, gid=51, euid=1002, egid=52):
    for field, value in zip(ID_FIELDS, (uid, gid, euid, egid)):
        monkeypatch.setattr(os, "get" + field, lambda value=value: value, raising=False)


def unavailable_ids(monkeypatch):
    for field in ID_FIELDS:
        monkeypatch.delattr(os, "get" + field, raising=False)


@pytest.mark.parametrize("uid,euid", [(1001, 1002), (0, 0)])
def test_os_accounts_override_inherited_environment(monkeypatch, uid, euid):
    fake_ids(monkeypatch, uid=uid, euid=euid)
    monkeypatch.setitem(sys.modules, "pwd", SimpleNamespace(
        getpwuid=lambda value: SimpleNamespace(pw_name="account-" + str(value))
    ))
    for key in ("USER", "LOGNAME", "LNAME", "USERNAME", "SUDO_USER"):
        monkeypatch.setenv(key, "not-the-process-account")
    result = worker._account_identity()
    assert result == {
        "username": "account-" + str(uid),
        "effective_username": "account-" + str(euid),
        "uid": uid, "gid": 51, "euid": euid, "egid": 52,
        "username_source": "pwd.getpwuid",
        "effective_username_source": "pwd.getpwuid",
    }
    assert json.loads(json.dumps(result)) == result


@pytest.mark.parametrize("error", [KeyError("no account"), OSError("NSS unavailable")])
def test_unknown_account_retains_ids_without_env_fallback(monkeypatch, error):
    fake_ids(monkeypatch)
    def lookup(uid):
        raise error
    monkeypatch.setitem(sys.modules, "pwd", SimpleNamespace(getpwuid=lookup))
    monkeypatch.setenv("USER", "incorrect-user")
    result = worker._account_identity()
    assert result["uid"] == 1001
    assert result["euid"] == 1002
    assert result["gid"] == 51
    assert result["egid"] == 52
    assert result["username"] is None
    assert result["effective_username"] is None
    assert result["username_source"] is None
    assert "username" in result["errors"]
    assert "effective_username" in result["errors"]


def test_pwd_absent_on_posix_retains_numeric_ids(monkeypatch):
    fake_ids(monkeypatch)
    monkeypatch.setitem(sys.modules, "pwd", None)
    monkeypatch.setenv("USER", "incorrect-user")
    result = worker._account_identity()
    assert result["uid"] == 1001
    assert result["username"] is None
    assert "pwd module unavailable" in result["errors"]["username"]


def test_non_posix_fallback_is_labelled(monkeypatch):
    unavailable_ids(monkeypatch)
    monkeypatch.setitem(sys.modules, "pwd", None)
    monkeypatch.setitem(sys.modules, "getpass", SimpleNamespace(getuser=lambda: "env-account"))
    result = worker._account_identity()
    assert result["username"] == "env-account"
    assert result["username_source"] == "getpass.getuser"
    assert result["effective_username"] is None
    assert all(result[field] is None for field in ID_FIELDS)


@pytest.mark.parametrize("error", [OSError("no login"), KeyError("no entry")])
def test_unavailable_identity_is_not_fatal(monkeypatch, error):
    unavailable_ids(monkeypatch)
    monkeypatch.setitem(sys.modules, "pwd", None)
    def getuser():
        raise error
    monkeypatch.setitem(sys.modules, "getpass", SimpleNamespace(getuser=getuser))
    result = worker._account_identity()
    assert result["username"] is None
    assert all(result[field] is None for field in ID_FIELDS)
    assert "username" in result["errors"]
    json.dumps(result)


def test_failed_uid_query_is_not_replaced_by_environment(monkeypatch):
    fake_ids(monkeypatch)
    def fail():
        raise OSError("uid unavailable")
    monkeypatch.setattr(os, "getuid", fail)
    monkeypatch.setitem(sys.modules, "pwd", SimpleNamespace(
        getpwuid=lambda value: SimpleNamespace(pw_name="effective-account")
    ))
    result = worker._account_identity()
    assert result["uid"] is None
    assert result["username"] is None
    assert result["euid"] == 1002
    assert result["effective_username"] == "effective-account"
    assert result["errors"]["uid"] == "uid unavailable"


def test_creation_submission_execution_are_separate_snapshots(tmp_path, monkeypatch):
    creator = fake_identity("alice", 101)
    submitter = fake_identity("bob", 102)
    executor = fake_identity("batch", 103)
    monkeypatch.setattr(campaigns, "_account_identity", lambda: creator)
    campaign = campaigns.create_campaign(make_spec(tmp_path), tmp_path / "campaigns")
    manifest_before = (campaign / "campaign.json").read_bytes()
    assert read(campaign / "campaign.json")["creation"]["identity"] == creator

    monkeypatch.setattr(campaigns, "_account_identity", lambda: submitter)
    monkeypatch.setattr(campaigns.platform, "node", lambda: "submission-host")
    campaigns.prepare_campaign_start(campaign)
    pending = read(campaign / "state/start-pending.json")
    assert pending["identity"] == submitter
    monkeypatch.setattr(campaigns, "_account_identity", lambda: fake_identity("finalizer", 104))
    monkeypatch.setattr(campaigns.platform, "node", lambda: "another-host")
    campaigns.begin_campaign(campaign)
    start_before = (campaign / "start.json").read_bytes()
    assert read(campaign / "start.json")["identity"] == submitter
    assert read(campaign / "start.json")["hostname"] == "submission-host"
    assert not (campaign / "state/start-pending.json").exists()

    monkeypatch.setattr(worker, "_account_identity", lambda: executor)
    assert worker.run_task(campaign, "one") == 0
    launch = read(campaign / "one_attempt_001/provenance.json")
    attempt = read(campaign / "one_attempt_001/attempt.json")
    assert launch["execution"]["identity"] == executor
    assert launch["execution"]["context"] == "host"
    assert attempt["identity"] == executor
    assert (campaign / "campaign.json").read_bytes() == manifest_before
    assert (campaign / "start.json").read_bytes() == start_before


def test_early_worker_does_not_replace_submitter(tmp_path, monkeypatch):
    campaign = campaigns.create_campaign(make_spec(tmp_path), tmp_path / "campaigns")
    submitter = fake_identity("submitter", 101)
    executor = fake_identity("worker", 102)
    monkeypatch.setattr(campaigns, "_account_identity", lambda: submitter)
    campaigns.prepare_campaign_start(campaign)
    monkeypatch.setattr(worker, "_account_identity", lambda: executor)
    assert worker.run_task(campaign, "one") == 0
    campaigns.begin_campaign(campaign)
    assert read(campaign / "start.json")["identity"] == submitter
    assert read(campaign / "one_attempt_001/provenance.json")["execution"]["identity"] == executor


def test_direct_begin_records_current_account(tmp_path, monkeypatch):
    campaign = campaigns.create_campaign(make_spec(tmp_path), tmp_path / "campaigns")
    identity = fake_identity("starter", 110)
    monkeypatch.setattr(campaigns, "_account_identity", lambda: identity)
    campaigns.begin_campaign(campaign)
    assert read(campaign / "start.json")["identity"] == identity


def test_cancelled_submission_does_not_leak_previous_identity(tmp_path, monkeypatch):
    campaign = campaigns.create_campaign(make_spec(tmp_path), tmp_path / "campaigns")
    monkeypatch.setattr(campaigns, "_account_identity", lambda: fake_identity("first", 1))
    campaigns.prepare_campaign_start(campaign)
    campaigns.cancel_prepared_start(campaign)
    second = fake_identity("second", 2)
    monkeypatch.setattr(campaigns, "_account_identity", lambda: second)
    campaigns.prepare_campaign_start(campaign)
    campaigns.begin_campaign(campaign)
    assert read(campaign / "start.json")["identity"] == second


def test_legacy_metadata_is_not_backfilled_from_current_user(tmp_path):
    campaign = campaigns.create_campaign(make_spec(tmp_path), tmp_path / "campaigns")
    manifest = read(campaign / "campaign.json")
    manifest["creation"].pop("identity")
    (campaign / "campaign.json").write_text(json.dumps(manifest))
    before = (campaign / "campaign.json").read_bytes()
    (campaign / "state/start-pending.json").write_text('{"overwrite": false}')
    campaigns.begin_campaign(campaign)
    assert read(campaign / "start.json")["identity"] is None
    assert worker.run_task(campaign, "one") == 0
    assert (campaign / "campaign.json").read_bytes() == before
    assert "identity" in read(campaign / "one_attempt_001/provenance.json")["execution"]


@pytest.mark.parametrize("failure", ["missing_inputs", "outputs_exist", "launch_failed", "command_failed", "missing_outputs"])
def test_failed_attempts_keep_execution_identity(tmp_path, monkeypatch, failure):
    declarations = ""
    command = None
    if failure == "missing_inputs":
        declarations = "    @input data absent.dat\n"
    elif failure in {"outputs_exist", "missing_outputs"}:
        declarations = "    @output result result.dat\n"
        if failure == "outputs_exist":
            (tmp_path / "result.dat").write_text("existing")
    elif failure == "launch_failed":
        command = [str(tmp_path / "missing-executable")]
    elif failure == "command_failed":
        command = [sys.executable, "-c", "raise SystemExit(7)"]
    campaign = campaigns.create_campaign(
        make_spec(tmp_path, declarations=declarations, command=command), tmp_path / "campaigns"
    )
    identity = fake_identity("executor", 501)
    monkeypatch.setattr(worker, "_account_identity", lambda: identity)
    assert worker.run_task(campaign, "one") != 0
    assert read(campaign / "one_attempt_001/provenance.json")["execution"]["identity"] == identity
    attempt = read(campaign / "one_attempt_001/attempt.json")
    assert attempt["identity"] == identity
    assert attempt["failure"]["kind"] == failure


def test_retries_capture_new_account_without_rewriting_history(tmp_path, monkeypatch):
    campaign = campaigns.create_campaign(make_spec(tmp_path), tmp_path / "campaigns")
    first = fake_identity("first-worker", 201)
    second = fake_identity("second-worker", 202)
    monkeypatch.setattr(worker, "_account_identity", lambda: first)
    assert worker.run_task(campaign, "one") == 0
    first_path = campaign / "one_attempt_001/provenance.json"
    first_bytes = first_path.read_bytes()
    monkeypatch.setattr(worker, "_account_identity", lambda: second)
    assert worker.run_task(campaign, "one") == 0
    assert first_path.read_bytes() == first_bytes
    assert read(first_path)["execution"]["identity"] == first
    assert read(campaign / "one_attempt_002/provenance.json")["execution"]["identity"] == second


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="compares POSIX account with spoofed environment")
@pytest.mark.parametrize("backend,render", [
    ("condor", render_condor), ("slurm", render_slurm), ("pbs", render_pbs),
])
def test_bundled_worker_collects_account_without_package(tmp_path, backend, render):
    campaign = render(make_spec(tmp_path), tmp_path / "campaigns")
    bundled = campaign / backend / "yall_worker.py"
    environment = os.environ.copy()
    environment["USER"] = "not-the-os-user"
    environment["LOGNAME"] = "not-the-os-user"
    result = subprocess.run(
        [sys.executable, "-I", str(bundled), str(campaign), "one"],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    identity = read(campaign / "one_attempt_001/provenance.json")["execution"]["identity"]
    assert identity == worker._account_identity()
    assert read(campaign / "one_attempt_001/attempt.json")["identity"] == identity


def test_local_start_records_all_three_identities(tmp_path):
    campaign = campaigns.create_campaign(make_spec(tmp_path), tmp_path / "campaigns")
    campaigns.start_local(campaign)
    expected = worker._account_identity()
    assert read(campaign / "campaign.json")["creation"]["identity"] == expected
    assert read(campaign / "start.json")["identity"] == expected
    assert read(campaign / "one_attempt_001/provenance.json")["execution"]["identity"] == expected


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX account-database failure")
def test_identity_lookup_failure_does_not_prevent_execution(tmp_path, monkeypatch):
    def lookup(uid):
        raise KeyError("account not mapped on this worker")
    monkeypatch.setitem(sys.modules, "pwd", SimpleNamespace(getpwuid=lookup))
    campaign = campaigns.create_campaign(make_spec(tmp_path), tmp_path / "campaigns")
    campaigns.start_local(campaign)
    attempt = read(campaign / "one_attempt_001/attempt.json")
    assert attempt["state"] == "completed"
    assert attempt["identity"]["username"] is None
    assert "username" in attempt["identity"]["errors"]


def test_package_version_metadata_matches():
    project = Path(__file__).resolve().parents[1] / "pyproject.toml"
    assert 'version = "' + __version__ + '"' in project.read_text()
