"""Frozen, opt-in account recording; off never queries account identifiers."""

from dataclasses import replace
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from types import SimpleNamespace

import pytest

from yall_run import campaign, worker
from yall_run.amend import _validate_wrapper
from yall_run.cli import main
from yall_run.condor_backend import render_condor
from yall_run.model import load_spec
from yall_run.pbs_backend import render_pbs
from yall_run.postflight_runner import write_runner
from yall_run.slurm_backend import render_slurm


OMITTED = {"recorded": False, "reason": "disabled_by_policy"}
RENDERERS = {"condor": render_condor, "slurm": render_slurm, "pbs": render_pbs}


def read(path):
    return json.loads(path.read_text())


def recipe(tmp_path, mode=None, command="/usr/bin/true", hooks=False):
    path = tmp_path / "Yallfile"
    policy = "" if mode is None else "%account-provenance " + mode + "\n"
    lifecycle = "%preflight /usr/bin/true\n%postflight /usr/bin/true\n" if hooks else ""
    path.write_text("campaign policy-test\nbackend local\n" + policy + lifecycle
                    + "\none:\n    " + command + "\n")
    return load_spec(path)


class NoAccounts:
    """Delegate normal OS calls but fail any attempted identity lookup."""
    def __getattr__(self, name):
        if name in ("getuid", "getgid", "geteuid", "getegid", "getlogin"):
            raise AssertionError("account lookup must not happen: " + name)
        return getattr(os, name)


def forbid_accounts(monkeypatch):
    monkeypatch.setattr(worker, "os", NoAccounts())
    def forbidden(uid):
        raise AssertionError("pwd lookup must not happen")
    monkeypatch.setitem(sys.modules, "pwd", SimpleNamespace(getpwuid=forbidden))


@pytest.mark.parametrize("mode,expected", [(None, "off"), ("off", "off"), ("full", "full")])
def test_parse_policy_and_freeze(tmp_path, mode, expected):
    spec = recipe(tmp_path, mode)
    assert spec.account_provenance == expected
    directory = campaign.create_campaign(spec, tmp_path / "campaigns")
    manifest = read(directory / "campaign.json")
    assert manifest["provenance_policy"]["accounts"] == expected
    assert manifest["provenance_policy"]["recipe_accounts"] == expected
    if expected == "off":
        assert manifest["creation"]["user"] == OMITTED
    else:
        assert manifest["creation"]["user"] == worker.user_identity("full")


@pytest.mark.parametrize("mode", ["", "yes", "false", "FULL", "full off", "{mode}"])
def test_bad_directive_is_rejected_before_preflight(tmp_path, mode):
    marker = tmp_path / "should-not-exist"
    path = tmp_path / "Yallfile"
    path.write_text("campaign invalid\n%preflight touch " + str(marker)
                    + "\n%account-provenance " + mode + "\none:\n    /usr/bin/true\n")
    with pytest.raises(ValueError, match="account-provenance"):
        load_spec(path)
    assert not marker.exists()


@pytest.mark.parametrize("text", [
    "campaign bad\n%account-provenance off\n%account-provenance full\none:\n    /usr/bin/true\n",
    "campaign bad\none:\n    /usr/bin/true\n%account-provenance full\n",
    "campaign bad\none:\n    %account-provenance full\n    /usr/bin/true\n",
])
def test_policy_must_be_single_campaign_directive_before_tasks(tmp_path, text):
    path = tmp_path / "Yallfile"
    path.write_text(text)
    with pytest.raises(ValueError, match="account-provenance"):
        load_spec(path)


@pytest.mark.parametrize("bad", ["bad", None, [], {}])
def test_invalid_programmatic_mode_is_rejected(tmp_path, bad):
    spec = recipe(tmp_path)
    with pytest.raises(ValueError, match="account provenance"):
        replace(spec, account_provenance=bad)


def test_off_helper_never_queries_accounts(monkeypatch):
    forbid_accounts(monkeypatch)
    assert worker.user_identity() == OMITTED
    assert worker.user_identity("off") == OMITTED
    with pytest.raises(AssertionError, match="lookup"):
        worker.user_identity("full")


@pytest.mark.parametrize("explicit", [False, True])
def test_off_create_start_hooks_repeat_and_resume_never_query_accounts(tmp_path, monkeypatch, explicit):
    spec = recipe(tmp_path, "off" if explicit else None, hooks=True)
    forbid_accounts(monkeypatch)
    directory = campaign.create_campaign(spec, tmp_path / "campaigns")
    assert read(directory / "preflight/001/result.json")["user"] == OMITTED
    campaign.start_local(directory)
    assert read(directory / "start.json")["user"] == OMITTED
    assert read(directory / "postflight/001/result.json")["user"] == OMITTED
    assert worker.run_task(directory, "one") == 0
    for path in directory.glob("*_attempt_*/provenance.json"):
        assert read(path)["execution"]["user"] == OMITTED
    original = (directory / "start.json").read_bytes()
    campaign.resume_local(directory, reason="policy test")
    assert read(directory / "resumes/resume_001.json")["user"] == OMITTED
    assert (directory / "start.json").read_bytes() == original


def test_off_failure_retry_resume_never_queries_accounts(tmp_path, monkeypatch):
    spec = recipe(tmp_path, "off", command="/usr/bin/false")
    forbid_accounts(monkeypatch)
    directory = campaign.create_campaign(spec, tmp_path / "campaigns")
    with pytest.raises(RuntimeError, match="failed"):
        campaign.start_local(directory)
    assert campaign.retry_task(directory, "one") != 0
    with pytest.raises(RuntimeError, match="failed"):
        campaign.resume_local(directory)
    assert len(list(directory.glob("*_attempt_*/provenance.json"))) == 3
    for path in directory.glob("*_attempt_*/provenance.json"):
        assert read(path)["execution"]["user"] == OMITTED


@pytest.mark.parametrize("recipe_mode,override", [("off", "full"), ("full", "off")])
def test_cli_override_is_frozen_and_stdout_is_only_campaign_path(tmp_path, monkeypatch, capsys, recipe_mode, override):
    spec = recipe(tmp_path, recipe_mode)
    monkeypatch.chdir(tmp_path)
    assert main(["create", str(spec.source), "--account-provenance", override]) == 0
    capture = capsys.readouterr()
    assert "Account provenance: " + override in capture.err
    assert len(capture.out.strip().splitlines()) == 1
    directory = Path(capture.out.strip())
    manifest = read(directory / "campaign.json")
    assert manifest["provenance_policy"] == {"accounts": override, "recipe_accounts": recipe_mode}
    assert (directory / "Yallfile").read_bytes() == spec.source.read_bytes()
    monkeypatch.setenv("YALL_ACCOUNT_PROVENANCE", recipe_mode)
    spec.source.write_text("not a workflow anymore\n")
    assert main(["start", str(directory)]) == 0
    assert "Account provenance: " + override in capsys.readouterr().err
    actual = read(directory / "one_attempt_001/provenance.json")["execution"]["user"]
    assert actual == (OMITTED if override == "off" else worker.user_identity("full"))


def test_plan_text_and_json_show_policy_without_corrupting_dot(tmp_path, capsys):
    spec = recipe(tmp_path, "full")
    assert main(["plan", str(spec.source)]) == 0
    assert "Account provenance: full" in capsys.readouterr().out
    assert main(["plan", str(spec.source), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["provenance_policy"]["accounts"] == "full"
    assert main(["plan", str(spec.source), "--dot"]) == 0
    output = capsys.readouterr().out
    assert output.startswith("digraph yall {")
    assert "Account provenance:" not in output


def test_cli_bad_mode_creates_nothing(tmp_path, monkeypatch):
    spec = recipe(tmp_path, hooks=True)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        main(["create", str(spec.source), "--account-provenance", "bad"])
    assert not (tmp_path / "campaigns").exists()


@pytest.mark.parametrize("command", ["start", "resume", "retry"])
def test_cli_has_no_late_policy_override(command):
    with pytest.raises(SystemExit):
        main([command, "not-a-campaign", "--account-provenance", "full"])


@pytest.mark.parametrize("policy", [None, [], "full", {"accounts": "bad"}, {"accounts": None}, {"accounts": []}])
def test_invalid_frozen_policy_fails_before_attempt(tmp_path, policy):
    directory = campaign.create_campaign(recipe(tmp_path), tmp_path / "campaigns")
    manifest = read(directory / "campaign.json")
    manifest["provenance_policy"] = policy
    (directory / "campaign.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="account provenance"):
        worker.run_task(directory, "one")
    with pytest.raises(ValueError, match="account provenance"):
        campaign.prepare_campaign_start(directory)
    assert not list(directory.glob("*_attempt_*"))
    assert not (directory / "state/start-pending.json").exists()


def test_legacy_campaign_defaults_off_without_rewriting_history(tmp_path, monkeypatch):
    directory = campaign.create_campaign(recipe(tmp_path, "full"), tmp_path / "campaigns")
    manifest = read(directory / "campaign.json")
    manifest.pop("provenance_policy")
    (directory / "campaign.json").write_text(json.dumps(manifest))
    original = (directory / "campaign.json").read_bytes()
    forbid_accounts(monkeypatch)
    campaign.start_local(directory)
    assert read(directory / "start.json")["user"] == OMITTED
    assert read(directory / "one_attempt_001/provenance.json")["execution"]["user"] == OMITTED
    assert (directory / "campaign.json").read_bytes() == original


def test_off_does_not_copy_old_pending_identity_to_new_start(tmp_path, monkeypatch):
    directory = campaign.create_campaign(recipe(tmp_path), tmp_path / "campaigns")
    (directory / "state/start-pending.json").write_text(json.dumps({"user": {"username": "old-account"}}))
    forbid_accounts(monkeypatch)
    campaign.begin_campaign(directory)
    assert read(directory / "start.json")["user"] == OMITTED


def test_pending_policy_cannot_change_during_submission(tmp_path):
    directory = campaign.create_campaign(recipe(tmp_path), tmp_path / "campaigns")
    campaign.prepare_campaign_start(directory)
    manifest = read(directory / "campaign.json")
    manifest["provenance_policy"]["accounts"] = "full"
    (directory / "campaign.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="policy changed"):
        campaign.begin_campaign(directory)
    assert not (directory / "start.json").exists()


def isolated_run(script, args, forbid=False):
    code = "import os, runpy, sys\n"
    if forbid:
        code += ("def forbidden(): raise AssertionError('account lookup in off runner')\n"
                 "for n in ('getuid','getgid','geteuid','getegid','getlogin'): setattr(os,n,forbidden)\n")
    code += "sys.argv = " + repr([str(script)] + [str(a) for a in args]) + "\n"
    code += "runpy.run_path(sys.argv[0], run_name='__main__')\n"
    return subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("backend", RENDERERS)
@pytest.mark.parametrize("mode", ["off", "full"])
def test_bundled_worker_policy_without_installed_package(tmp_path, backend, mode):
    directory = RENDERERS[backend](recipe(tmp_path, mode), tmp_path / "campaigns")
    script = directory / backend / "yall_worker.py"
    result = isolated_run(script, [directory, "one"], forbid=mode == "off")
    assert result.returncode == 0, result.stderr
    expected = OMITTED if mode == "off" else worker.user_identity("full")
    assert read(directory / "one_attempt_001/provenance.json")["execution"]["user"] == expected


@pytest.mark.parametrize("mode", ["off", "full"])
def test_standalone_postflight_uses_frozen_policy(tmp_path, mode):
    directory = campaign.create_campaign(recipe(tmp_path, mode), tmp_path / "campaigns")
    script = directory / "postflight-runner.py"
    write_runner(script, campaign_dir=directory, workflow_dir=tmp_path, commands=[["/usr/bin/true"]])
    result = isolated_run(script, [], forbid=mode == "off")
    assert result.returncode == 0, result.stderr
    expected = OMITTED if mode == "off" else worker.user_identity("full")
    assert read(directory / "postflight/001/result.json")["user"] == expected


def test_amend_rejects_policy_changes_but_preserves_cli_override(tmp_path):
    spec = recipe(tmp_path, "off")
    overridden = replace(spec, account_provenance="full", account_provenance_recipe="off")
    directory = campaign.create_campaign(overridden, tmp_path / "campaigns")
    manifest = read(directory / "campaign.json")
    _validate_wrapper(spec, manifest)
    with pytest.raises(ValueError, match="frozen account provenance"):
        _validate_wrapper(replace(spec, account_provenance="full"), manifest)
