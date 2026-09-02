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

    assert main(["create", "--campaigns-dir", "campaigns", "-j", "2"]) == 0
    campaign_dir = Path(capsys.readouterr().out.strip())
    assert campaign_dir.is_dir()
    assert campaign_status(campaign_dir)["counts"] == {"pending": 1}
    assert not list(campaign_dir.glob("*_attempt_*"))

    assert main(["start", str(campaign_dir)]) == 0
    output = capsys.readouterr().out
    assert "jobs=2" in output
    assert "[start] hello" in output
    assert "[done ] hello" in output
    assert campaign_status(campaign_dir)["counts"] == {"completed": 1}

    assert main(["start", str(campaign_dir)]) == 2
    assert "already been started" in capsys.readouterr().err


def test_cli_rejects_j_for_condor_create(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "Yawlfile").write_text(
        "campaign condor-j\n"
        "backend condor\n\n"
        "hello:\n"
        "    echo hello\n"
    )

    assert main(["create", "--campaigns-dir", "campaigns", "-j", "4"]) == 2
    assert "only valid for the local backend" in capsys.readouterr().err
    assert not (tmp_path / "campaigns").exists()


def test_cli_local_failure_returns_error_status(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "Yawlfile").write_text(
        "campaign failing\n"
        "backend local\n\n"
        "bad:\n"
        "    ! exit 7\n"
    )

    assert main(["create", "--campaigns-dir", "campaigns"]) == 0
    campaign_dir = Path(capsys.readouterr().out.strip())
    assert main(["start", str(campaign_dir)]) == 2
    captured = capsys.readouterr()
    assert "[FAIL ] bad" in captured.out
    assert "failed=1" in captured.out
    assert "local campaign failed" in captured.err


def test_cli_rejects_retired_root_option(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "Yawlfile").write_text(
        "campaign no-root-option\n"
        "backend local\n\n"
        "hello:\n"
        "    echo hello\n"
    )

    try:
        main(["create", "--root", "campaigns"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("retired --root option should be rejected by argparse")
