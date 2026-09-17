"""Condor queue-response edge cases observed on production pools."""
import json

from yall_run import recovery as r


def test_empty_successful_condor_query_is_an_empty_queue(tmp_path, monkeypatch):
    campaign = tmp_path / "campaign"
    condor = campaign / "condor"
    condor.mkdir(parents=True)

    (campaign / "campaign.json").write_text(json.dumps({
        "id": "empty-condor-query",
        "backend": "condor",
        "task_order": ["task"],
        "tasks": {"task": {}},
    }))
    (condor / "submit.json").write_text(json.dumps({
        "cluster_id": 11899,
        "returncode": 0,
    }))
    (condor / "render.json").write_text(json.dumps({
        "node_names": {"task": "yall_0000_task"},
    }))

    # BNL SDCC was observed returning status 0 with empty stdout after the DAG
    # had left the queue. A successful empty result means no matching ads.
    monkeypatch.setattr(r, "_checked", lambda argv, cwd=None: "")

    snapshot = r.scheduler_snapshot(campaign)

    assert snapshot["query_ok"] is True
    assert snapshot["cluster_id"] == 11899
    assert snapshot["dagman"] is None
    assert snapshot["nodes"] == {}
    assert snapshot["active_jobs"] == {}
    assert snapshot["counts"] == {}
