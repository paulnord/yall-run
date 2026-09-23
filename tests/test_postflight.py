import json
from pathlib import Path

from yall_run.campaign import create_campaign, start_local
from yall_run.model import load_spec


def test_postflight_runs_after_local_graph_and_records_campaign_environment(tmp_path, capsys):
    source = tmp_path / "Yallfile"
    source.write_text(
        "campaign postflight-test\n"
        "%postflight ! printf '%s\\n' \"$YALL_CAMPAIGN_ID $YALL_WORKFLOW_DIR\" > postflight-id.txt\n"
        "work:\n"
        "    touch work.done\n"
    )
    campaign = create_campaign(load_spec(source), tmp_path / "campaigns")
    start_local(campaign)
    capsys.readouterr()
    assert (campaign / "postflight-id.txt").read_text() == (
        campaign.name + " " + str(source.parent) + "\n"
    )
    record = json.loads((campaign / "postflight.json").read_text())
    assert record["state"] == "completed"
    assert record["commands"][0]["state"] == "completed"
    assert (campaign / "postflight" / "001" / "result.json").exists()


def test_postflight_requires_completed_tasks(tmp_path):
    source = tmp_path / "Yallfile"
    source.write_text(
        "campaign postflight-test\n"
        "%postflight echo done\n"
        "work:\n"
        "    echo work\n"
    )
    campaign = create_campaign(load_spec(source), tmp_path / "campaigns")
    assert not (campaign / "postflight.json").exists()
