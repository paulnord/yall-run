import io
import json
from pathlib import Path
import sys

from yall_run.cli import main
from yall_run.campaign import campaign_status


def test_cli_create_then_start_one_campaign(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "Yallfile").write_text(
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
    assert str(campaign_dir) not in output
    assert output.rstrip().endswith(
        "[local] finished completed=1 failed=0 blocked=0"
    )
    assert campaign_status(campaign_dir)["counts"] == {"completed": 1}

    assert main(["start", str(campaign_dir)]) == 2
    assert "already been started" in capsys.readouterr().err


def test_cli_start_reads_one_campaign_path_from_stdin(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "Yallfile").write_text(
        "campaign piped-start\n"
        "backend local\n\n"
        "hello:\n"
        "    echo hello\n"
    )

    assert main(["create", "--campaigns-dir", "campaigns"]) == 0
    campaign_dir = Path(capsys.readouterr().out.strip())
    monkeypatch.setattr(sys, "stdin", io.StringIO(str(campaign_dir) + "\n"))

    assert main(["start"]) == 0
    output = capsys.readouterr().out
    assert "[start] hello" in output
    assert "[done ] hello" in output
    assert str(campaign_dir) not in output
    assert output.rstrip().endswith(
        "[local] finished completed=1 failed=0 blocked=0"
    )
    assert campaign_status(campaign_dir)["counts"] == {"completed": 1}


def test_cli_start_rejects_multiple_stdin_paths(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("campaign-one\ncampaign-two\n"))
    assert main(["start"]) == 2
    assert "exactly one campaign path" in capsys.readouterr().err


def test_cli_plan_json_emits_expanded_machine_readable_plan(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "Yallfile").write_text(
        "campaign structured-plan\n"
        "backend local\n\n"
        "prepare:\n"
        "    @output data prepared.dat\n"
        "    echo prepare\n\n"
        "analyze: prepare\n"
        "    @input data prepared.dat\n"
        "    @output result result.dat\n"
        "    %retry 2\n"
        "    %cpus 4\n"
        "    %overwrite\n"
        "    echo analyze\n"
    )

    assert main(["plan", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["name"] == "structured-plan"
    assert data["backend"] == "local"
    assert data["source"] == str(tmp_path / "Yallfile")
    assert [task["name"] for task in data["tasks"]] == ["prepare", "analyze"]
    analyze = data["tasks"][1]
    assert analyze["parents"] == ["prepare"]
    assert analyze["retries"] == 2
    assert analyze["overwrite"] is True
    assert analyze["resources"]["cpus"] == 4
    assert analyze["inputs"] == [{"role": "data", "path": "prepared.dat"}]
    assert analyze["outputs"] == [{"role": "result", "path": "result.dat"}]
    assert analyze["command"] == ["echo", "analyze"]


def test_cli_plan_dot_emits_expanded_dependency_graph(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "Yallfile").write_text(
        "campaign dot-plan\n\n"
        "left:\n"
        "    echo left\n\n"
        "right:\n"
        "    echo right\n\n"
        "finish: left right\n"
        "    echo finish\n"
    )

    assert main(["plan", "--dot"]) == 0
    output = capsys.readouterr().out
    assert output.startswith("digraph yall {\n")
    assert '  "left";' in output
    assert '  "right";' in output
    assert '  "finish";' in output
    assert '  "left" -> "finish";' in output
    assert '  "right" -> "finish";' in output


def test_cli_rejects_j_for_condor_create(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "Yallfile").write_text(
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
    (tmp_path / "Yallfile").write_text(
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
    (tmp_path / "Yallfile").write_text(
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
