import io
import json
from pathlib import Path
import shlex
import sys

import pytest

from yall_run.campaign import (
    begin_campaign,
    create_campaign,
    prepare_campaign_start,
    start_local,
)
from yall_run.cli import main as cli_main
from yall_run.model import load_spec
from yall_run.worker import run_task


def _single_output_workflow(tmp_path: Path, *, overwrite: bool = False) -> Path:
    script = tmp_path / "write.py"
    script.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "Path(sys.argv[1]).write_text('new\\n')\n"
    )
    overwrite_line = "    %overwrite\n" if overwrite else ""
    yallfile = tmp_path / "Yallfile"
    yallfile.write_text(
        "campaign overwrite-test\n"
        "backend local\n\n"
        "write:\n"
        "    @output result result.txt\n"
        + overwrite_line
        + f"    {shlex.quote(sys.executable)} {shlex.quote(str(script))} @output.result\n"
    )
    return yallfile


def test_existing_declared_output_blocks_campaign_before_start(tmp_path):
    yallfile = _single_output_workflow(tmp_path)
    result = tmp_path / "result.txt"
    result.write_text("old\n")
    campaign_dir = create_campaign(
        load_spec(yallfile), tmp_path / "campaigns", backend="local"
    )

    with pytest.raises(ValueError, match="declared outputs already exist"):
        start_local(campaign_dir)

    assert result.read_text() == "old\n"
    assert not (campaign_dir / "start.json").exists()
    assert not (campaign_dir / "state" / "start-pending.json").exists()
    assert not (campaign_dir / "write_attempt_001").exists()


def test_failed_preflight_can_be_retried_with_start_overwrite(tmp_path):
    yallfile = _single_output_workflow(tmp_path)
    result = tmp_path / "result.txt"
    result.write_text("old\n")
    campaign_dir = create_campaign(
        load_spec(yallfile), tmp_path / "campaigns", backend="local"
    )

    with pytest.raises(ValueError, match="start --overwrite"):
        start_local(campaign_dir)

    start_local(campaign_dir, overwrite=True)
    assert result.read_text() == "new\n"
    start = json.loads((campaign_dir / "start.json").read_text())
    assert start["overwrite"] is True


def test_worker_blocks_output_that_appears_after_campaign_start(tmp_path):
    yallfile = _single_output_workflow(tmp_path)
    result = tmp_path / "result.txt"
    campaign_dir = create_campaign(
        load_spec(yallfile), tmp_path / "campaigns", backend="local"
    )

    prepare_campaign_start(campaign_dir)
    begin_campaign(campaign_dir)
    result.write_text("late\n")

    assert run_task(campaign_dir, "write") == 2
    attempt = json.loads(
        (campaign_dir / "write_attempt_001" / "attempt.json").read_text()
    )
    assert attempt["command_returncode"] is None
    assert attempt["failure"] == {
        "kind": "outputs_exist",
        "paths": [str(result)],
    }
    assert result.read_text() == "late\n"


def test_task_overwrite_allows_existing_output(tmp_path):
    yallfile = _single_output_workflow(tmp_path, overwrite=True)
    result = tmp_path / "result.txt"
    result.write_text("old\n")
    spec = load_spec(yallfile)
    assert spec.tasks[0].overwrite is True

    campaign_dir = create_campaign(spec, tmp_path / "campaigns", backend="local")
    start_local(campaign_dir)

    assert result.read_text() == "new\n"
    manifest = json.loads((campaign_dir / "campaign.json").read_text())
    assert manifest["tasks"]["write"]["overwrite"] is True


def test_start_overwrite_allows_all_existing_outputs_and_is_recorded(tmp_path):
    yallfile = _single_output_workflow(tmp_path)
    result = tmp_path / "result.txt"
    result.write_text("old\n")
    campaign_dir = create_campaign(
        load_spec(yallfile), tmp_path / "campaigns", backend="local"
    )

    assert cli_main(["start", "--overwrite", str(campaign_dir)]) == 0
    assert result.read_text() == "new\n"
    start = json.loads((campaign_dir / "start.json").read_text())
    assert start["overwrite"] is True


def test_start_overwrite_accepts_campaign_path_from_stdin(tmp_path, monkeypatch):
    yallfile = _single_output_workflow(tmp_path)
    result = tmp_path / "result.txt"
    result.write_text("old\n")
    campaign_dir = create_campaign(
        load_spec(yallfile), tmp_path / "campaigns", backend="local"
    )

    monkeypatch.setattr(sys, "stdin", io.StringIO(f"{campaign_dir}\n"))
    assert cli_main(["start", "--overwrite"]) == 0

    assert result.read_text() == "new\n"
    start = json.loads((campaign_dir / "start.json").read_text())
    assert start["overwrite"] is True


def test_existing_declared_directory_is_protected_at_start(tmp_path):
    output_dir = tmp_path / "plots"
    output_dir.mkdir()
    marker = tmp_path / "ran.txt"
    yallfile = tmp_path / "Yallfile"
    yallfile.write_text(
        "campaign directory-output\n"
        "backend local\n\n"
        "plot:\n"
        "    @output plots plots\n"
        f"    {shlex.quote(sys.executable)} -c "
        + shlex.quote(
            f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')"
        )
        + "\n"
    )
    campaign_dir = create_campaign(
        load_spec(yallfile), tmp_path / "campaigns", backend="local"
    )

    with pytest.raises(ValueError, match="declared outputs already exist"):
        start_local(campaign_dir)
    assert not marker.exists()
    assert not (campaign_dir / "start.json").exists()


def test_two_tasks_cannot_own_same_expanded_output(tmp_path):
    yallfile = tmp_path / "Yallfile"
    yallfile.write_text(
        "campaign duplicate-output\n\n"
        "left:\n"
        "    @output result same.dat\n"
        "    echo left\n\n"
        "right:\n"
        "    @output result same.dat\n"
        "    echo right\n"
    )

    with pytest.raises(ValueError, match="owned by both 'left' and 'right'"):
        load_spec(yallfile)


def test_same_relative_output_in_different_task_cwds_is_allowed(tmp_path):
    (tmp_path / "left").mkdir()
    (tmp_path / "right").mkdir()
    yallfile = tmp_path / "Yallfile"
    yallfile.write_text(
        "campaign distinct-output\n\n"
        "left:\n"
        "    %cwd left\n"
        "    @output result same.dat\n"
        "    echo left\n\n"
        "right:\n"
        "    %cwd right\n"
        "    @output result same.dat\n"
        "    echo right\n"
    )

    spec = load_spec(yallfile)
    assert len(spec.tasks) == 2


def test_prepared_start_carries_overwrite_to_early_worker(tmp_path):
    yallfile = _single_output_workflow(tmp_path)
    result = tmp_path / "result.txt"
    result.write_text("old\n")
    campaign_dir = create_campaign(
        load_spec(yallfile), tmp_path / "campaigns", backend="local"
    )

    prepare_campaign_start(campaign_dir, overwrite=True)
    assert run_task(campaign_dir, "write") == 0
    begin_campaign(campaign_dir)

    assert result.read_text() == "new\n"
    start = json.loads((campaign_dir / "start.json").read_text())
    assert start["overwrite"] is True
    assert not (campaign_dir / "state" / "start-pending.json").exists()
