from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import sqlite3

from yall_run.cli import main
from yall_run.export import export_provenance


def _write_failing(path: Path, *, cpus: int | None = None) -> None:
    resource = f"    %cpus {cpus}\n" if cpus is not None else ""
    path.write_text(
        "campaign amend-test\n"
        "backend local\n\n"
        "work:\n"
        "    @output result result.txt\n"
        + resource
        + "    ! exit 7\n"
    )


def _create_failed_campaign(tmp_path: Path, capsys) -> tuple[Path, Path]:
    yallfile = tmp_path / "Yallfile"
    _write_failing(yallfile)
    assert main(["create", "--campaigns-dir", "campaigns"]) == 0
    campaign_dir = Path(capsys.readouterr().out.strip())
    assert main(["start", str(campaign_dir)]) == 2
    capsys.readouterr()
    return yallfile, campaign_dir


def test_amend_discovers_command_change_and_preserves_campaign_json(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    yallfile, campaign_dir = _create_failed_campaign(tmp_path, capsys)
    frozen_manifest = (campaign_dir / "campaign.json").read_bytes()

    yallfile.write_text(
        "campaign amend-test\n"
        "backend local\n\n"
        "work:\n"
        "    @output result result.txt\n"
        "    ! printf fixed > @output.result\n"
    )

    assert main([
        "amend",
        str(campaign_dir),
        "--reason",
        "repair failed command",
        "--yes",
    ]) == 0
    output = capsys.readouterr().out
    assert "amendment 0001" in output
    assert "work" in output
    assert "failed" in output

    assert (campaign_dir / "campaign.json").read_bytes() == frozen_manifest
    amendment_dir = campaign_dir / "amendments" / "0001"
    assert (amendment_dir / "Yallfile").read_text() == yallfile.read_text()
    amendment = json.loads((amendment_dir / "amendment.json").read_text())
    assert amendment["reason"] == "repair failed command"
    assert amendment["changes"][0]["task"] == "work"
    assert amendment["changes"][0]["before"] == "exit 7"
    assert amendment["changes"][0]["after"] == "printf fixed > result.txt"

    assert main(["retry", str(campaign_dir), "work"]) == 0
    assert (tmp_path / "result.txt").read_text() == "fixed"
    provenance = json.loads(
        (campaign_dir / "work_attempt_002" / "provenance.json").read_text()
    )
    assert provenance["task"]["command"] == amendment["changes"][0]["after"]
    assert provenance["task"]["amendments"][0]["number"] == 1
    assert provenance["task"]["amendments"][0]["sha256"]


def test_amend_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    yallfile, campaign_dir = _create_failed_campaign(tmp_path, capsys)
    yallfile.write_text(
        "campaign amend-test\n"
        "backend local\n\n"
        "work:\n"
        "    @output result result.txt\n"
        "    ! printf fixed > @output.result\n"
    )

    assert main(["amend", str(campaign_dir), "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "dry run" in output
    assert not (campaign_dir / "amendments").exists()


def test_amend_rejects_change_to_completed_task(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    yallfile = tmp_path / "Yallfile"
    yallfile.write_text(
        "campaign amend-completed\n"
        "backend local\n\n"
        "work:\n"
        "    ! true\n"
    )
    assert main(["create", "--campaigns-dir", "campaigns"]) == 0
    campaign_dir = Path(capsys.readouterr().out.strip())
    assert main(["start", str(campaign_dir)]) == 0
    capsys.readouterr()

    yallfile.write_text(
        "campaign amend-completed\n"
        "backend local\n\n"
        "work:\n"
        "    ! echo changed\n"
    )
    assert main(["amend", str(campaign_dir)]) == 2
    assert "changes completed task" in capsys.readouterr().err
    assert not (campaign_dir / "amendments").exists()


def test_amend_rejects_noncommand_task_changes(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    yallfile, campaign_dir = _create_failed_campaign(tmp_path, capsys)
    _write_failing(yallfile, cpus=2)

    assert main(["amend", str(campaign_dir)]) == 2
    error = capsys.readouterr().err
    assert "unsupported field" in error
    assert "resources" in error
    assert not (campaign_dir / "amendments").exists()


def test_export_includes_amendment_and_attempt_reference(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    yallfile, campaign_dir = _create_failed_campaign(tmp_path, capsys)
    yallfile.write_text(
        "campaign amend-test\n"
        "backend local\n\n"
        "work:\n"
        "    @output result result.txt\n"
        "    ! printf fixed > @output.result\n"
    )
    assert main(["amend", str(campaign_dir), "--reason", "fix command", "--yes"]) == 0
    capsys.readouterr()
    assert main(["retry", str(campaign_dir), "work"]) == 0
    capsys.readouterr()

    sqlite_path = tmp_path / "amend.sqlite"
    _, counts = export_provenance([campaign_dir], sqlite_path=sqlite_path)
    assert counts["amendment"] == 1
    assert counts["amendment_change"] == 1

    with sqlite3.connect(sqlite_path) as db:
        assert db.execute(
            "SELECT amendment_number, reason FROM amendment"
        ).fetchone() == (1, "fix command")
        assert db.execute(
            "SELECT task_name, field_name FROM amendment_change"
        ).fetchone() == ("work", "command")
        amendments_json = db.execute(
            "SELECT amendments_json FROM attempt_provenance "
            "WHERE task_name = 'work' AND attempt = 2"
        ).fetchone()[0]
        assert json.loads(amendments_json)[0]["number"] == 1


def test_amend_uses_frozen_env_values_not_current_shell(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AMEND_SAMPLE", "frozen-value")
    yallfile = tmp_path / "Yallfile"
    yallfile.write_text(
        "campaign amend-env\n"
        "backend local\n"
        "@env AMEND_SAMPLE\n\n"
        "work:\n"
        "    ! exit 7 # {AMEND_SAMPLE}\n"
    )
    assert main(["create", "--campaigns-dir", "campaigns"]) == 0
    campaign_dir = Path(capsys.readouterr().out.strip())
    assert main(["start", str(campaign_dir)]) == 2
    capsys.readouterr()

    # Shell drift is not a workflow amendment. The comparison reuses the
    # creation-time value recorded by the campaign.
    monkeypatch.setenv("AMEND_SAMPLE", "different-live-value")
    assert main(["amend", str(campaign_dir), "--dry-run"]) == 0
    assert "no semantic changes" in capsys.readouterr().out

    # The frozen value also lets amend work if the variable is no longer set.
    monkeypatch.delenv("AMEND_SAMPLE")
    yallfile.write_text(
        "campaign amend-env\n"
        "backend local\n"
        "@env AMEND_SAMPLE\n\n"
        "work:\n"
        "    ! echo repaired-{AMEND_SAMPLE}\n"
    )
    assert main(["amend", str(campaign_dir), "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "repaired-frozen-value" in output
    assert "different-live-value" not in output

class _TTYInput(io.StringIO):
    def isatty(self):
        return True


def test_amend_interactive_confirmation_is_default(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    yallfile, campaign_dir = _create_failed_campaign(tmp_path, capsys)
    yallfile.write_text(
        "campaign amend-test\n"
        "backend local\n\n"
        "work:\n"
        "    @output result result.txt\n"
        "    ! printf fixed > @output.result\n"
    )
    monkeypatch.setattr(sys, "stdin", _TTYInput("y\n"))
    assert main(["amend", str(campaign_dir)]) == 0
    output = capsys.readouterr().out
    assert "proposed amendment 0001" in output
    assert "record amendment 0001? [y/N]" in output
    assert (campaign_dir / "amendments" / "0001" / "amendment.json").is_file()


def test_amend_noninteractive_requires_yes_or_dry_run(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    yallfile, campaign_dir = _create_failed_campaign(tmp_path, capsys)
    yallfile.write_text(
        "campaign amend-test\n"
        "backend local\n\n"
        "work:\n"
        "    @output result result.txt\n"
        "    ! printf fixed > @output.result\n"
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert main(["amend", str(campaign_dir)]) == 2
    error = capsys.readouterr().err
    assert "use --yes" in error
    assert not (campaign_dir / "amendments").exists()


def test_resume_points_to_amend_when_source_has_safe_change(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    yallfile, campaign_dir = _create_failed_campaign(tmp_path, capsys)
    yallfile.write_text(
        "campaign amend-test\n"
        "backend local\n\n"
        "work:\n"
        "    @output result result.txt\n"
        "    ! printf fixed > @output.result\n"
    )
    assert main(["resume", str(campaign_dir)]) == 2
    error = capsys.readouterr().err
    assert "run yall-run amend" in error
    assert "work" in error
