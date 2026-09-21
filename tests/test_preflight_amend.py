from __future__ import annotations

import json
from pathlib import Path

import pytest

from yall_run.cli import main


PREFLIGHT = [
    "%preflight ! printf first >> setup.log",
    "%preflight /usr/bin/touch second.marker",
]


def _write_spec(path: Path, preflight: list[str], command: str = "exit 7") -> None:
    path.write_text(
        "campaign amend-preflight\n"
        "backend local\n"
        + "\n".join(preflight)
        + "\nwork:\n"
        + f"    ! {command}\n"
    )


def _create_failed_campaign(tmp_path, capsys, preflight):
    source = tmp_path / "Yallfile"
    _write_spec(source, preflight)
    assert main(["create", str(source), "--campaigns-dir", "campaigns"]) == 0
    campaign = Path(capsys.readouterr().out.strip())
    assert main(["start", str(campaign)]) == 2
    capsys.readouterr()
    return source, campaign


def test_amend_preserves_preflight_and_does_not_rerun_it(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    source, campaign = _create_failed_campaign(tmp_path, capsys, PREFLIGHT)
    frozen_manifest = (campaign / "campaign.json").read_bytes()
    assert (tmp_path / "setup.log").read_text() == "first"
    (tmp_path / "second.marker").unlink()
    _write_spec(source, PREFLIGHT, "printf repaired")

    assert main(["amend", str(campaign), "--yes"]) == 0
    capsys.readouterr()
    assert (campaign / "campaign.json").read_bytes() == frozen_manifest
    amendment = json.loads(
        (campaign / "amendments" / "0001" / "amendment.json").read_text()
    )
    assert amendment["changes"][0]["after"] == "printf repaired"
    assert (tmp_path / "setup.log").read_text() == "first"
    assert not (tmp_path / "second.marker").exists()


@pytest.mark.parametrize(
    ("original", "revised"),
    [
        ([], PREFLIGHT),
        (PREFLIGHT, []),
        (PREFLIGHT, [PREFLIGHT[0].replace("first", "changed"), PREFLIGHT[1]]),
        (PREFLIGHT, [PREFLIGHT[0], PREFLIGHT[1].replace("second", "changed")]),
        (PREFLIGHT, list(reversed(PREFLIGHT))),
    ],
    ids=["added", "removed", "shell-changed", "argv-changed", "reordered"],
)
def test_amend_rejects_preflight_changes_before_writing(
    tmp_path, monkeypatch, capsys, original, revised
):
    monkeypatch.chdir(tmp_path)
    source, campaign = _create_failed_campaign(tmp_path, capsys, original)
    frozen_manifest = (campaign / "campaign.json").read_bytes()
    _write_spec(source, revised, "printf repaired")

    assert main(["amend", str(campaign), "--yes"]) == 2
    assert "changes frozen preflight" in capsys.readouterr().err
    assert not (campaign / "amendments").exists()
    assert (campaign / "campaign.json").read_bytes() == frozen_manifest


def test_amend_rejects_preflight_working_directory_change(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _, campaign = _create_failed_campaign(tmp_path, capsys, PREFLIGHT)
    other = tmp_path / "other"
    other.mkdir()
    source = other / "Yallfile"
    _write_spec(source, PREFLIGHT, "printf repaired")

    assert main(["amend", str(campaign), "--from", str(source), "--yes"]) == 2
    assert "changes frozen preflight commands or working directory" in capsys.readouterr().err
    assert not (campaign / "amendments").exists()
    assert not (other / "setup.log").exists()
