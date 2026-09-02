from pathlib import Path

from yawl_run.cli import main
from yawl_run.campaign import campaign_status


def test_cli_create_then_start_one_campaign(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "Yawlfile").write_text(
        "campaign lifecycle\n"
        "backend local\n\n"
        "hello:\n"
        "    echo hello\n"
    )

    assert main(["create", "--root", "campaigns"]) == 0
    campaign_dir = Path(capsys.readouterr().out.strip())
    assert campaign_dir.is_dir()
    assert campaign_status(campaign_dir)["counts"] == {"pending": 1}
    assert not list(campaign_dir.glob("*_attempt_*"))

    assert main(["start", str(campaign_dir), "-j", "2"]) == 0
    capsys.readouterr()
    assert campaign_status(campaign_dir)["counts"] == {"completed": 1}

    assert main(["start", str(campaign_dir)]) == 2
    assert "already been started" in capsys.readouterr().err
